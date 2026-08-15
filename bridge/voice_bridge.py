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
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
# Repo root: this file lives in <repo>/bridge/, so relative paths in
# bridge-config.json are resolved against the repo root (e.g. media dirs,
# ref_audio.wav). Absolute paths pass through untouched.
REPO_ROOT = HERE.parent
CONFIG_PATH = HERE / "bridge-config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("voice_bridge")


def _resolve_path(value: str) -> str:
    """Resolve a relative path against the repo root; absolute paths unchanged."""
    path = Path(value)
    if path.is_absolute():
        return value
    return str((REPO_ROOT / path).resolve())


def load_config() -> dict:
    """Load bridge-config.json and normalize relative paths.

    Only TTS model / ref audio / media directories are resolved (they point
    at local files). The STT model_name stays untouched — it is a HuggingFace
    model id (e.g. openai/whisper-large-v3) and must NOT be path-resolved.
    """
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    tts = cfg.setdefault("tts", {})
    if tts.get("model_name"):
        tts["model_name"] = _resolve_path(tts["model_name"])
    if tts.get("ref_audio"):
        tts["ref_audio"] = _resolve_path(tts["ref_audio"])

    media = cfg.setdefault("media", {})
    for key in ("bg_images_dir", "task_videos_dir"):
        if media.get(key):
            media[key] = _resolve_path(media[key])

    return cfg


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
    """Instantiate WhisperSTTHandler with bridge-config.json['stt'] settings."""
    from queue import Empty, Queue
    from threading import Event

    from speech_to_speech.STT.whisper_stt_handler import WhisperSTTHandler

    cfg = dict(CONFIG["stt"])
    handler = WhisperSTTHandler(
        Event(),
        queue_in=Queue(),
        queue_out=Queue(),
        setup_args=(),
        setup_kwargs=cfg,
    )
    return handler


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
    """Idle/background media list (videos + images, name-sorted)."""
    return {"media": _list_media(BG_IMAGES_DIR)}


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
if BG_IMAGES_DIR.is_dir():
    app.mount("/media/bg-images", StaticFiles(directory=str(BG_IMAGES_DIR)), name="media-bg")
if TASK_VIDEOS_DIR.is_dir():
    app.mount("/media/task-videos", StaticFiles(directory=str(TASK_VIDEOS_DIR)), name="media-task")
