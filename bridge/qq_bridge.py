# -*- coding: utf-8 -*-
"""QQ 推送模块：把文本 / TTS 语音发送到指定 QQ（经 NapCat OneBot v11）。

- 发文本：POST {napcat}/send_private_msg { user_id, message }
- 发语音：TTS wav(16k mono) → pilk 编码 silk → record 消息（QQ 原生语音格式）
- 鉴权：Authorization: Bearer {napcat_token}

依赖：pilk（pip install pilk）、ffmpeg 非必需（pilk 直接吃 wav 的 PCM）。
"""
from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

try:
    import pilk  # silk 编码
    HAS_PILK = True
except ImportError:  # pragma: no cover
    HAS_PILK = False


class QQPushError(RuntimeError):
    pass


def _call(base: str, token: str, action: str, payload: dict) -> dict:
    """OneBot v11 主动调用（POST {action}）。"""
    url = f"{base.rstrip('/')}/{action}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("status") != "ok":
        raise QQPushError(f"NapCat {action} failed: {result.get('message') or result.get('wording')}")
    return result


def send_text(base: str, token: str, user_id: int, text: str) -> dict:
    """发一条文本私聊消息。"""
    return _call(base, token, "send_private_msg", {"user_id": user_id, "message": text})


def send_voice(base: str, token: str, user_id: int, pcm16: bytes) -> dict:
    """把 16 kHz mono PCM16 编码成 silk 并作为语音消息发送。

    Args:
        pcm16: raw little-endian 16-bit PCM, 16 kHz, mono（bridge /api/tts 的输出）。
    """
    if not HAS_PILK:
        raise QQPushError("pilk 未安装：pip install pilk")
    audio = np.frombuffer(pcm16, dtype=np.int16)
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "voice.wav"
        silk_path = Path(tmp) / "voice.silk"
        _write_wav(wav_path, audio)
        pilk.encode(str(wav_path), str(silk_path), pcm_rate=16000, tencent=True)
        file_arg = str(silk_path)  # NapCat 读本地绝对路径
        message = [{"type": "record", "data": {"file": file_arg}}]
        return _call(base, token, "send_private_msg", {"user_id": user_id, "message": message})


def send_image(base: str, token: str, user_id: int, image_path: str) -> dict:
    """把本地图片作为图片消息发送（base64 内嵌，避免 NapCat 读路径失败）。"""
    if not Path(image_path).is_file():
        raise QQPushError(f"image not found: {image_path}")
    data = Path(image_path).read_bytes()
    b64 = __import__("base64").b64encode(data).decode("ascii")
    message = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
    return _call(base, token, "send_private_msg", {"user_id": user_id, "message": message})


def _write_wav(path: Path, pcm16: np.ndarray) -> None:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm16.astype("<i2").tobytes())
