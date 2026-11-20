#!/usr/bin/env python3
"""Slice a local WAV file by offset/duration (seconds) into a new WAV.
Local-only variant of slice_audio.py (no rclone needed: source is already
a local working copy, not a remote)."""
import argparse
import wave


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--offset-s", type=float, required=True)
    ap.add_argument("--duration-s", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with wave.open(a.src, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        start_frame = int(a.offset_s * sr)
        n_frames = int(a.duration_s * sr)
        w.setpos(start_frame)
        pcm = w.readframes(n_frames)

    with wave.open(a.out, "wb") as out:
        out.setnchannels(ch)
        out.setsampwidth(sw)
        out.setframerate(sr)
        out.writeframes(pcm)
    print(f"{a.out} {len(pcm)/(sr*ch*sw):.1f}s actual, requested {a.duration_s:.1f}s")


if __name__ == "__main__":
    raise SystemExit(main())
