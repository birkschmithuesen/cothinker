"""Packet-gap probe (M4): zero-sample runs inside speech on a call track.

usage: python -m demo.acceptance.gap_probe <wav>...

Prints numbers only: never audio content, never a path beyond the filename.
"""
from __future__ import annotations

import array
import math
import sys
import wave
from pathlib import Path

if __package__ in (None, ""):  # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo.acceptance.audio import voiced_frames  # noqa: E402


def zero_runs(samples, rate: int, *, zero_max: int = 1, voiced_rms: float = 300,
              frame_s: float = 0.1, within_s: float = 3.0, min_s: float = 0.2,
              max_s: float = 3.0) -> list[float]:
    """Lengths (s) of zero runs lying between two voiced frames.

    A zero run is consecutive samples with `|x| <= zero_max`. It counts only
    if it is `min_s..max_s` long, and a voiced frame (`rms > voiced_rms` over
    `frame_s`) ends within `within_s` before it starts and another starts
    within `within_s` after it ends.
    """
    n = max(1, int(round(rate * frame_s)))
    voiced = voiced_frames(samples, rate, frame_s=frame_s, voiced_rms=voiced_rms)
    voiced_idx = [i for i, v in enumerate(voiced) if v]
    if not voiced_idx:
        return []
    reach = int(within_s * rate)
    out: list[float] = []
    run_start = None
    total = len(samples)
    for i in range(total + 1):
        zero = i < total and -zero_max <= samples[i] <= zero_max
        if zero:
            if run_start is None:
                run_start = i
            continue
        if run_start is None:
            continue
        start, end, run_start = run_start, i, None
        length_s = (end - start) / rate
        if not (min_s <= length_s <= max_s):
            continue
        # nearest voiced frame fully before the run, and fully after it
        before = [j for j in voiced_idx if (j + 1) * n <= start]
        after = [j for j in voiced_idx if j * n >= end]
        if not before or not after:
            continue
        if start - (before[-1] + 1) * n > reach or after[0] * n - end > reach:
            continue
        out.append(length_s)
    return out


def _quantile(sorted_vals: list[float], q: float) -> float:
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def stats(runs: list[float], voiced_min: float) -> dict:
    vals = sorted(runs)
    return {
        "n": len(vals),
        "p50_s": round(_quantile(vals, 0.5), 3) if vals else None,
        "p90_s": round(_quantile(vals, 0.9), 3) if vals else None,
        "runs_per_voiced_min": round(len(vals) / voiced_min, 2) if voiced_min > 0 else 0.0,
    }


def _read_mono(path: Path):
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("16-bit PCM required")
        ch, rate = w.getnchannels(), w.getframerate()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        a.byteswap()
    return (a[::ch] if ch > 1 else a), rate


def main(argv: list[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print("usage: python -m demo.acceptance.gap_probe <wav>...", file=sys.stderr)
        return 2
    for p in paths:
        a, rate = _read_mono(Path(p))
        runs = zero_runs(a, rate)
        voiced_min = sum(voiced_frames(a, rate)) * 0.1 / 60.0
        st = stats(runs, voiced_min)
        print(f"{Path(p).name}: n={st['n']} p50_s={st['p50_s']} p90_s={st['p90_s']} "
              f"runs_per_voiced_min={st['runs_per_voiced_min']} "
              f"voiced_min={voiced_min:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
