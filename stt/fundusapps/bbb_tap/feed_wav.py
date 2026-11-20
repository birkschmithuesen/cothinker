"""Stream a 16 kHz mono WAV into the STT server's tcp: input in real time.

The fallback proof when no BBB room is available, and the replay path
for bench runs: the server sees exactly what it would see from the tap.
"""
import argparse
import json
import sys
import time
import wave

from fundusapps.bbb_tap.pcm import PcmSink

BLOCK = 2048  # 128 ms at 16 kHz, the tap's chunk size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav")
    ap.add_argument("--pcm", default="tcp:127.0.0.1:5053")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="1.0 = real time; tests use more")
    ap.add_argument("--lead-silence", dest="lead_silence", type=float, default=1.0,
                    help="seconds of silence before the file, so the VAD sees a floor")
    a = ap.parse_args(argv)
    with wave.open(a.wav) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            print("need 16 kHz mono 16-bit WAV", file=sys.stderr)
            return 2
        pcm = w.readframes(w.getnframes())
    sink = PcmSink(a.pcm, reconnect_s=0.2)
    lead = b"\x00\x00" * int(16000 * a.lead_silence)
    data = lead + pcm
    step = BLOCK * 2
    t0 = time.monotonic()
    print(json.dumps({"event": "feed_start", "wall": time.time(),
                      "lead_silence": a.lead_silence}), flush=True)
    for i in range(0, len(data), step):
        due = t0 + (i / 2 / 16000) / a.speed
        delay = due - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        while not sink.send(data[i:i + step]):
            time.sleep(0.05)
    sink.close()
    print(json.dumps({"event": "feed_end", "wall": time.time()}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
