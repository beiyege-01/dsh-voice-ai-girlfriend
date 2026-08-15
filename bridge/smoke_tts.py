"""TTS smoke test for the voice bridge (T3 verification).

Sends a Chinese sentence to POST /api/tts, saves the returned WAV to
tts_out.wav (play it to check the Xiaoya voice clone), and prints metadata.
Usage:
  venv-speech\\Scripts\\python.exe smoke_tts.py [--text "你好，我是小雅，很高兴认识你"] [--out tts_out.wav]
"""

import argparse
import io
import json
import sys
import urllib.request
import wave
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/api/tts")
    ap.add_argument("--text", default="你好，我是小雅，很高兴认识你。今天想聊点什么呢？")
    ap.add_argument("--out", default=r"D:\speech-to-speech\tts_out.wav")
    args = ap.parse_args()

    req = urllib.request.Request(
        args.url,
        data=json.dumps({"text": args.text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = resp.read()
        content_type = resp.headers.get("Content-Type", "")

    out = Path(args.out)
    out.write_bytes(body)
    print(f"HTTP {content_type}, {len(body)} bytes -> {out}")

    # Metadata: duration + RMS (sanity that it is not silence).
    with wave.open(io.BytesIO(body), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        ch = w.getnchannels()
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if ch > 1:
        samples = samples.reshape(-1, ch).mean(axis=1)
    rms = float(np.sqrt(np.mean(samples**2)))
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    print(f"audio: {n/sr:.2f}s @ {sr}Hz {ch}ch, RMS={rms:.4f}, peak={peak:.4f}")
    print("Play tts_out.wav to verify the Xiaoya voice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
