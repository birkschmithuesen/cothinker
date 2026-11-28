"""Deterministic packet-gap injection for synthetic call audio.

A real call sometimes delivers runs of exact zero samples inside speech
(dropped packets). `GapProfile` describes such gaps by a measured
distribution (see `baseline.yaml`, `gap_profiles`); `inject_gaps` writes
them into 16-bit mono PCM from a seed, so the same input and seed always
give byte-identical output. Gaps are placed only between voiced frames and
never touch silent or edge regions.
"""
from __future__ import annotations

import array
import math
import random
import sys
from dataclasses import dataclass
from statistics import NormalDist

FRAME_S = 0.1
VOICED_RMS = 300.0
GAP_MIN_S = 0.2
GAP_MAX_S = 3.0
_Z90 = NormalDist().inv_cdf(0.9)
# Frames kept untouched between two injected gaps, so they never merge.
_SEPARATION_FRAMES = 2


@dataclass(frozen=True)
class GapProfile:
    p50_s: float
    p90_s: float
    runs_per_voiced_min: float

    @classmethod
    def from_baseline(cls, entry: dict) -> "GapProfile":
        return cls(float(entry["p50_s"]), float(entry["p90_s"]),
                   float(entry["runs_per_voiced_min"]))

    def draw(self, rng: random.Random) -> float:
        """One gap length: log-normal through p50/p90, clipped to 0.2..3.0 s."""
        mu = math.log(self.p50_s)
        sigma = (math.log(self.p90_s) - mu) / _Z90
        return min(GAP_MAX_S, max(GAP_MIN_S, rng.lognormvariate(mu, sigma)))


def _frame_len(rate: int, frame_s: float) -> int:
    return max(1, int(round(rate * frame_s)))


def voiced_frames(a, rate: int, *, frame_s: float = FRAME_S,
                  voiced_rms: float = VOICED_RMS) -> list[bool]:
    """One flag per whole `frame_s` frame: its RMS is above `voiced_rms`."""
    n = _frame_len(rate, frame_s)
    flags = []
    for start in range(0, len(a) - n + 1, n):
        acc = 0
        for x in a[start:start + n]:
            acc += x * x
        flags.append(math.sqrt(acc / n) > voiced_rms)
    return flags


def _to_samples(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def inject_gaps_counted(pcm: bytes, rate: int, profile: GapProfile,
                        seed: int) -> tuple[bytes, int]:
    """Like `inject_gaps`, also returning how many gaps were written."""
    a = _to_samples(pcm)
    voiced = voiced_frames(a, rate)
    n = _frame_len(rate, FRAME_S)
    want = round(profile.runs_per_voiced_min * sum(voiced) * FRAME_S / 60.0)
    rng = random.Random(seed)
    taken: list[tuple[int, int]] = []  # frame ranges [first, last] incl. margins
    placed = 0
    for _ in range(want * 50):
        if placed >= want:
            break
        length = max(1, int(round(profile.draw(rng) * rate)))
        first = rng.randrange(1, max(2, len(voiced)))
        # last frame that holds gap samples, and the voiced frame after it
        last = (first * n + length - 1) // n
        after = last + 1
        if after >= len(voiced):
            continue
        if not all(voiced[first - 1:after + 1]):
            continue
        lo, hi = first - 1 - _SEPARATION_FRAMES, after + _SEPARATION_FRAMES
        if any(not (hi < t0 or lo > t1) for t0, t1 in taken):
            continue
        taken.append((first - 1, after))
        for i in range(first * n, first * n + length):
            a[i] = 0
        placed += 1
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes() + pcm[len(pcm) - len(pcm) % 2:], placed


def inject_gaps(pcm: bytes, rate: int, profile: GapProfile, seed: int) -> bytes:
    """Zero `profile.draw()` seconds at seeded positions between voiced frames.

    The count is `round(runs_per_voiced_min * voiced_minutes)`; fewer gaps are
    written only if the speech has no room for more. Byte-identical for equal
    inputs and seed.
    """
    return inject_gaps_counted(pcm, rate, profile, seed)[0]
