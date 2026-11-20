#!/usr/bin/env python3
# CoThinker — replay a Pegeltest recording through the "move closer" rule
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Runs a pegeltest WAV recording (mono PCM16) through the SAME rule the
table mic page ships: workshop/static/vad.js's EnergyVad for the noise floor
and speaking state, workshop/static/closer-warning.js's CloserWarning for the
SNR/hold/hide hint. A faithful stdlib-only port of both (no Node on the
deploy target) -- tests/test_pegeltest_replay.py pins its numbers against the
same boundary cases as tests/test_workshop_js.py's node harness.

Frames at 10 ms (matching vad.js's default frameMs) and evaluates the hint
every 10th frame (100 ms), exactly like workshop/static/audio-worklet.js's
decimation -- the recording sees the same timing the real page would.

    python3 scripts/pegeltest-replay.py *.wav
    python3 scripts/pegeltest-replay.py --closer-snr-db 10 --closer-hold-s 2 *.wav

Offline replay of the 27.10. pegeltest recordings before
shipping the SNR 12 dB / 3 s rule (approved 2026-10-28).
"""
from __future__ import annotations

import argparse
import math
import statistics
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

FRAME_MS = 10
LEVEL_MS = 100


def frame_db(samples: List[float]) -> float:
    if not samples:
        return -120.0
    ms = sum(s * s for s in samples) / len(samples)
    rms = math.sqrt(ms)
    return max(-120.0, 20 * math.log10(rms)) if rms > 0 else -120.0


@dataclass
class EnergyVad:
    """Port of workshop/static/vad.js EnergyVad. Keep in step with it by hand --
    tests/test_pegeltest_replay.py runs the same boundary cases as
    tests/test_workshop_js.py's VAD_HARNESS."""
    frame_ms: float = FRAME_MS
    on_db: float = 12.0
    off_db: float = 6.0
    floor_min_db: float = -100.0
    attack: int = 3
    hang_ms: float = 300.0
    floor_rise_ms: float = 2000.0
    floor_init_db: float = -70.0
    floor: float = field(init=False)
    speaking: bool = field(init=False, default=False)
    loud_run: int = field(init=False, default=0)
    quiet_ms: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.floor = self.floor_init_db

    def push(self, db: float) -> None:
        # Floor derived from non-speech frames only: frozen while speaking.
        if not self.speaking:
            if db < self.floor:
                self.floor = db
            else:
                self.floor += (db - self.floor) * (self.frame_ms / self.floor_rise_ms)
            self.floor = max(self.floor, self.floor_min_db)
        on = self.floor + self.on_db
        off = self.floor + self.off_db
        if not self.speaking:
            if db < on:
                self.loud_run = 0
                return
            self.loud_run += 1
            if self.loud_run < self.attack:
                return
            self.speaking = True
            self.quiet_ms = 0.0
            return
        if db >= off:
            self.quiet_ms = 0.0
            return
        self.quiet_ms += self.frame_ms
        if self.quiet_ms < self.hang_ms:
            return
        self.speaking = False
        self.loud_run = 0


CLOSER_ABS_DB = -51.0   # Xperia 40/80 cm anchor, pegeltest-2026-10-27-xperia5.json


@dataclass
class CloserWarning:
    """Port of workshop/static/closer-warning.js CloserWarning (v2):
    a robust speech level (percentile over the last level_window_ms of SPEAKING frames)
    compared against the frozen floor, with belowMs/aboveMs accumulating over speaking
    time only -- a pause freezes them, it never resets them."""
    snr_db: float = 12.0
    hold_s: float = 3.0
    hide_s: float = 1.0
    level_window_ms: float = 2000.0
    level_percentile: float = 80.0
    abs_db: float = CLOSER_ABS_DB
    mode: str = "snr"
    warning: bool = field(init=False, default=False)
    below_ms: float = field(init=False, default=0.0)
    above_ms: float = field(init=False, default=0.0)
    window: list = field(init=False, default_factory=list)   # [(db, dt_ms), ...], speaking frames only
    level: float | None = field(init=False, default=None)
    snr: float | None = field(init=False, default=None)

    def _push_level(self, db: float, dt_ms: float) -> None:
        self.window.append((db, dt_ms))
        total = sum(dt for _, dt in self.window)
        while total > self.level_window_ms and len(self.window) > 1:
            total -= self.window[0][1]
            self.window.pop(0)
        levels = sorted(db for db, _ in self.window)
        idx = min(len(levels) - 1, int(self.level_percentile / 100 * len(levels)))
        self.level = levels[idx]

    def push(self, speaking: bool, db: float, floor_db: float, dt_ms: float) -> bool:
        if not speaking:
            return self.warning   # a pause freezes everything; it is never a reset
        self._push_level(db, dt_ms)
        self.snr = self.level - floor_db
        below = self.level < self.abs_db if self.mode == "abs" else self.snr < self.snr_db
        if below:
            self.below_ms += dt_ms
            self.above_ms = 0.0
            if self.below_ms >= self.hold_s * 1000:
                self.warning = True
        else:
            self.above_ms += dt_ms
            self.below_ms = 0.0
            if self.above_ms >= self.hide_s * 1000:
                self.warning = False
        return self.warning


def read_mono_s16(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM, got {w.getsampwidth() * 8}-bit")
        rate, nchan, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    ints = struct.unpack(f"<{len(raw) // 2}h", raw)
    if nchan > 1:
        ints = ints[::nchan]   # channel 0 only
    return [v / 32768.0 for v in ints], rate


def replay(path: Path, closer_opts: dict) -> dict:
    samples, rate = read_mono_s16(path)
    frame_n = max(1, round(rate * FRAME_MS / 1000))
    vad = EnergyVad(frame_ms=frame_n / rate * 1000)
    closer = CloserWarning(**closer_opts)
    level_frames = max(1, round(LEVEL_MS / (frame_n / rate * 1000)))
    frame_count = 0
    speaking_dt = warning_dt = total_dt = 0.0
    dt_ms = frame_n / rate * 1000 * level_frames
    floor_samples: list[float] = []   # vad.floor at each speaking tick -- the frozen room floor
    level_samples: list[float] = []   # closer.level at each speaking tick -- the robust speech level
    i = 0
    while i < len(samples):
        frame = samples[i:i + frame_n]
        i += frame_n
        db = frame_db(frame)
        vad.push(db)
        frame_count += 1
        if frame_count >= level_frames:
            frame_count = 0
            warning = closer.push(vad.speaking, db, vad.floor, dt_ms)
            total_dt += dt_ms
            if vad.speaking:
                speaking_dt += dt_ms
                floor_samples.append(vad.floor)
                level_samples.append(closer.level)
                if warning:
                    warning_dt += dt_ms
    floor_dbfs = statistics.median(floor_samples) if floor_samples else None
    level_dbfs = statistics.median(level_samples) if level_samples else None
    return {
        "file": path.name,
        "dur_s": round(len(samples) / rate, 1),
        "speech_s": round(speaking_dt / 1000, 1),
        "pct_warning_of_speech": round(100 * warning_dt / speaking_dt, 1) if speaking_dt else None,
        "pct_warning_of_total": round(100 * warning_dt / total_dt, 1) if total_dt else None,
        # Median over speaking time -- the frozen floor, the robust speech level and their
        # difference, as named columns for the verification table (not an
        # input to the rule itself, which already computes these per frame).
        "floor_dbfs": round(floor_dbfs, 1) if floor_dbfs is not None else None,
        "level_dbfs": round(level_dbfs, 1) if level_dbfs is not None else None,
        "snr_db": round(level_dbfs - floor_dbfs, 1) if floor_dbfs is not None and level_dbfs is not None else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wavs", nargs="+", type=Path)
    ap.add_argument("--closer-snr-db", type=float, default=12.0)
    ap.add_argument("--closer-hold-s", type=float, default=3.0)
    ap.add_argument("--closer-hide-s", type=float, default=1.0)
    ap.add_argument("--closer-level-window-ms", type=float, default=2000.0)
    ap.add_argument("--closer-level-percentile", type=float, default=80.0)
    ap.add_argument("--closer-abs-db", type=float, default=CLOSER_ABS_DB)
    ap.add_argument("--closer-mode", choices=("snr", "abs"), default="snr")
    a = ap.parse_args()
    opts = {"snr_db": a.closer_snr_db, "hold_s": a.closer_hold_s, "hide_s": a.closer_hide_s,
            "level_window_ms": a.closer_level_window_ms, "level_percentile": a.closer_level_percentile,
            "abs_db": a.closer_abs_db, "mode": a.closer_mode}
    rows = [replay(p, opts) for p in a.wavs]
    w = max(len(r["file"]) for r in rows)
    print(f"{'file':<{w}}  dur_s  speech_s  floor_dBFS  level_dBFS  snr_dB  %warning/speech  %warning/total")
    for r in rows:
        fl = "–" if r["floor_dbfs"] is None else f"{r['floor_dbfs']:.1f}"
        lv = "–" if r["level_dbfs"] is None else f"{r['level_dbfs']:.1f}"
        sn = "–" if r["snr_db"] is None else f"{r['snr_db']:.1f}"
        pw_s = "–" if r["pct_warning_of_speech"] is None else f"{r['pct_warning_of_speech']:.1f}"
        pw_t = "–" if r["pct_warning_of_total"] is None else f"{r['pct_warning_of_total']:.1f}"
        print(f"{r['file']:<{w}}  {r['dur_s']:>5}  {r['speech_s']:>8}  {fl:>10}  {lv:>10}  {sn:>6}  "
              f"{pw_s:>15}  {pw_t:>14}")


if __name__ == "__main__":
    main()
