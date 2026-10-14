"""
Voice bridge — reuse the speech-to-speech STT / TTS handlers as a local HTTP
service for the DSH voice plugin.

Pipeline of the original backend (VAD -> STT -> LLM -> TTS) is NOT started:
this service instantiates only the STT and TTS handlers and exposes them over
HTTP, so the DSH agent itself plays the LLM role.

Buildout (see DSH-语音接入-设计方案.md):
  T1  skeleton + /api/health                          done
  T2  /api/stt   (WhisperSTTHandler, lazy load)       done
  T3  /api/tts   (Qwen3TTSHandler, lazy load)         done
  T8  /api/media/* lists + /media/* static mounts     <- current step

Run:
  D:\\speech-to-speech\\venv-speech\\Scripts\\python.exe -m uvicorn voice_bridge:app \
      --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from uuid import uuid4

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "bridge-config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("voice_bridge")

# 日志同时落盘：桥接平时跑在自己的控制台窗口里，窗口一关/没看着就无法回看，
# 「任务到底提交了没有、失败在哪一步」只能靠猜。这里额外挂一个轮转文件日志
# （logs/bridge.log, 4MB × 4）。写盘失败只告警，不影响服务。
try:
    from logging.handlers import RotatingFileHandler

    _log_dir = HERE / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _file_handler = RotatingFileHandler(
        _log_dir / "bridge.log", maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.getLogger().addHandler(_file_handler)
    logger.info("bridge log file -> %s", _log_dir / "bridge.log")
except Exception:  # noqa: BLE001
    logger.exception("bridge log file setup failed (console logging continues)")


def load_config() -> dict:
    # utf-8-sig 容忍 BOM（某些工具如 PowerShell 5.1 Set-Content 会写 BOM）
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


CONFIG = load_config()

app = FastAPI(title="voice-bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.get("cors_origins", ["http://127.0.0.1:3080"]),
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModelManager:
    """Owns the two lazily-loaded model handlers.

    Handlers are loaded on first use (heavy: whisper-large-v3 + qwen3-tts,
    plus TTS warmup ~10-60s), guarded by a lock so concurrent requests queue
    instead of double-loading. A shared `infer_lock` serializes ALL model
    work (STT + TTS share the one GPU; single-user local service).
    """

    def __init__(self) -> None:
        self._stt = None
        self._tts = None
        self._stt_error: str | None = None
        self._tts_error: str | None = None
        self._load_lock = asyncio.Lock()
        # Serializes every model inference call (STT + TTS) on the shared GPU.
        self.infer_lock = asyncio.Lock()

    @property
    def stt_ready(self) -> bool:
        return self._stt is not None

    @property
    def tts_ready(self) -> bool:
        return self._tts is not None

    @property
    def stt_error(self) -> str | None:
        return self._stt_error

    @property
    def tts_error(self) -> str | None:
        return self._tts_error

    async def ensure_stt(self):
        """Lazily load the Whisper STT handler once (thread off the event loop)."""
        async with self._load_lock:
            if self._stt is not None:
                return self._stt
            if self._stt_error is not None:
                raise HTTPException(status_code=503, detail=f"STT model failed to load: {self._stt_error}")
            try:
                self._stt = await asyncio.to_thread(_load_stt_handler)
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                logger.exception("STT model load failed")
                self._stt_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(status_code=503, detail=f"STT model load failed: {self._stt_error}")
        return self._stt

    async def ensure_tts(self):
        """Lazily load the Qwen3 TTS handler once (T3)."""
        async with self._load_lock:
            if self._tts is not None:
                return self._tts
            if self._tts_error is not None:
                raise HTTPException(status_code=503, detail=f"TTS model failed to load: {self._tts_error}")
            try:
                self._tts = await asyncio.to_thread(_load_tts_handler)
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                logger.exception("TTS model load failed")
                self._tts_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(status_code=503, detail=f"TTS model load failed: {self._tts_error}")
        return self._tts


def _load_stt_handler():
    """Instantiate the configured STT backend: 'funasr' (Chinese ASR, default
    when configured) or the original WhisperSTTHandler fallback."""
    backend = CONFIG["stt"].get("backend", "whisper")
    if backend == "funasr":
        return _load_funasr_handler()

    from queue import Empty, Queue
    from threading import Event

    from speech_to_speech.STT.whisper_stt_handler import WhisperSTTHandler

    cfg = dict(CONFIG["stt"])
    cfg.pop("backend", None)
    handler = WhisperSTTHandler(
        Event(),
        queue_in=Queue(),
        queue_out=Queue(),
        setup_args=(),
        setup_kwargs=cfg,
    )
    return handler


def _load_funasr_handler():
    """Lazily load the FunASR Chinese ASR model (Paraformer-large, 16k).

    Returns the funasr AutoModel; transcribing goes through _transcribe_funasr.
    The FunASR AutoModel caches its own singleton, so repeated loads are cheap.
    """
    from funasr import AutoModel

    model_name = CONFIG["stt"].get(
        "model_name",
        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    )
    device = CONFIG["stt"].get("device", "cuda")
    dtype = CONFIG["stt"].get("torch_dtype", "float16")
    return AutoModel(
        model=model_name,
        trust_remote_code=True,
        device=device,
        dtype=dtype,
    )


def _load_tts_handler():
    """Instantiate Qwen3TTSHandler with bridge-config.json['tts'] settings (T3)."""
    from queue import Queue
    from threading import Event

    from speech_to_speech.TTS.qwen3_tts_handler import Qwen3TTSHandler

    cfg = dict(CONFIG["tts"])
    handler = Qwen3TTSHandler(
        Event(),
        queue_in=Queue(),
        queue_out=Queue(),
        setup_args=(Event(),),  # should_listen
        setup_kwargs=cfg,
    )
    return handler


def decode_audio(body: bytes, content_type: str) -> np.ndarray:
    """Decode request audio to float32 mono at 16 kHz.

    Accepts WAV (any rate/channels soundfile can read) or raw little-endian
    16-bit PCM mono at 16 kHz (the mic-capture worklet output)."""
    if content_type == "audio/wav" or body[:4] == b"RIFF":
        import soundfile as sf

        data, sr = sf.read(io.BytesIO(body), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
    else:
        raw = np.frombuffer(body, dtype="<i2")
        data = raw.astype(np.float32) / 32768.0
        sr = 16000
    if sr != 16000:
        from scipy.signal import resample_poly

        gcd = int(np.gcd(sr, 16000))
        data = resample_poly(data, up=16000 // gcd, down=sr // gcd)
    return np.ascontiguousarray(data, dtype=np.float32)


def _transcribe(handler, audio: np.ndarray) -> tuple[str, str | None]:
    if CONFIG["stt"].get("backend", "whisper") == "funasr":
        return _transcribe_funasr(handler, audio)

    from speech_to_speech.pipeline.messages import VADAudio

    try:
        transcription = next(iter(handler.process(VADAudio(audio=audio))))
    except IndexError:
        # Upstream whisper handler assumes >= 2 generated tokens (language
        # token + content) and reads pred_ids[0, 1]; a near-silent or very
        # short utterance can produce a single token and crash. Guard: treat
        # it as an empty transcription so continuous listening never breaks.
        logger.warning("STT: whisper returned a degenerate (1-token) generation; treating as empty")
        return "", None
    return transcription.text, transcription.language_code


def _transcribe_funasr(model, audio: np.ndarray) -> tuple[str, str | None]:
    """Transcribe 16 kHz mono float32 audio with the FunASR model."""
    try:
        result = model.generate(input=audio, cache={})
        text = (result[0].get("text") or "").strip() if result else ""
        if not text:
            logger.warning("STT: funasr returned empty result; treating as empty")
        return text, "zh"
    except Exception:  # noqa: BLE001 - surfaced to the client
        logger.exception("STT: funasr transcribe failed")
        return "", None


models = ModelManager()


@app.get("/api/health")
async def health() -> dict:
    """Model readiness probe. Overall status is 'ok' once the app serves;
    stt/tts flags reflect lazy model load state (false until first use)."""
    return {
        "status": "ok",
        "stt": models.stt_ready,
        "tts": models.tts_ready,
        "stt_error": models.stt_error,
        "tts_error": models.tts_error,
    }


@app.post("/api/stt")
async def stt(request: Request) -> dict:
    """Speech to text: 16 kHz PCM16 (raw or WAV) -> { text, language }."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    audio = await asyncio.to_thread(decode_audio, body, request.headers.get("content-type", ""))
    duration = len(audio) / 16000.0
    max_sec = float(request.headers.get("X-Max-Audio-Sec", "30") or "30")
    if duration > max_sec:
        raise HTTPException(
            status_code=422,
            detail=f"Audio too long: {duration:.1f}s exceeds X-Max-Audio-Sec {max_sec}s",
        )
    async with models.infer_lock:
        handler = await models.ensure_stt()
        text, language = await asyncio.to_thread(_transcribe, handler, audio)
    return {"text": text, "language": language}


class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def tts(req: TTSRequest, request: Request) -> Response:
    """Text to speech: { text } -> 16 kHz mono PCM16 WAV (Xiaoya voice clone).

    Cooperative cancellation: while the client aborts its fetch (the voice
    toggle turned off), the request disconnects here; a watchdog sets a
    threading event and the synthesis loop stops between chunks, so the GPU
    is freed immediately instead of draining the queue."""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 512:
        logger.warning("TTS text truncated from %d to 512 chars", len(text))
        text = text[:512]

    cancel = threading.Event()

    async def watch_disconnect() -> None:
        while True:
            if await request.is_disconnected():
                cancel.set()
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch_disconnect())
    try:
        async with models.infer_lock:
            if PERSONAS.get(_current_persona, {}).get("engine", "qwen3") == "qwen3":
                handler = await models.ensure_tts()
            else:
                handler = None  # OmniVoice 引擎不需要 Qwen3 handler
            samples = await asyncio.to_thread(_synthesize, handler, text, cancel)
    finally:
        watcher.cancel()

    if cancel.is_set():
        logger.info("TTS cancelled by client disconnect")
        raise HTTPException(status_code=499, detail="TTS cancelled by client")
    wav = _pcm16_to_wav(samples)
    logger.info("TTS OK: %d chars -> %.2fs wav (%d bytes)", len(text), len(samples) / 16000.0, len(wav))
    return Response(content=wav, media_type="audio/wav")


