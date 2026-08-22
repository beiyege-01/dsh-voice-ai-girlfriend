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


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()

app = FastAPI(title="voice-bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.get("cors_origins", ["http://127.0.0.1:3080"]),
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
            handler = await models.ensure_tts()
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
    """Run the Qwen3 TTS handler for one utterance, concatenating int16 chunks.

    Stops early between chunks when `cancel` is set (client disconnect)."""
    from speech_to_speech.pipeline.messages import TTSInput

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
DH_ENABLED = bool(DH_CFG.get("enabled", False))
DH_DUIX_BASE = DH_CFG.get("duix_base", "http://127.0.0.1:8383").rstrip("/")
DH_DATA_DIR = Path(DH_CFG.get("data_dir", "D:/duix_avatar_data/face2face"))
DH_TEMP_DIR = DH_DATA_DIR / DH_CFG.get("temp_dir", "temp")
# 生成产物子目录：数字人成品视频（<uuid>-r.mp4）统一放这里，与 temp 根目录的
# 形象素材 / 输入音频分开。形象文件（avatar*.mp4 / 自定义名.mp4）仍在 temp 根。
DH_OUTPUT_DIR = DH_TEMP_DIR / "output"
DH_AVATAR = DH_CFG.get("avatar_video", "")
DH_SUBMIT_RETRY_SEC = float(DH_CFG.get("submit_retry_sec", 5))
DH_QUERY_INTERVAL = float(DH_CFG.get("query_interval_sec", 2))
DH_QUERY_TIMEOUT = float(DH_CFG.get("query_timeout_sec", 240))
DH_MAX_KEEP = int(DH_CFG.get("max_keep", 10))
# 每段音频的文本上限：~48 字 ≈ 8~10s 音频（用户实测 10s 视频十几秒出片）
DH_SEGMENT_CHARS = int(DH_CFG.get("segment_chars", 48))
DH_MAX_TEXT = 1000

# ── TTS 音色预设（统一从 voices 大文件夹下子文件夹读取）────────────────────
# 约定：VOICES_DIR（voices）下的每个【子文件夹】= 一个音色，文件夹名即音色名；
# 子文件夹内放 ref_audio.wav（参考音频）+ ref_text.txt（对应文本，UTF-8）。
# 新增音色：在 voices 下新建子文件夹放这两个文件即可，启动时自动扫描。
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
            text = next((f for f in entry.iterdir() if f.suffix.lower() in TEXT_EXTS), None)
            if audio is None:
                continue  # 无参考音频的子文件夹跳过
            ref_text = text.read_text(encoding="utf-8", errors="ignore").strip() if text is not None else ""
            voices[name] = {
                "label": name,
                "ref_audio": str(audio),
                "ref_text": ref_text,
            }
    return voices


PERSONAS: dict[str, dict] = _scan_voices()
# 默认音色：用户当前在用的（可改；不存在时回退到扫描到的第一个音色）。
_default_voice = "xiaoya-hunan" if "xiaoya-hunan" in PERSONAS else next(iter(PERSONAS), "")
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
    if not DH_ENABLED:
        raise HTTPException(status_code=400, detail="digital_human disabled in bridge-config.json")
    text = (req.text or "").strip()
    if not text:
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
    return _dh_get()


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
                    return
                _dh_set(
                    state="generating",
                    message=f"数字人生成中 {i + 1}/{total}…",
                    code=code, text=text, done_segments=i,
                    progress=round(i / total * 100),
                )
                # 当前段音频：优先用流水线预合成好的；否则现合成
                if pending_synth is not None:
                    wav_path = await pending_synth
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
                if next_synth is not None:
                    try:
                        await next_synth
                    except Exception:  # noqa: BLE001
                        logger.exception("DH %s: next synth failed", code)
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
        handler = await models.ensure_tts()
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
    for _ in range(180):  # 最多等 15 分钟 busy 释放
        try:
            resp = await _dh_submit(client, payload)
        except Exception:  # noqa: BLE001
            logger.exception("DH %s: submit http failed", seg_code)
            await asyncio.sleep(DH_SUBMIT_RETRY_SEC)
            continue
        if resp.get("code") == 10000:
            submitted = True
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
    新任务开始时才清空旧结果（见 _dh_run 开头）。"""
    while True:
        item = _dh_pop_next()
        if item is None:
            await asyncio.sleep(1.0)
            continue
        try:
            await _dh_run(item)
        except Exception:  # noqa: BLE001
            logger.exception("DH worker crashed on %s", item.get("code"))
            _dh_set(state="error", message="worker 异常", code=item.get("code", ""), text=item.get("text", ""))


@app.on_event("startup")
async def _dh_start_worker() -> None:
    if DH_ENABLED:
        _dh_prune_videos()  # 启动时把成品视频修剪到最近 max_keep 个
        asyncio.create_task(_dh_worker())
        # 启动预热：合成一段短音频并提交 DUIX，让 wenet 特征提取 / init_wh /
        # 模型加载 / TRT engine 全部走一遍热路径，首条真实回复的等待时间
        # 显著缩短。预热任务独立于 _dh_queue 运行，结果直接丢弃。
        asyncio.create_task(_dh_warmup())
        logger.info("DH worker started (DUIX %s, temp=%s, avatar=%s, max_keep=%d)", DH_DUIX_BASE, DH_TEMP_DIR, DH_AVATAR, DH_MAX_KEEP)


async def _dh_warmup() -> None:
    """后台预热：跑一次完整的「TTS → 特征 → init_wh → 生成」链路。

    预热输出是「数字人没有说话」的占位短句（或仅预合成音频 + 提交），
    完成即删，不进入播放列表、不污染 max_keep 历史。
    """
    try:
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
            for _ in range(60):  # 最多等 5 分钟 busy 释放
                try:
                    resp = await _dh_submit(client, payload)
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(DH_SUBMIT_RETRY_SEC)
                    continue
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
if DH_ENABLED and DH_TEMP_DIR.is_dir():
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
                handler = await models.ensure_tts()
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
