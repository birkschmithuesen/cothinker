#!/usr/bin/env python3
# CoThinker — cut a measurement slice out of a vault recording
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Read a byte range out of the raw recording and write it as a WAV.

The source file lives in the vault and is never copied in full: a
`rclone cat --offset/--count` reads exactly the 20 minutes we measure,
straight into a fresh WAV header. The slice lands in $MESS_DIR, never in
the repo.

    python3 slice_audio.py --offset-s 2400 --duration-s 1200 \\
        --remote hermes-vault:<pfad>.wav --out $MESS_DIR/audio/slice.wav

Why not ffmpeg: the server has none, and for 16 kHz mono PCM the header
is 44 bytes of arithmetic anyway.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import wave

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPWIDTH = 2
BYTE_RATE = SAMPLE_RATE * CHANNELS * SAMPWIDTH   # 32000 bytes per second
HEADER_BYTES = 44


def slice_range(offset_s: float, duration_s: float,
                data_start: int = HEADER_BYTES,
                byte_rate: int = BYTE_RATE) -> tuple[int, int]:
    """Byte offset and byte count for a time range in a headered PCM file."""
    if offset_s < 0 or duration_s <= 0:
        raise ValueError("offset must be >= 0 and duration > 0")
    return data_start + int(offset_s * byte_rate), int(duration_s * byte_rate)


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE,
              channels: int = CHANNELS, sampwidth: int = SAMPWIDTH) -> bytes:
    """Wrap raw PCM in a WAV container."""
    if len(pcm) % (channels * sampwidth):
        raise ValueError("pcm length is not a whole number of frames")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", required=True,
                    help="rclone path of the source recording")
    ap.add_argument("--offset-s", type=float, required=True)
    ap.add_argument("--duration-s", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    offset, count = slice_range(a.offset_s, a.duration_s)
    r = subprocess.run(
        ["rclone", "cat", "--offset", str(offset), "--count", str(count),
         a.remote],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-2000:])
        return 1
    if len(r.stdout) != count:
        sys.stderr.write(f"short read: {len(r.stdout)} of {count} bytes\n")
        return 1
    with open(a.out, "wb") as f:
        f.write(wav_bytes(r.stdout))
    print(f"{a.out} {a.duration_s:.1f}s {len(r.stdout)} pcm bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
