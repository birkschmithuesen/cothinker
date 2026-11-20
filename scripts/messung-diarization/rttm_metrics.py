#!/usr/bin/env python3
# CoThinker — deterministic metrics over a diarization RTTM file
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Turn an RTTM file into the numbers the report quotes.

Pure functions over timings and generic labels -- no audio, no text, no
model. That is deliberate: the plausibility question is answered by the
judge (judge_client.py), the arithmetic here has to be reproducible from
the RTTM alone.

    python3 rttm_metrics.py $MESS_DIR/diar/default.rttm --audio-s 1200
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    start: float
    dur: float
    speaker: str

    @property
    def end(self) -> float:
        return self.start + self.dur


def parse_rttm(text: str) -> list[Segment]:
    """Read RTTM `SPEAKER` lines. Other types and comments are ignored."""
    segs: list[Segment] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        f = line.split()
        if len(f) < 8 or f[0] != "SPEAKER":
            continue
        segs.append(Segment(float(f[3]), float(f[4]), f[7]))
    return segs


def speaker_totals(segs: list[Segment]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for s in segs:
        totals[s.speaker] = totals.get(s.speaker, 0.0) + s.dur
    return totals


def speaker_count(segs: list[Segment],
                  min_speech_s: float = 5.0) -> tuple[int, int]:
    """(labels with at least min_speech_s of speech, all labels)."""
    totals = speaker_totals(segs)
    return sum(1 for v in totals.values() if v >= min_speech_s), len(totals)


def switches(segs: list[Segment]) -> int:
    """Speaker changes along the timeline, repeats collapsed."""
    folge = [s.speaker for s in sorted(segs, key=lambda s: (s.start, s.end))]
    kollabiert = [k for i, k in enumerate(folge) if i == 0 or k != folge[i - 1]]
    return max(0, len(kollabiert) - 1)


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def speech_time(segs: list[Segment]) -> float:
    return sum(b - a for a, b in _union([(s.start, s.end) for s in segs]))


def overlap_time(segs: list[Segment]) -> float:
    """Time covered by two or more segments at once."""
    punkte: list[tuple[float, int]] = []
    for s in segs:
        punkte.append((s.start, 1))
        punkte.append((s.end, -1))
    punkte.sort()
    offen = 0
    letzte = 0.0
    summe = 0.0
    for t, d in punkte:
        if offen >= 2:
            summe += t - letzte
        offen += d
        letzte = t
    return summe


def segment_median(segs: list[Segment]) -> float:
    return statistics.median([s.dur for s in segs]) if segs else 0.0


def metrics(segs: list[Segment], audio_s: float) -> dict:
    gefiltert, roh = speaker_count(segs)
    sprech = speech_time(segs)
    ueber = overlap_time(segs)
    return {
        "segments": len(segs),
        "speakers_filtered": gefiltert,
        "speakers_raw": roh,
        "switches": switches(segs),
        "switches_per_min": switches(segs) / (audio_s / 60.0) if audio_s else 0.0,
        "speech_s": sprech,
        "speech_share": sprech / audio_s if audio_s else 0.0,
        "overlap_s": ueber,
        "overlap_share": ueber / sprech if sprech else 0.0,
        "segment_median_s": segment_median(segs),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rttm")
    ap.add_argument("--audio-s", type=float, required=True)
    a = ap.parse_args()
    with open(a.rttm, encoding="utf-8") as f:
        segs = parse_rttm(f.read())
    print(json.dumps(metrics(segs, a.audio_s), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
