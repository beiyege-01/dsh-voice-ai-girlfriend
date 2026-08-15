"""STT smoke test for the voice bridge (T2 verification).

Reads the Xiaoya reference wav (real Chinese speech), resamples to 16 kHz
mono float32, sends it to POST /api/stt, and prints the result.
Usage:
  venv-speech\\Scripts\\python.exe smoke_stt.py [--file path] [--max-sec 120] [--format raw|wav]
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
    ap.add_argument("--file", default=r"C:\Users\wrpsg\Music\小雅台版).wav")
    ap.add_argument("--url", default="http://127.0.0.1:8765/api/stt")
    ap.add_argument("--max-sec", type=float, default=120.0)
    ap.add_argument("--format", choices=["raw", "wav"], default="raw")
    args = ap.parse_args()

    import soundfile as sf
    from scipy.signal import resample_poly

    data, sr = sf.read(args.file, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        gcd = int(np.gcd(sr, 16000))
        data = resample_poly(data, up=16000 // gcd, down=sr // gcd)
    pcm16 = np.clip(data * 32768, -32768, 32767).astype(np.int16)

    if args.format == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm16.tobytes())
        body = buf.getvalue()
        content_type = "audio/wav"
    else:
        body = pcm16.tobytes()
        content_type = "application/octet-stream"
    print(f"input: {Path(args.file).name}, {len(pcm16)/16000:.1f}s @ 16kHz mono PCM16, format={args.format} ({len(body)} bytes)")

    req = urllib.request.Request(
        args.url,
        data=body,
        headers={
            "Content-Type": content_type,
            "X-Max-Audio-Sec": str(args.max_sec),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    print("RESULT:", json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