def _synthesize(handler, text: str, cancel: threading.Event | None = None) -> np.ndarray:
    """Run TTS for one utterance, concatenating int16 chunks.

    Stops early between chunks when `cancel` is set (client disconnect).
    当前音色是 OmniVoice（design/clone）时改走 OmniVoice 引擎；
    其余走 Qwen3 handler（参考音色克隆）。
    """
    from speech_to_speech.pipeline.messages import TTSInput

    persona = PERSONAS.get(_current_persona, {})
    if persona.get("engine", "qwen3") != "qwen3":
        # OmniVoice 无法中途取消：合成为原子调用，完成后由调用方检查 cancel
        return _synthesize_omnivoice(text, persona)

    chunks = []
    for chunk in handler.process(TTSInput(text=text, language_code="zh")):
        if cancel is not None and cancel.is_set():
            logger.info("TTS: cancelled mid-synthesis")
            break
        if isinstance(chunk, bytes):
            chunks.append(np.frombuffer(chunk, dtype=np.int16))
        else:
            chunks.append(np.asarray(chunk, dtype=np.int16))
    if not chunks:
        raise HTTPException(status_code=500, detail="TTS produced no audio")
    return np.concatenate(chunks)


def _pcm16_to_wav(samples: np.ndarray) -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()


# ── OmniVoice TTS 引擎（小米 k2-fsa/OmniVoice，第二 TTS 后端）────────────────
# 服务启动：omnivoice-demo --model <dir> --port 9877 --no-asr --ip <LAN IP>
# （Gradio app，绑定局域网 IP；本机访问须用该 IP，127.0.0.1 连不上）。
# 调用方式：gradio_client 官方库（直接 HTTP 缺 session_hash 易 500）。
# 两种模式（对应 voices 下的音色子文件夹）：
#   omni-design —— design.json 参数设计音色（gender/age/pitch/style/accent/dialect）
#   omni-clone  —— ref_audio + ref_text + omnivoice.txt 标记，克隆参考音频
# 输出统一重采样为 16kHz 单声道 int16，与 Qwen3 路径一致。
# 注意：gradio_client 非线程安全 → 专用锁串行（infer_lock 之外的保险）。
OMNI_CFG = CONFIG.get("omnivoice", {})
OMNI_BASE = str(OMNI_CFG.get("base", "")).rstrip("/")
OMNI_LANG = OMNI_CFG.get("lang", "Chinese")
OMNI_TIMEOUT = float(OMNI_CFG.get("timeout_sec", 120))

_omni_lock = threading.Lock()
_omni_client = None  # 惰性单例 gradio Client


def _omni_get_client():
    """惰性创建 gradio Client（首次调用会拉取服务配置，~秒级）。"""
    global _omni_client
    if _omni_client is None:
        from gradio_client import Client
        _omni_client = Client(OMNI_BASE, verbose=False)
    return _omni_client


def _omni_default(param: str, fallback):
    return OMNI_CFG.get(param, fallback)


