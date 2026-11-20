#!/usr/bin/env python3
# CoThinker — time and measure exactly one child process
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Wall clock, CPU seconds and peak RSS of one command, as JSON.

Stands in for `/usr/bin/time -v`, which this server does not have, and
for cgroup v2 `memory.peak`, which kernel 5.10 does not expose.

    python3 timed_run.py --json $MESS_DIR/runs/default-1.json -- \\
        nemo-speech diarize $MESS_DIR/audio/slice.wav ...

IMPORTANT: `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` reports the
largest resident set of ANY child this process ever had. The CLI therefore
starts exactly one child and exits -- do not extend it to run several
commands in a row, the memory figure would silently become the maximum of
all of them.
"""
from __future__ import annotations

import argparse
import json
import resource
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass


@dataclass
class RunResult:
    argv: list
    rc: int
    wall_s: float
    cpu_user_s: float
    cpu_sys_s: float
    maxrss_kb: int


def run_timed(argv: list, cwd: str | None = None) -> RunResult:
    vorher = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    rc = subprocess.call(argv, cwd=cwd)
    wall = time.monotonic() - t0
    nachher = resource.getrusage(resource.RUSAGE_CHILDREN)
    return RunResult(
        argv=list(argv),
        rc=rc,
        wall_s=wall,
        cpu_user_s=nachher.ru_utime - vorher.ru_utime,
        cpu_sys_s=nachher.ru_stime - vorher.ru_stime,
        # not a delta on purpose: ru_maxrss is a maximum, not a counter.
        maxrss_kb=int(nachher.ru_maxrss),
    )


def rtf(wall_s: float, audio_s: float) -> float:
    """Real-time factor: compute seconds per second of audio."""
    if audio_s <= 0:
        raise ValueError("audio length must be > 0")
    return wall_s / audio_s


def summarize(values: list) -> dict:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(values), "min": min(values),
            "median": statistics.median(values), "max": max(values)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="where to write the result")
    ap.add_argument("--audio-s", type=float, default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        sys.stderr.write("no command given (use -- before the command)\n")
        return 2
    r = run_timed(cmd)
    out = asdict(r)
    out["label"] = a.label
    if a.audio_s:
        out["rtf"] = rtf(r.wall_s, a.audio_s)
        out["audio_s"] = a.audio_s
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print(json.dumps({k: out[k] for k in
                      ("label", "rc", "wall_s", "cpu_user_s", "cpu_sys_s",
                       "maxrss_kb") if k in out}))
    return r.rc


if __name__ == "__main__":
    raise SystemExit(main())