def _load_wav_16k(path: str) -> np.ndarray:
    """读 wav（任意采样率）→ 16kHz 单声道 int16 samples。"""
    import soundfile as sf
    from scipy.signal import resample_poly

    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import math
        g = math.gcd(int(sr), 16000)
        data = resample_poly(data, 16000 // g, int(sr) // g)
    return (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)


def _synthesize_omnivoice(text: str, persona: dict) -> np.ndarray:
    """用 OmniVoice 合成一段文本，返回 16kHz int16 samples。

    persona: PERSONAS[name]（engine=omni-design / omni-clone）。
    服务不可达时抛 RuntimeError，由调用方决定如何收尾。
    """
    if not OMNI_BASE:
        raise RuntimeError("OmniVoice 未配置：bridge-config.json 缺 omnivoice.base")
    engine = persona.get("engine", "omni-design")
    params = persona.get("params", {})
    lang = params.get("lang", OMNI_LANG)
    ns = int(params.get("ns", _omni_default("ns", 32)))
    gs = float(params.get("gs", _omni_default("gs", 2.0)))
    dn = bool(params.get("dn", _omni_default("dn", True)))
    sp = float(params.get("sp", _omni_default("sp", 1.0)))
    pp = bool(params.get("pp", _omni_default("pp", True)))
    po = bool(params.get("po", _omni_default("po", True)))
    du = params.get("du")  # 可选秒数，None=自动

    with _omni_lock:
        client = _omni_get_client()
        try:
            if engine == "omni-design":
                result = client.predict(
                    text, lang, ns, gs, dn, sp, du, pp, po,
                    params.get("gender", "Auto"),
                    params.get("age", "Auto"),
                    params.get("pitch", "Auto"),
                    params.get("style", "Auto"),
                    params.get("accent", "Auto"),
                    params.get("dialect", "Auto"),
                    api_name="/_design_fn",
                )
            else:  # omni-clone
                from gradio_client import handle_file
                result = client.predict(
                    text, lang,
                    handle_file(persona.get("ref_audio", "")),
                    persona.get("ref_text", ""),
                    "",  # instruct
                    ns, gs, dn, sp, du, pp, po,
                    api_name="/_clone_fn",
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("OmniVoice %s failed for %r", engine, text[:40])
            raise RuntimeError(f"OmniVoice 合成失败: {type(exc).__name__}: {exc}") from exc
    wav_path, status = result[0], result[1]
    logger.info("OmniVoice %s OK: status=%r chars=%d", engine, status, len(text))
    return _load_wav_16k(wav_path)


# ── Companion media hosting (T8) ────────────────────────────────────────────

BG_IMAGES_DIR = Path(CONFIG["media"]["bg_images_dir"])
TASK_VIDEOS_DIR = Path(CONFIG["media"]["task_videos_dir"])
VIDEO_EXTS = {".mp4", ".webm", ".ogg", ".mov", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}

# ── 待机动画组预设（统一从 bg-images 下子文件夹读取，动态扫描）──────────────
# 约定：BG_IMAGES_DIR（bg-images）下的每个【子文件夹】= 一个待机动画组，
# 文件夹名即组名；bg-images 根目录直接放的文件 = 默认组 "default"。
# 新增待机组：在 bg-images 下新建子文件夹放视频/图片即可，**即时生效**——
# 每次查询都重新扫描（不需要重启桥接）。没有文件的组自动隐藏。
def _idle_groups() -> dict[str, Path]:
    groups: dict[str, Path] = {"default": BG_IMAGES_DIR}
    if BG_IMAGES_DIR.is_dir():
        for entry in sorted(BG_IMAGES_DIR.iterdir()):
            if entry.is_dir():
                groups[entry.name] = entry
    # 隐藏空组（default 根目录空也隐藏，避免切到空背景）
    return {name: path for name, path in groups.items() if _list_media(path)}


_current_idle: str = "default"


def _idle_dir() -> Path:
    return _idle_groups().get(_current_idle, BG_IMAGES_DIR)


def _list_media(directory: Path) -> list[dict]:
    if not directory.is_dir():
        return []
    entries = []
    for name in sorted(os.listdir(directory)):
        ext = Path(name).suffix.lower()
        if ext in VIDEO_EXTS:
            entries.append({"name": name, "type": "video"})
        elif ext in IMAGE_EXTS:
            entries.append({"name": name, "type": "image"})
    return entries


# 默认待机动画组：优先 bridge-config persona.default_idle（组存在时），
# 否则回退 "default"（bg-images 根目录）。须在 _idle_groups/_list_media
# 定义之后解析（模块级顺序）。
_PERSONA_CFG = CONFIG.get("persona", {})
_default_idle_cfg = str(_PERSONA_CFG.get("default_idle", "") or "")
_current_idle: str = _default_idle_cfg if _default_idle_cfg in _idle_groups() else "default"


@app.get("/api/media/bg-images")
async def media_bg_images() -> dict:
    """Idle/background media list (current idle group, name-sorted)."""
    return {"media": _list_media(_idle_dir())}


@app.get("/api/media/task-videos")
async def media_task_videos() -> dict:
    """Speaking-animation video list (videos only, name-sorted)."""
    return {
        "videos": [
            entry["name"]
            for entry in _list_media(TASK_VIDEOS_DIR)
            if entry["type"] == "video"
        ]
    }


# Static mounts (Range-capable) for the companion window's <video> sources.
# bg 用动态路由：文件从「当前待机组」目录读，切换组时 URL 不变、内容跟随。
if TASK_VIDEOS_DIR.is_dir():
    app.mount("/media/task-videos", StaticFiles(directory=str(TASK_VIDEOS_DIR)), name="media-task")


@app.get("/media/bg-images/{name:path}")
async def media_bg_file(name: str):
    from fastapi.responses import FileResponse

    base = _idle_dir()
    target = (base / name).resolve()
    # 防目录穿越：必须落在当前待机组目录内
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(status_code=403, detail="forbidden")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target)


# ── 数字人 DUIX 集成 ────────────────────────────────────────────────────────
#
# 回复结束 → 插件调 /api/dh/speak {text} → 桥接用 Qwen3 TTS 整段合成 →
# 写入共享卷 temp（宿主 D:\duix_avatar_data\face2face\temp = 容器 /code/data/temp）
# → POST DUIX /easy/submit（audio_url=裸文件名, video_url=形象文件名）→
# 轮询 /easy/query 直到 success → 最终视频 <uuid>-r.mp4 落在宿主 temp 下，
# 容器已把 TTS 声音混入视频（ffmpeg -c:a aac），播放该 mp4 即音画同步。
#
# 约束：DUIX 单任务互斥（忙碌时 submit 返回 10001 busy）→ worker 串行处理；
# 排队期间来了新回复则丢弃未开始的任务，只保留最新一条（对话中只有最后的
# 回复值得生成视频）。query 查询成功/失败后任务即被服务端删除（一次性）。

DH_CFG = CONFIG.get("digital_human", {})
# ⚠️ 数字人运行时开关（2026-09-14 改）：DH_ENABLED 曾是启动时读配置的静态值，
# 且启动就无条件开 worker + 预热；插件端把数字人关掉（localStorage
# s2s.voice.digitalHuman=0）只让前端不再调 /api/dh/speak，桥接完全不知情——
# DUIX 没起时预热还会对着它连续重试提交（60 次 × 5s），就是「数字人关着、
# 桥接却一直敲 DUIX」的根因。
# 现在：_dh_enabled 运行时可热切（POST /api/dh/enable，并持久化回
# bridge-config.json）；关闭时 worker 空转、不预热、在途任务立即作废，桥接
# 不产生任何 DUIX 流量，TTS 朗读链完全不受影响。
# DH_ENABLED 仅表示「启动时的配置默认值」，运行时判断一律用 _dh_enabled()。
DH_ENABLED = bool(DH_CFG.get("enabled", False))
_dh_enabled: bool = DH_ENABLED


def _dh_enabled_now() -> bool:
    """当前数字人开关状态（运行时可热切，见 POST /api/dh/enable）。"""
    return _dh_enabled


def _persist_dh_enabled(enabled: bool) -> None:
    """把开关写回 bridge-config.json（写入失败只告警，不影响本次运行）。"""
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        raw.setdefault("digital_human", {})["enabled"] = bool(enabled)
        CONFIG_PATH.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info("DH switch persisted: digital_human.enabled=%s", enabled)
    except Exception:  # noqa: BLE001
        logger.exception("DH switch persist failed (runtime value still applied)")


DH_DUIX_BASE = DH_CFG.get("duix_base", "http://127.0.0.1:8383").rstrip("/")
DH_DATA_DIR = Path(DH_CFG.get("data_dir", "D:/duix_avatar_data/face2face"))
DH_TEMP_DIR = DH_DATA_DIR / DH_CFG.get("temp_dir", "temp")
# 生成产物子目录：数字人成品视频（<uuid>-r.mp4）统一放这里，与 temp 根目录的
# 形象素材 / 输入音频分开。形象文件（avatar*.mp4 / 自定义名.mp4）仍在 temp 根。
DH_OUTPUT_DIR = DH_TEMP_DIR / "output"
DH_AVATAR = DH_CFG.get("avatar_video", "")
DH_SUBMIT_RETRY_SEC = float(DH_CFG.get("submit_retry_sec", 5))
# 连续提交失败（DUIX 连不上）多少次后判死并回退 TTS：6 × 5s ≈ 30s 宽限，
# 够容器启动/预热用，又不会像以前那样闷头重试 15 分钟。
DH_SUBMIT_CONN_FAILS = int(DH_CFG.get("submit_conn_fails", 6))
DH_QUERY_INTERVAL = float(DH_CFG.get("query_interval_sec", 2))
DH_QUERY_TIMEOUT = float(DH_CFG.get("query_timeout_sec", 240))
DH_MAX_KEEP = int(DH_CFG.get("max_keep", 10))
# 每段音频的文本上限：~48 字 ≈ 8~10s 音频（用户实测 10s 视频十几秒出片）
DH_SEGMENT_CHARS = int(DH_CFG.get("segment_chars", 48))
DH_MAX_TEXT = 1000

# ── DUIX 容器生命周期（2026-09-14：关掉数字人 = 连容器一起停，把显存还回去）──
# 以前开关只管「桥接不再提交」，DUIX 容器仍常驻占着显存（模型十几 GB）。
# 现在关闭时顺手 docker stop 容器（release_gpu=true），打开时 docker start
# 并等它就绪（冷启动要加载模型，1-3 分钟）再预热。
DH_CONTAINER = str(DH_CFG.get("container_name", "duix-avatar-gen-video"))
DH_DOCKER_CLI = str(DH_CFG.get("docker_cli", "docker"))
DH_RELEASE_GPU = bool(DH_CFG.get("stop_container_on_disable", True))

# DUIX 容器/服务状态（status 里回给插件，卡片可以显示「容器启动中…」）
_dh_duix: dict = {"container": DH_CONTAINER, "state": "unknown", "message": "", "checked_at": 0.0}

# 可观测性：以前「任务到底提交了没有」无从查证（日志只在控制台窗口里）。
# 这些计数随 /api/dh/status 一起返回，前端/人工都能一眼确认。
_dh_stats: dict = {
    "submits": 0,           # /api/dh/speak 被成功受理的次数（插件真的提交了）
    "rejected": 0,          # 被拒次数（开关关着 / 空文本）
    "last_submit_at": 0.0,
    "last_reject": "",
    "segment_submits": 0,   # 真正提交给 DUIX 的段次数（分段逐批提交）
    "status_polls": 0,      # 状态轮询次数（证明前端在连着、在轮询）
    "last_poll_at": 0.0,
}


def _dh_duix_set(state: str, message: str = "") -> None:
    _dh_duix.update({"state": state, "message": message, "checked_at": time.time()})


def _dh_stat(key: str, delta: int = 1) -> None:
    _dh_stats[key] = int(_dh_stats.get(key, 0)) + delta


def _docker(args: list[str], timeout: float = 120.0) -> tuple[bool, str]:
    """在宿主上跑一条 docker 命令（Docker Desktop）。返回 (成功, 输出摘要)。"""
    try:
        proc = subprocess.run(
            [DH_DOCKER_CLI, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return False, f"docker CLI 不可用（{DH_DOCKER_CLI} 不在 PATH）"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode == 0, out

# ── TTS 音色预设（统一从 voices 大文件夹下子文件夹读取）────────────────────
# 约定：VOICES_DIR（voices）下的每个【子文件夹】= 一个音色，文件夹名即音色名。
# 三种音色类型（按文件夹内容自动识别，engine 字段区分）：
#   1. qwen3 克隆音色（默认）：ref_audio.wav + ref_text.txt —— Qwen3-TTS 克隆
#   2. omni-design 音色：design.json（无需音频）—— OmniVoice 参数设计音色
#   3. omni-clone 音色：ref_audio + ref_text.txt + omnivoice.txt（空标记）——
#      OmniVoice 引擎克隆参考音频（比 Qwen3 克隆更保真）
# 新增音色：在 voices 下新建子文件夹放对应文件即可，启动时自动扫描。
VOICES_DIR = Path("D:/speech-to-speech/voices")
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
TEXT_EXTS = {".txt", ".text"}


def _scan_voices() -> dict[str, dict]:
    voices: dict[str, dict] = {}
    if VOICES_DIR.is_dir():
        for entry in sorted(VOICES_DIR.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            audio = next((f for f in entry.iterdir() if f.suffix.lower() in AUDIO_EXTS), None)
            # 参考文本只认 ref_text.txt/.text —— 不能匹配所有 .txt，否则可能
            # 抓到 omnivoice.txt（OmniVoice 克隆标记）当参考文本（iterdir 无序）。
            text = next((f for f in entry.iterdir() if f.name.lower() in ("ref_text.txt", "ref_text.text")), None)
            design_file = entry / "design.json"
            omni_marker = entry / "omnivoice.txt"
            if design_file.is_file():
                # OmniVoice 设计音色：无需参考音频，参数从 design.json 读
                try:
                    params = json.loads(design_file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    logger.exception("voice %s: bad design.json, skipped", name)
                    continue
                voices[name] = {
                    "label": str(params.pop("label", name)),
                    "engine": "omni-design",
                    "params": params,
                }
            elif audio is not None and omni_marker.is_file():
                # OmniVoice 克隆音色：用 OmniVoice 引擎克隆参考音频
                ref_text = text.read_text(encoding="utf-8", errors="ignore").strip() if text is not None else ""
                voices[name] = {
                    "label": name,
                    "engine": "omni-clone",
                    "ref_audio": str(audio),
                    "ref_text": ref_text,
                }
            elif audio is not None:
                # Qwen3 克隆音色（默认引擎）
                ref_text = text.read_text(encoding="utf-8", errors="ignore").strip() if text is not None else ""
                voices[name] = {
                    "label": name,
                    "engine": "qwen3",
                    "ref_audio": str(audio),
                    "ref_text": ref_text,
                }
    return voices


PERSONAS: dict[str, dict] = _scan_voices()
# 默认音色：优先 bridge-config persona.default_voice（存在时），其次
# OmniVoice 引擎音色（engine != qwen3，保证启动不加载 Qwen3-TTS 省显存），
# 最后回退 Qwen3 系。
_default_cfg = str(_PERSONA_CFG.get("default_voice", "") or "")
if _default_cfg in PERSONAS:
    _default_voice = _default_cfg
else:
    _omni_voices = [n for n, p in PERSONAS.items() if p.get("engine", "qwen3") != "qwen3"]
    if _omni_voices:
        _default_voice = _omni_voices[0]
    elif "xiaoya-hunan" in PERSONAS:
        _default_voice = "xiaoya-hunan"
    else:
        _default_voice = next(iter(PERSONAS), "")
_current_persona: str = _default_voice
# 运行时形象覆盖（POST /api/persona/set {avatar} 手动指定时设置；None = 默认
# 用配置 avatar_video 或 temp 下第一个可用形象）。音色与形象完全独立切换。
_avatar_override: str | None = None


def _persona_avatar() -> str:
    """当前数字人形象视频文件名（提交 DUIX 时用）。

    优先级：运行时覆盖 → 配置 avatar_video → temp 下第一个非产物/非备份的
    mp4（用户放的形象素材）。都不存在时返回空（调用方会报错提示放形象）。
    素材文件（avatar*.mp4 / 自定义名.mp4）由用户放入 temp，不随代码分发。
    """
    if _avatar_override is not None:
        return _avatar_override
    configured = DH_CFG.get("avatar_video", "")
    if configured and (DH_TEMP_DIR / configured).is_file():
        return configured
    if DH_TEMP_DIR.is_dir():
        for f in sorted(DH_TEMP_DIR.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".mp4":
                continue
            if f.name.endswith("-r.mp4") or f.name.endswith(".bak.mp4"):
                continue
            return f.name
    return configured

# 生成的成品视频保留策略：磁盘上最多保留最近 max_keep 个 <uuid>-r.mp4，
# 超出删除最旧的（不碰形象素材 / 输入音频）。
# 内存里存一份最近完成任务的 {code,file,url,text,time} 供 /api/dh/history 回放。
_dh_history: list[dict] = []


def _dh_move_to_output(host_path: Path) -> str | None:
    """把 DUIX 产物（temp 根下的 -r.mp4）挪到 output 子目录；返回文件名或 None。"""
    if not host_path.is_file():
        return None
    try:
        DH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = DH_OUTPUT_DIR / host_path.name
        if target.exists():
            target.unlink()
        host_path.replace(target)
        return target.name
    except Exception:  # noqa: BLE001
        logger.exception("DH move to output failed on %s", host_path)
        return host_path.name


def _dh_prune_videos() -> None:
    """把 output 子目录下的成品视频修剪到最近 max_keep 个（新的在前，删旧的）。"""
    if not DH_OUTPUT_DIR.is_dir():
        return
    files = sorted(
        (p for p in DH_OUTPUT_DIR.glob("*-r.mp4") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[DH_MAX_KEEP:]:
        try:
            old.unlink()
            logger.info("DH prune: deleted old result %s", old.name)
        except Exception:  # noqa: BLE001
            logger.exception("DH prune failed on %s", old)
    # 顺带清理 temp 根下的 DH 中间合成音频（*.wav）：DUIX 消费生成视频后
    # 这些 wav 就没用了。保留最近 DH_MAX_KEEP 个（流水线在途的 wav 最多
    # 个位数，200 个余量充足），删更旧的，防止长期运行无限累积。
    if DH_TEMP_DIR.is_dir():
        wavs = sorted(
            (p for p in DH_TEMP_DIR.glob("*.wav") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in wavs[DH_MAX_KEEP:]:
            try:
                old.unlink()
                logger.info("DH prune: deleted old temp wav %s", old.name)
            except Exception:  # noqa: BLE001
                logger.exception("DH prune wav failed on %s", old)


def _dh_record_history(code: str, video_file: str, text: str) -> None:
    """把刚完成的视频记入内存历史（新→旧，最多 max_keep 条）。"""
    with _dh_lock:
        _dh_history.insert(
            0,
            {
                "code": code,
                "video_file": video_file,
                "video_url": f"/media/dh/{video_file}",
                "text": text,
                "created_at": time.time(),
            },
        )
        del _dh_history[DH_MAX_KEEP:]

_dh_state: dict = {
    "enabled": DH_ENABLED,
    "state": "idle",       # idle | tts | generating | done | error | discarded
    "message": "",
    "progress": 0,
    "video_file": "",      # 最近一段成品文件名（temp 下）
    "video_url": "",       # 最近一段成品媒体 URL（桥接 /media/dh/<file>）
    "videos": [],          # 本回复已产出的小段视频列表 [{video_file, video_url}]
    "total_segments": 0,   # 本回复总段数
    "done_segments": 0,    # 已产出段数
    "code": "",            # 当前（或最近完成）任务的 code
    "text": "",            # 当前（或最近完成）任务的回复文本
    "pending": 0,          # 排队中（未开始）的任务数
    "updated_at": 0.0,
}
_dh_lock = threading.Lock()
_dh_queue: list[dict] = []  # {code, text, started}
_dh_discarded: set[str] = set()  # 已作废（打断/超时）的任务 code


def _dh_set(**kw) -> None:
    with _dh_lock:
        _dh_state.update(kw)
        _dh_state["updated_at"] = time.time()


def _dh_get() -> dict:
    with _dh_lock:
        state = dict(_dh_state)
        state["pending"] = sum(1 for t in _dh_queue if not t["started"])
        # 容器状态 + 计数（前端卡片显示「容器启动中…」，人工核对「提交了没有」）
        state["duix"] = dict(_dh_duix)
        state["stats"] = dict(_dh_stats)
        return state


def _dh_pop_next() -> dict | None:
    """取下一个未开始的任务；已处理的清理掉，未开始的只保留最新一条。"""
    with _dh_lock:
        _dh_queue[:] = [t for t in _dh_queue if not t["started"]]
        if not _dh_queue:
            return None
        _dh_queue[:] = _dh_queue[-1:]
        item = _dh_queue[0]
        item["started"] = True
        return item


def _dh_has_newer_pending() -> bool:
    """是否已有更新的回复在排队（段级抢占判断）。

    队列里只保留最新一条未开始任务（见 _dh_pop_next），所以存在未开始任务
    就意味着有更新的回复在等——当前任务应停止剩余段，把 DUIX 让给最新回复。
    """
    with _dh_lock:
        return any(not t["started"] for t in _dh_queue)


class DHSpeakRequest(BaseModel):
    text: str


@app.post("/api/dh/speak")
async def dh_speak(req: DHSpeakRequest) -> dict:
    """提交一段回复文本，生成数字人口播视频（后台排队，最新替换未开始任务）。"""
    if not _dh_enabled_now():
        _dh_stat("rejected")
        _dh_stats["last_reject"] = "数字人开关关闭（/api/dh/enable {enabled:true} 可打开）"
        raise HTTPException(status_code=400, detail="digital_human disabled (POST /api/dh/enable {enabled:true} to turn on)")
    text = (req.text or "").strip()
    if not text:
        _dh_stat("rejected")
        _dh_stats["last_reject"] = "空文本"
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > DH_MAX_TEXT:
        logger.warning("DH text truncated from %d to %d chars", len(text), DH_MAX_TEXT)
        text = text[:DH_MAX_TEXT]
    code = str(uuid4())
    with _dh_lock:
        # 只保留正在运行的任务，未开始的旧排队任务被最新提交替换
        _dh_queue[:] = [t for t in _dh_queue if t["started"]]
        _dh_queue.append({"code": code, "text": text, "started": False})
        pending = sum(1 for t in _dh_queue if not t["started"])
    logger.info("DH enqueue: %s (%d chars), pending=%d", code, len(text), pending)
    _dh_stat("submits")
    _dh_stats["last_submit_at"] = time.time()
    return {"ok": True, "code": code}


class DHDiscardRequest(BaseModel):
    code: str


@app.post("/api/dh/discard")
async def dh_discard(req: DHDiscardRequest) -> dict:
    """作废一个已提交的数字人任务（用户打断/放弃该回复）：结果不再播放。"""
    code = (req.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Empty code")
    with _dh_lock:
        _dh_discarded.add(code)
        # 若它还在排队，直接移除
        _dh_queue[:] = [t for t in _dh_queue if t.get("code") != code]
    logger.info("DH discard: %s", code)
    return {"ok": True}


@app.get("/api/dh/status")
async def dh_status() -> dict:
    """数字人任务状态：插件轮询；done 时带 video_url。"""
    _dh_stat("status_polls")
    _dh_stats["last_poll_at"] = time.time()
    return _dh_get()


class DHEnableRequest(BaseModel):
    enabled: bool


@app.post("/api/dh/enable")
async def dh_enable(req: DHEnableRequest) -> dict:
    """运行时开关数字人（热切换，无需重启桥接；并持久化到 bridge-config.json）。

    ON  ：起 worker、确保 DUIX 容器在跑（必要时 docker start + 等就绪）、预热
    OFF ：清空队列 + 在途任务全部作废、状态复位、worker 空转（零 DUIX 流量），
          并 docker stop 掉 DUIX 容器把显存还回去（release_gpu=true 时）
    这就是插件端数字人开关的桥接侧落点：关掉=桥接停手 + 容器也停。
    """
    global _dh_enabled
    enabled = bool(req.enabled)
    with _dh_lock:
        if enabled == _dh_enabled:
            return {"ok": True, "enabled": _dh_enabled, "changed": False}
        _dh_enabled = enabled
        if not enabled:
            # 在途 + 排队任务全部作废（结果不再播放、不再提交新段）
            for t in _dh_queue:
                _dh_discarded.add(t["code"])
            _dh_queue.clear()
            current = _dh_state.get("code") or ""
            if current:
                _dh_discarded.add(current)
    # 复位状态字段（两种方向都清干净，避免插件端读到上一轮的残留视频）
    _dh_set(
        enabled=enabled, state="idle", message="", progress=0,
        video_file="", video_url="", videos=[], total_segments=0,
        done_segments=0, code="", text="",
    )
    if enabled:
        DH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _dh_prune_videos()
        _dh_ensure_worker()
        # 0.1.3 级联动：先确保 DUIX 容器在跑（冷启动可能要 1-3 分钟），就绪后再预热
        asyncio.create_task(_dh_bring_up())
    elif DH_RELEASE_GPU:
        # 关掉数字人 = 连容器一起停，把显存还给别的服务（如 OmniVoice / LLM）
        asyncio.create_task(_dh_container_stop())
    else:
        _dh_duix_set("running", "容器保持运行（release_gpu=false）")
    _persist_dh_enabled(enabled)
    logger.info("DH runtime switch: %s (container stop=%s)", "ON" if enabled else "OFF", DH_RELEASE_GPU)
    return {"ok": True, "enabled": enabled, "changed": True, "duix": dict(_dh_duix)}


@app.get("/api/dh/history")
async def dh_history() -> dict:
    """最近保留的成品视频列表（新→旧，最多 max_keep 条），供回放/查看。"""
    videos = []
    with _dh_lock:
        for entry in _dh_history:
            videos.append(dict(entry))
    if not videos and DH_TEMP_DIR.is_dir():
        # 进程重启后从磁盘重建
        files = sorted(
            (p for p in DH_TEMP_DIR.glob("*-r.mp4") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:DH_MAX_KEEP]
        videos = [
            {
                "code": "",
                "video_file": p.name,
                "video_url": f"/media/dh/{p.name}",
                "text": "",
                "created_at": p.stat().st_mtime,
            }
            for p in files
        ]
    return {"videos": videos, "max_keep": DH_MAX_KEEP}


def _split_segments(text: str, max_len: int = 48) -> list[str]:
    """把文本按标点切成语义完整的段。

    规则：先按句末标点（。！？!?；;）切出完整句子，句子尽量不劈开——
    累积句子成段，若下一句放不下（超过 max_len）就收尾本段、下句开新段；
    只有单句本身超长（无标点）才硬切。时长是软参考，不刻意控制。
    """
    import re

    parts = re.split(r"(?<=[。！？!?；;])", text)
    sentences: list[str] = [p.strip() for p in parts if p.strip()]

    out: list[str] = []
    cur = ""

    def flush() -> None:
        nonlocal cur
        if cur.strip():
            out.append(cur.strip())
        cur = ""

    for sent in sentences:
        # 单句超长（无标点长句）：句内硬切，尽量在次级标点后切。
        while len(sent) > max_len:
            head = sent[:max_len]
            pos = max((head.rfind(c) for c in "，、：；:,. "), default=-1)
            cut = pos + 1 if pos > 0 else max_len
            piece = head[:cut].strip()
            if piece:
                flush() if cur else None
                out.append(piece)
            sent = sent[cut:].strip()
            if not sent:
                break
        # 句子完整累积：放不下就收尾本段（不拆句）。
        if cur and len(cur) + len(sent) > max_len:
            flush()
        cur += sent
    flush()
    return out


def _synthesize_full(handler, text: str) -> np.ndarray:
    """整段回复 TTS：超长文本分块合成后拼接为一条连续 int16 音频。"""
    from speech_to_speech.pipeline.messages import TTSInput

    persona = PERSONAS.get(_current_persona, {})
    if persona.get("engine", "qwen3") != "qwen3":
        # OmniVoice：同样分块（每块一次合成调用），再拼接
        chunks = [_synthesize_omnivoice(part, persona) for part in _split_segments(text, 400)]
        if not chunks:
            raise RuntimeError("DH TTS produced no audio")
        return np.concatenate(chunks)

    chunks: list[np.ndarray] = []
    for part in _split_segments(text, 400):
        for chunk in handler.process(TTSInput(text=part, language_code="zh")):
            if isinstance(chunk, bytes):
                chunks.append(np.frombuffer(chunk, dtype=np.int16))
            else:
                chunks.append(np.asarray(chunk, dtype=np.int16))
    if not chunks:
        raise RuntimeError("DH TTS produced no audio")
    return np.concatenate(chunks)


async def _dh_submit(client: httpx.AsyncClient, payload: dict) -> dict:
    resp = await client.post(f"{DH_DUIX_BASE}/easy/submit", json=payload)
    resp.raise_for_status()
    return resp.json()


async def _dh_query(client: httpx.AsyncClient, code: str) -> dict | None:
    resp = await client.get(f"{DH_DUIX_BASE}/easy/query", params={"code": code})
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") == 10004:  # 任务不存在（可能刚提交或已被消费）
        return None
    return body.get("data") or {}


async def _dh_run(item: dict) -> None:
    """处理一个数字人回复：切成 <=10s 的小段 → 逐段 TTS + 提交 DUIX。

    流水线：当前段在 DUIX 生成期间，后台预合成下一段的 TTS 音频；DUIX
    一空闲立即提交下一段。各段成品视频按顺序收进 status.videos，插件端
    逐个续接播放（一段播完马上接下一段）。段太长时 DUIX 单任务排队。
    """
    code, text = item["code"], item["text"]
    segments = [s for s in _split_segments(text, DH_SEGMENT_CHARS) if s]
    if not segments:
        _dh_set(state="error", message="文本为空", code=code, text=text)
        return
    total = len(segments)
    try:
        _dh_set(
            state="tts", message="语音合成中…", code=code, text=text,
            video_file="", video_url="", videos=[], total_segments=total,
            done_segments=0, progress=0,
        )
        async with httpx.AsyncClient(timeout=20) as client:
            pending_synth: asyncio.Task | None = None
            for i, seg_text in enumerate(segments):
                if code in _dh_discarded:
                    if pending_synth is not None:
                        pending_synth.cancel()
                    _dh_set(state="discarded", message="已取消（被打断）", progress=0, code=code, text=text, videos=[])
                    logger.info("DH %s: discarded before segment %d", code, i)
                    return
                if _dh_has_newer_pending():
                    # 更新的回复已提交：停止本任务剩余段，把 DUIX 让给最新回复。
                    # 已生成的段视频保留在磁盘（存取不受影响）；新任务开始后
                    # （code 变化）companion 自动切到新任务的播放列表。
                    _dh_set(
                        state="discarded", message="被新回复取代",
                        progress=round(i / total * 100),
                        code=code, text=text, done_segments=i,
                    )
                    logger.info("DH %s: preempted by newer task at segment %d/%d", code, i, total)
                    if pending_synth is not None:
                        pending_synth.cancel()
                    return
                _dh_set(
                    state="generating",
                    message=f"数字人生成中 {i + 1}/{total}…",
                    code=code, text=text, done_segments=i,
                    progress=round(i / total * 100),
                )
                # 当前段音频：优先用流水线预合成好的；预合成失败则回退现合成
                if pending_synth is not None:
                    try:
                        wav_path = await pending_synth
                    except Exception:  # noqa: BLE001
                        logger.exception("DH %s: preset synth failed at segment %d, synthesizing now", code, i)
                        wav_path = await _dh_synth_segment(seg_text)
                    pending_synth = None
                else:
                    wav_path = await _dh_synth_segment(seg_text)
                # 预合成下一段（当前段在 DUIX 生成期间并行跑）
                next_synth: asyncio.Task | None = None
                if i + 1 < total:
                    next_synth = asyncio.create_task(_dh_synth_segment(segments[i + 1]))
                # 提交 + 轮询当前段，拿到成品文件名
                seg_code = f"{code}-s{i}"
                fname = await _dh_submit_and_wait(client, seg_code, wav_path, code, text)
                if fname is None:
                    if next_synth is not None:
                        next_synth.cancel()
                    if code in _dh_discarded:
                        _dh_set(state="discarded", message="已取消（被打断）", progress=0, code=code, text=text, videos=[])
                    return  # 错误/超时状态已由 _dh_submit_and_wait 设置
                with _dh_lock:
                    videos = _dh_state.get("videos", [])
                    videos = videos + [{"video_file": fname, "video_url": f"/media/dh/{fname}"}]
                    _dh_state["videos"] = videos
                _dh_record_history(seg_code, fname, seg_text)
                # 下一段音频已在此段 DUIX 生成期间预合成：把任务递给下一轮直接
                # 取用。此前这里 await 后把结果丢弃、pending_synth 永远为空，
                # 下一轮只能对同一段重复合成（TTS 白跑一遍、流水线提速归零）。
                pending_synth = next_synth
                _dh_set(
                    state="generating",
                    message=f"数字人生成中 {i + 2}/{total}…",
                    code=code, text=text, done_segments=i + 1,
                    progress=round((i + 1) / total * 100),
                )
            # 全部段落完成
            with _dh_lock:
                videos = list(_dh_state.get("videos", []))
            first = videos[0] if videos else {}
            _dh_set(
                state="done", message="数字人视频已就绪", progress=100,
                video_file=first.get("video_file", ""),
                video_url=first.get("video_url", ""),
                code=code, text=text, total_segments=total, done_segments=total,
            )
            _dh_prune_videos()
            logger.info(
                "DH %s: done, %d segment(s): %s",
                code, total, [v["video_file"] for v in videos],
            )
    except Exception as exc:  # noqa: BLE001 - surfaced to the plugin
        logger.exception("DH %s: task failed", code)
        if code in _dh_discarded:
            _dh_set(state="discarded", message="已取消（被打断）", progress=0, code=code, text=text, videos=[])
        else:
            _dh_set(state="error", message=f"任务异常: {type(exc).__name__}", code=code, text=text)


async def _dh_synth_segment(seg_text: str) -> Path:
    """合成一小段音频到 temp 目录，返回宿主路径。"""
    async with models.infer_lock:
        if PERSONAS.get(_current_persona, {}).get("engine", "qwen3") == "qwen3":
            handler = await models.ensure_tts()
        else:
            handler = None  # OmniVoice 引擎不需要 Qwen3 handler
        samples = await asyncio.to_thread(_synthesize_full, handler, seg_text)
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    wav_path = DH_TEMP_DIR / f"{ts}.wav"
    wav_path.write_bytes(_pcm16_to_wav(samples))
    logger.info("DH synth: %.2fs audio -> %s", len(samples) / 16000.0, wav_path.name)
    return wav_path


async def _dh_submit_and_wait(
    client: httpx.AsyncClient,
    seg_code: str,
    wav_path: Path,
    reply_code: str,
    reply_text: str,
) -> str | None:
    """提交一段到 DUIX 并轮询到成品视频；返回文件名，失败返回 None。

    状态已设置（error/超时）时返回 None，由调用方收尾。DUIX Status 是数字
    枚举：1=处理中（带进度 msg）、2=任务完成（带 result）、3=失败（推测）；
    同时兼容字符串 "s"/"e"/"r"/"success"/...。
    """
    payload = {
        "code": seg_code,
        "audio_url": wav_path.name,
        "video_url": _persona_avatar(),
        "watermark_switch": 0,
        "digital_auth": 0,
        "chaofen": 0,
        "pn": 1,
    }
    submitted = False
    submit_fails = 0
    max_fails = DH_SUBMIT_CONN_FAILS
    for _ in range(180):  # 最多等 15 分钟 busy 释放
        if not _dh_enabled_now():
            # 开关关掉：立刻停手，不再提交/占用 DUIX
            logger.info("DH %s: aborted (digital human switched off)", seg_code)
            return None
        try:
            resp = await _dh_submit(client, payload)
        except Exception as exc:  # noqa: BLE001
            # DUIX 不可达（容器没起/正在冷启动）：前几次静默重试，连续失败到
            # 阈值就判死回退 TTS。容器正在 starting/stopping 时给足宽限
            # （冷启动要加载模型 1-3 分钟），别把正常启动误判成故障。
            if _dh_duix.get("state") in ("starting", "stopping"):
                max_fails = max(DH_SUBMIT_CONN_FAILS, int(240 / max(DH_SUBMIT_RETRY_SEC, 1)))
            submit_fails += 1
            if submit_fails == 1:
                logger.warning(
                    "DH %s: DUIX unreachable at %s (%s) — retrying up to %d×",
                    seg_code, DH_DUIX_BASE, type(exc).__name__, max_fails,
                )
            if submit_fails >= max_fails:
                _dh_set(
                    state="error",
                    message=f"DUIX 不可达（{DH_DUIX_BASE} 未响应）",
                    code=reply_code, text=reply_text,
                )
                return None
            await asyncio.sleep(DH_SUBMIT_RETRY_SEC)
            continue
        submit_fails = 0
        if resp.get("code") == 10000:
            submitted = True
            _dh_stat("segment_submits")
            break
        if resp.get("code") == 10001:  # busy
            await asyncio.sleep(DH_SUBMIT_RETRY_SEC)
            continue
        _dh_set(state="error", message=f"提交失败: {resp.get('msg')}", code=reply_code, text=reply_text)
        return None
    if not submitted:
        _dh_set(state="error", message="提交超时（DUIX 持续忙碌）", code=reply_code, text=reply_text)
        return None

    deadline = time.time() + DH_QUERY_TIMEOUT
    while time.time() < deadline:
        if not _dh_enabled_now():
            logger.info("DH %s: polling aborted (digital human switched off)", seg_code)
            return None
        await asyncio.sleep(DH_QUERY_INTERVAL)
        q = await _dh_query(client, seg_code)
        if q is None:
            continue
        status = str(q.get("status"))
        if status in ("2", "success", "s", "done", "finished", "成功"):
            fname = str(q.get("result") or "").rsplit("/", 1)[-1]
            host = DH_TEMP_DIR / fname
            for _ in range(15):  # 等文件落盘（最多 15s）
                if host.is_file():
                    break
                await asyncio.sleep(1)
            moved = _dh_move_to_output(host)
            logger.info("DH %s: segment done -> %s", seg_code, moved or fname)
            return moved or fname
        if status in ("3", "error", "e", "failed", "失败"):
            _dh_set(state="error", message=f"生成失败: {q.get('msg')}", code=reply_code, text=reply_text)
            return None
        # 1 / run / 未知：生成中的中间状态绝不误判失败；带 result 时视为成功
        if status not in ("1", "run", "r", "running", "运行") and q.get("result"):
            fname = str(q.get("result")).rsplit("/", 1)[-1]
            if (DH_TEMP_DIR / fname).is_file():
                moved = _dh_move_to_output(DH_TEMP_DIR / fname)
                logger.info("DH %s: segment done (result field) -> %s", seg_code, moved or fname)
                return moved or fname
    _dh_set(state="error", message="生成超时", code=reply_code, text=reply_text)
    return None


async def _dh_worker() -> None:
    """后台队列 worker：串行消费 /api/dh/speak 提交的任务。

    完成后保留 done/error/discarded 状态（含 video_url），供插件取用；
    新任务开始时才清空旧结果（见 _dh_run 开头）。
    数字人开关关闭时空转（不消费队列、不碰 DUIX）——开关一关，桥接零 DUIX 流量。"""
    while True:
        if not _dh_enabled_now():
            await asyncio.sleep(1.0)
            continue
        item = _dh_pop_next()
        if item is None:
            await asyncio.sleep(1.0)
            continue
        try:
            await _dh_run(item)
        except Exception:  # noqa: BLE001
            logger.exception("DH worker crashed on %s", item.get("code"))
            _dh_set(state="error", message="worker 异常", code=item.get("code", ""), text=item.get("text", ""))


_dh_worker_task: "asyncio.Task | None" = None


def _dh_ensure_worker() -> None:
    """确保 worker 在跑（运行时可反复开关，重复调用安全）。"""
    global _dh_worker_task
    if _dh_worker_task is None or _dh_worker_task.done():
        _dh_worker_task = asyncio.create_task(_dh_worker())
        logger.info("DH worker started")


async def _dh_reachable(timeout: float = 3.0) -> bool:
    """DUIX 是否可达：GET /easy/query 探活（容器没起时立刻 False，不重试）。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{DH_DUIX_BASE}/easy/query", params={"code": "probe"})
            return resp.status_code < 500
    except Exception:  # noqa: BLE001
        return False


async def _dh_container_stop() -> None:
    """停掉 DUIX 容器，把显存真的还回去（关数字人 = 连 GPU 一起放）。"""
    _dh_duix_set("stopping", "正在停止 DUIX 容器…")
    ok, out = await asyncio.to_thread(_docker, ["stop", DH_CONTAINER], 180)
    if ok:
        _dh_duix_set("stopped", "DUIX 已停止，显存已释放")
        logger.info("DH container stopped (%s): %s", DH_CONTAINER, out)
    else:
        _dh_duix_set("unknown", f"停止容器失败: {out[:160]}")
        logger.warning("DH container stop failed: %s", out)


async def _dh_wait_duix_ready(timeout: float = 240.0) -> bool:
    """等 DUIX 真的能应答（冷启动要加载模型，可能 1-3 分钟）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _dh_enabled_now():
            return False
        if await _dh_reachable():
            return True
        await asyncio.sleep(3)
    return False


async def _dh_container_start() -> None:
    """启动 DUIX 容器并等它就绪；失败/超时只更新状态，不阻塞其它功能。"""
    _dh_duix_set("starting", "正在启动 DUIX 容器…")
    ok, out = await asyncio.to_thread(_docker, ["start", DH_CONTAINER], 120)
    if not ok:
        _dh_duix_set("unavailable", f"启动容器失败: {out[:160]}")
        logger.warning("DH container start failed: %s", out)
        return
    logger.info("DH container starting (%s)…", DH_CONTAINER)
    if await _dh_wait_duix_ready():
        _dh_duix_set("running", "DUIX 就绪")
        logger.info("DH container ready at %s", DH_DUIX_BASE)
    else:
        _dh_duix_set("unavailable", "DUIX 容器未在超时内就绪")
        logger.warning("DH container did not become ready in time")


async def _dh_bring_up() -> None:
    """打开数字人：确保容器在跑 → 真正就绪后再预热。"""
    if not _dh_enabled_now():
        return
    if await _dh_reachable():
        _dh_duix_set("running", "DUIX 就绪")
    else:
        await _dh_container_start()
    if _dh_enabled_now() and _dh_duix.get("state") == "running":
        await _dh_warmup()


@app.on_event("startup")
async def _dh_start_worker() -> None:
    if _dh_enabled_now():
        _dh_prune_videos()  # 启动时把成品视频修剪到最近 max_keep 个
        _dh_ensure_worker()
        # 预热前先确保容器在跑（DUIX 不可达时预热会直接跳过，不再空转重试）；
        # 预热让 wenet 特征提取 / init_wh / 模型加载 / TRT engine 走一遍热路径，
        # 首条真实回复的等待时间显著缩短。预热独立于 _dh_queue，结果直接丢弃。
        asyncio.create_task(_dh_bring_up())
        logger.info("DH worker started (DUIX %s, container=%s, temp=%s, avatar=%s, max_keep=%d)", DH_DUIX_BASE, DH_CONTAINER, DH_TEMP_DIR, DH_AVATAR, DH_MAX_KEEP)
    else:
        logger.info("DH disabled — no worker, no warmup, zero DUIX traffic")


async def _dh_warmup() -> None:
    """后台预热：跑一次完整的「TTS → 特征 → init_wh → 生成」链路。

    预热输出是「数字人没有说话」的占位短句（或仅预合成音频 + 提交），
    完成即删，不进入播放列表、不污染 max_keep 历史。
    DUIX 不可达时直接跳过（旧版会在这里对着空气重试 60×5s，是「关着数字人
    还一直敲 DUIX」的元凶之一）。"""
    try:
        if not _dh_enabled_now():
            return
        if not await _dh_reachable():
            logger.info("DH warmup skipped: DUIX unreachable at %s", DH_DUIX_BASE)
            return
        logger.info("DH warmup: synthesizing warmup audio…")
        wav = await _dh_synth_segment("数字人系统预热完成")
        code = f"warmup-{uuid4()}"
        payload = {
            "code": code,
            "audio_url": wav.name,
            "video_url": _persona_avatar(),
            "watermark_switch": 0,
            "digital_auth": 0,
            "chaofen": 0,
            "pn": 1,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            # 预热不写 _dh_state：直接提交 + 轮询，完成即删，失败静默。
            warm_fails = 0
            for _ in range(60):  # 最多等 5 分钟 busy 释放
                if not _dh_enabled_now():
                    logger.info("DH warmup: aborted (switched off)")
                    wav.unlink(missing_ok=True)
                    return
                try:
                    resp = await _dh_submit(client, payload)
                except Exception:  # noqa: BLE001
                    # DUIX 掉线：连续失败到阈值就放弃预热（旧版在这里闷头重试
                    # 5 分钟，每次都是一条到 :9000 的连接尝试 → 日志/连接刷屏）
                    warm_fails += 1
                    if warm_fails >= DH_SUBMIT_CONN_FAILS:
                        logger.warning("DH warmup: DUIX unreachable, skipping warmup")
                        wav.unlink(missing_ok=True)
                        return
                    await asyncio.sleep(DH_SUBMIT_RETRY_SEC)
                    continue
                warm_fails = 0
                if resp.get("code") == 10000:
                    break
                if resp.get("code") == 10001:  # busy（真实任务在跑，跳过预热）
                    logger.info("DH warmup: DUIX busy, skipping warmup")
                    wav.unlink(missing_ok=True)
                    return
                await asyncio.sleep(DH_SUBMIT_RETRY_SEC)
            else:
                logger.warning("DH warmup: DUIX busy too long, skipping")
                wav.unlink(missing_ok=True)
                return
            deadline = time.time() + 120
            fname = ""
            while time.time() < deadline:
                await asyncio.sleep(DH_QUERY_INTERVAL)
                q = await _dh_query(client, code)
                if q is None:
                    continue
                status = str(q.get("status"))
                if status in ("2", "success", "s", "done", "finished", "成功"):
                    fname = str(q.get("result") or "").rsplit("/", 1)[-1]
                    break
            host = DH_TEMP_DIR / fname if fname else None
            if host is not None:
                # 挪到 output 子目录再删（保持 temp 根只有形象素材）
                moved = _dh_move_to_output(host)
                if moved is not None:
                    (DH_OUTPUT_DIR / moved).unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
            logger.info("DH warmup: done (prewarmed TTS/wenet/DUIX)%s", f" {fname} cleaned" if fname else "")
    except Exception:  # noqa: BLE001
        logger.exception("DH warmup failed (non-fatal)")


# 结果视频静态挂载：插件 video 元素直接播 /media/dh/<uuid>-r.mp4（Range 支持）。
# 产物统一在 output 子目录（与形象素材分离）。
# 注意：不按 DH_ENABLED 门控 —— 开关现在是运行时可切的，若这里跳过挂载，
# 之后再把数字人打开时 /media/dh 会 404（视频生成了却播不出来）。
if DH_TEMP_DIR.is_dir():
    DH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/media/dh", StaticFiles(directory=str(DH_OUTPUT_DIR)), name="media-dh")


# ── 人设/预设切换（音色 + 数字人形象 + 待机动画，三类独立）─────────────────
#
# GET  /api/persona/list  → 三类预设列表 + 当前选择
# POST /api/persona/set   → 可选字段 {voice?, avatar?, idle?} 分别切换
#    voice  : TTS 参考音色（热切：改 handler 属性，下一段合成即生效）
#    avatar : 数字人形象视频文件名（后续 /api/dh/* 提交用新形象）
#    idle   : 待机动画组名（/api/media/bg-images 与静态文件动态跟随）
# 三类互不影响，各自独立选择；重启回默认，插件端可持久化并启动时恢复。


class PersonaSetRequest(BaseModel):
    voice: str | None = None
    avatar: str | None = None
    idle: str | None = None


def _voice_entry(name: str) -> dict:
    fallback = next(iter(PERSONAS.values()), {})
    p = PERSONAS.get(name, fallback)
    return {"name": name, "label": p.get("label", name), "current": name == _current_persona}


@app.get("/api/persona/list")
async def persona_list() -> dict:
    voices = [_voice_entry(n) for n in PERSONAS]
    # 形象：扫描 temp 目录下的 mp4 形象文件（任意命名，排除生成产物 -r.mp4
    # 与备份 .bak.mp4）。名字即文件名（去扩展名）。
    avatar_files = []
    if DH_TEMP_DIR.is_dir():
        for f in sorted(DH_TEMP_DIR.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".mp4":
                continue
            if f.name.endswith("-r.mp4") or f.name.endswith(".bak.mp4"):
                continue  # 跳过生成产物与备份
            avatar_files.append({
                "name": f.name,
                "label": f.stem,
                "current": f.name == _persona_avatar(),
            })
    idles = [{"name": n, "label": n, "current": n == _current_idle} for n in _idle_groups()]
    return {
        "voices": voices,
        "avatars": avatar_files,
        "idles": idles,
        "current": {
            "voice": _current_persona,
            "avatar": _persona_avatar(),
            "idle": _current_idle,
        },
    }


@app.post("/api/persona/set")
async def persona_set(req: PersonaSetRequest) -> dict:
    global _current_persona, _current_idle, _avatar_override
    result: dict = {"ok": True}

    # 音色切换
    if req.voice is not None:
        name = req.voice.strip()
        if name not in PERSONAS:
            raise HTTPException(status_code=404, detail=f"Unknown voice: {name}")
        p = PERSONAS[name]
        if p.get("engine", "qwen3") == "qwen3":
            # 只有 Qwen3 音色需要热切 handler.ref_audio/ref_text；OmniVoice
            # 音色走独立引擎，不加载 Qwen3（否则首次切换会卡几十秒加载）。
            try:
                handler = await models.ensure_tts()
                handler.ref_audio = p.get("ref_audio") or None
                handler.ref_text = p.get("ref_text", "")
            except HTTPException:
                logger.warning("persona set: TTS not ready, switching voice config only")
        _current_persona = name
        # 音色与形象完全独立：切音色不影响当前形象选择。
        result["voice"] = name
        logger.info("voice switched to %s", name)

    # 形象切换
    if req.avatar is not None:
        av = req.avatar.strip()
        if not (DH_TEMP_DIR / av).is_file():
            raise HTTPException(status_code=404, detail=f"Avatar file not found: {av}")
        _avatar_override = av
        result["avatar"] = av
        logger.info("avatar switched to %s", av)

    # 待机动画切换
    if req.idle is not None:
        name = req.idle.strip()
        if name not in _idle_groups():
            raise HTTPException(status_code=404, detail=f"Unknown idle group: {name}")
        _current_idle = name
        result["idle"] = name
        logger.info("idle switched to %s", name)

    if not result.get("voice") and not result.get("avatar") and not result.get("idle"):
        raise HTTPException(status_code=400, detail="Nothing to set")
    return result


# ── DeepSeek 余额（低开销：10 分钟内存缓存，挂载/点击才查）────────────────────
#
# 调官方 GET https://api.deepseek.com/user/balance（Bearer 鉴权），返回
# CNY/USD 总余额 + 赠金 + 充值。key 来源：bridge-config.json 的
# deepseek.api_key 优先，否则环境变量 DEEPSEEK_API_KEY。缓存期内重复请求
# 不再打官方接口，日常零轮询、零开销。

_balance_cache: dict = {"data": None, "at": 0.0}
BALANCE_CACHE_SEC = 600


@app.get("/api/balance")
async def api_balance() -> dict:
    now = time.time()
    if _balance_cache["data"] is not None and now - _balance_cache["at"] < BALANCE_CACHE_SEC:
        cached = dict(_balance_cache["data"])
        cached["cached"] = True
        return cached
    key = (CONFIG.get("deepseek") or {}).get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="DEEPSEEK_API_KEY not configured (bridge-config deepseek.api_key or env)")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("balance query failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"DeepSeek balance query failed: {exc}") from exc
    result = {
        "is_available": body.get("is_available"),
        "balance_infos": body.get("balance_infos", []),
        "cached": False,
        "at": now,
    }
    _balance_cache["data"] = result
    _balance_cache["at"] = now
    return result


# ── QQ 推送（NapCat OneBot）───────────────────────────────────────────────
#
# Sends text and TTS voice to a target QQ via a local NapCat OneBot v11 HTTP
# endpoint. Config (bridge-config.json):
#   "qq": {
#     "enabled": true,
#     "napcat_base": "http://127.0.0.1:3000",
#     "napcat_token": "",
#     "target_qq": 0
#   }

class QQSendRequest(BaseModel):
    text: str
    voice: bool = False
    user_id: int | None = None  # override the configured target


class QQImageRequest(BaseModel):
    path: str
    user_id: int | None = None


@app.post("/api/qq/image")
async def qq_send_image(req: QQImageRequest) -> dict:
    """Send a local image file to the configured QQ."""
    qq = CONFIG.get("qq", {})
    if not qq.get("enabled"):
        raise HTTPException(status_code=400, detail="QQ push disabled in bridge-config.json")
    base = qq.get("napcat_base", "http://127.0.0.1:3000")
    token = qq.get("napcat_token", "")
    user_id = req.user_id or qq.get("target_qq")
    if not user_id:
        raise HTTPException(status_code=400, detail="target_qq not configured")
    if not Path(req.path).is_file():
        raise HTTPException(status_code=404, detail=f"image not found: {req.path}")
    from qq_bridge import send_image

    try:
        result = send_image(base, token, user_id, req.path)
        return {"ok": True, "user_id": user_id, "napcat": result}
    except Exception as exc:  # noqa: BLE001 - surfaced to the client
        logger.exception("QQ image send failed")
        raise HTTPException(status_code=502, detail=f"QQ image send failed: {exc}") from exc


@app.post("/api/qq/send")
async def qq_send(req: QQSendRequest) -> dict:
    """Send { text } (and optionally TTS voice) to the configured QQ."""
    qq = CONFIG.get("qq", {})
    if not qq.get("enabled"):
        raise HTTPException(status_code=400, detail="QQ push disabled in bridge-config.json")
    base = qq.get("napcat_base", "http://127.0.0.1:3000")
    token = qq.get("napcat_token", "")
    user_id = req.user_id or qq.get("target_qq")
    if not user_id:
        raise HTTPException(status_code=400, detail="target_qq not configured")
    if not req.text.strip() and not req.voice:
        raise HTTPException(status_code=400, detail="Empty text")

    from qq_bridge import send_text, send_voice

    try:
        if req.voice:
            if not req.text.strip():
                raise HTTPException(status_code=400, detail="voice needs text to synthesize")
            text = (req.text or "").strip()[:512]
            cancel = threading.Event()
            async with models.infer_lock:
                if PERSONAS.get(_current_persona, {}).get("engine", "qwen3") == "qwen3":
                    handler = await models.ensure_tts()
                else:
                    handler = None  # OmniVoice 引擎不需要 Qwen3 handler
                samples = await asyncio.to_thread(_synthesize, handler, text, cancel)
            result = send_voice(base, token, user_id, samples.astype("<i2").tobytes())
        else:
            result = send_text(base, token, user_id, req.text.strip()[:2000])
        return {"ok": True, "user_id": user_id, "napcat": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the client
        logger.exception("QQ send failed")
        raise HTTPException(status_code=502, detail=f"QQ send failed: {exc}") from exc


# ── QQ 双向：事件接收 + 插件 WS 桥 ────────────────────────────────────────
#
# NapCat 把消息事件 POST 到 /api/qq/event（HTTP 上报 postUrls）；桥接再把
# 私聊文本推给已连接的浏览器插件（/api/qq/ws）。插件注入 DSH，回复完成后
# 把回复文本发回桥接（WS {"type":"reply"}），桥接 TTS→silk→QQ 发出。
# 单连接设计（个人使用）：新连接顶掉旧连接。

_qq_ws_conn: WebSocket | None = None
_qq_ws_lock = asyncio.Lock()


async def _qq_push(json_msg: dict) -> None:
    global _qq_ws_conn
    async with _qq_ws_lock:
        conn = _qq_ws_conn
    if conn is not None:
        try:
            await conn.send_json(json_msg)
        except Exception:
            logger.debug("QQ ws push failed (client gone)", exc_info=True)


@app.post("/api/qq/event")
async def qq_event(body: dict) -> dict:
    """OneBot v11 HTTP 上报入口（NapCat postUrls）。私聊文本消息 → 推给插件。"""
    try:
        post_type = body.get("post_type")
        if post_type == "message" and body.get("message_type") == "private":
            user_id = body.get("user_id")
            text = str(body.get("raw_message") or body.get("message") or "").strip()
            if user_id and text:
                await _qq_push({"type": "qq_message", "user_id": user_id, "text": text})
                logger.info("QQ event: %s -> %s", user_id, text[:40])
    except Exception:  # noqa: BLE001 - never break the upstream event feed
        logger.exception("QQ event handling failed")
    return {"ok": True}


@app.websocket("/api/qq/onebot")
async def qq_onebot_ws(ws: WebSocket) -> None:
    """NapCat WebSocket 客户端连到这里（OneBot 事件推送）。

    在 NapCat WebUI 网络配置里添加一个「WebSocket 客户端」指向
    ws://127.0.0.1:8765/api/qq/onebot，NapCat 会把全部事件推过来；
    私聊文本消息同样经 _qq_push 转给浏览器插件。这绕开了 HTTP 3000
    服务不稳定时的事件上报缺口。
    """
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            if not raw.strip():
                continue
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                continue
            post_type = body.get("post_type")
            if post_type == "message" and body.get("message_type") == "private":
                user_id = body.get("user_id")
                text = str(body.get("raw_message") or body.get("message") or "").strip()
                if user_id and text:
                    await _qq_push({"type": "qq_message", "user_id": user_id, "text": text})
                    logger.info("QQ event(ws): %s -> %s", user_id, text[:40])
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("QQ onebot ws error")


@app.websocket("/api/qq/ws")
async def qq_ws(ws: WebSocket) -> None:
    """插件桥连接：桥接 → 插件(qq_message)，插件 → 桥接(reply → 发 QQ)。"""
    global _qq_ws_conn
    await ws.accept()
    async with _qq_ws_lock:
        old = _qq_ws_conn
        _qq_ws_conn = ws
    if old is not None:
        try:
            await old.close()
        except Exception:
            pass
    try:
        while True:
            raw = await ws.receive_json()
            if not isinstance(raw, dict):
                continue
            if raw.get("type") == "reply":
                text = str(raw.get("text") or "").strip()
                if text:
                    qq = CONFIG.get("qq", {})
                    if qq.get("enabled"):
                        try:
                            # 先发原始文本，再发 TTS 语音（复用 /api/qq/send 逻辑）
                            resp_text = await qq_send(QQSendRequest(text=text, voice=False))
                            resp_voice = await qq_send(QQSendRequest(text=text, voice=True))
                            await ws.send_json({"type": "sent", "ok": True, "text": resp_text, "voice": resp_voice})
                        except HTTPException as exc:
                            await ws.send_json({"type": "sent", "ok": False, "detail": exc.detail})
                    else:
                        await ws.send_json({"type": "sent", "ok": False, "detail": "QQ push disabled"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("QQ ws error")
    finally:
        async with _qq_ws_lock:
            if _qq_ws_conn is ws:
                _qq_ws_conn = None


# ── Silero VAD endpoint (barge-in detection) ──────────────────────────────
#
# The original speech-to-speech project runs VAD on the SERVER with silero-vad,
# a neural network trained to tell a real human voice apart from noise / music /
# TTS echo. Our browser-side RMS threshold cannot do that, which is why ambient
# sounds kept tripping the barge-in and got STT'd into phantom messages.
#
# /api/vad is a WebSocket: while a reply is playing the client streams its mic
# PCM16 chunks here; the server runs them through silero VAD (loaded from the
# local <repo>/models/silero-vad/ directory, NOT the torch hub cache) and
# replies {"event":"speech_start"} only when a real voice is heard — the
# client then interrupts the reply. Chunks are never stored.

class VADSession:
    """One silero VAD session per WebSocket connection.

    Loads silero_vad_v4.jit (stable, no annotator) from <repo>/models/, falling
    back to silero_vad.jit if the v4 file is absent. State (h/c) lives in the
    jit model instance, so each session gets a fresh detector.
    """

    def __init__(self) -> None:
        import torch
        from speech_to_speech.VAD.vad_iterator import VADIterator

        models_dir = HERE / "models" / "silero-vad"
        model_path = models_dir / "silero_vad_v4.jit"
        if not model_path.is_file():
            model_path = models_dir / "silero_vad.jit"
        if not model_path.is_file():
            raise RuntimeError(f"silero-vad model not found under {models_dir}")

        self.model = torch.jit.load(str(model_path), map_location="cpu")
        self.model.eval()
        self.iterator = VADIterator(
            self.model,
            threshold=0.6,
            sampling_rate=16000,
            min_silence_duration_ms=64,
            speech_pad_ms=30,
        )
        self.min_speech_ms = 384
        self.speech_started = False
        # Byte buffer: client chunks (any size) accumulate until a full
        # 512-sample window is available — silero gets CONTINUOUS audio, never
        # zero-padded frames (padding between real audio breaks VAD state).
        self._buf = b""

    def feed(self, pcm16: bytes) -> list[dict]:
        """Feed one 16 kHz PCM16 chunk (any size); returns outbound JSON events.

        Silero VAD requires fixed 512-sample windows at 16 kHz; chunks are
        buffered and cut into 512-sample frames so the audio stream stays
        contiguous. A barge-in fires once sustained speech reaches
        min_speech_ms (384ms) — the same confirmation the original project
        applies. VADAudio outputs (final utterances) are intentionally ignored
        here — this endpoint only signals barge-in timing; the client keeps
        its own capture for STT.
        """
        import numpy as np
        import torch

        self._buf += pcm16
        out: list[dict] = []
        while len(self._buf) >= 1024:  # 512 int16 samples = 1024 bytes
            window = self._buf[:1024]
            self._buf = self._buf[1024:]
            x = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
            utterance = self.iterator(torch.from_numpy(x))
            if self.iterator.triggered and not self.speech_started:
                active_ms = self.iterator.active_speech_samples / 16.0
                if active_ms >= self.min_speech_ms:
                    self.speech_started = True
                    out.append({"event": "speech_start"})
            if utterance is not None:
                self.speech_started = False
                out.append({"event": "speech_end"})
        return out


@app.websocket("/api/vad")
async def vad_endpoint(ws: WebSocket) -> None:
    """Streaming barge-in VAD. Client pushes raw 16 kHz mono PCM16 (any chunk
    size, ~40ms typical); server replies speech_start/speech_end JSON when
    silero VAD hears human speech."""
    await ws.accept()
    session = VADSession()
    try:
        while True:
            data = await ws.receive_bytes()
            if not data:
                continue
            for msg in session.feed(data):
                await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("VAD websocket error")
        try:
            await ws.close()
        except Exception:
            pass
