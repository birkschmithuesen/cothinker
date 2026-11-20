#!/usr/bin/env python3
# CoThinker -- extract block-cut structure from a real consumer log
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Turn a real `consumer.py` log into STRUCTURE, never content.

Reads a log produced by `gateway/consumer.py` (the `log(f"[{n}] {text[:80]}")`
and `log(f"  block #{...} ({b.reason}, {b.chars} chars)")` lines) and emits,
per utterance, exactly what a turn-end measurement needs and NOTHING else:

  - `index`      the STT `final` counter (`[n]`)
  - `pause_s`    wall-clock seconds until the NEXT `final` arrived -- this is
                 what `gateway/blocks.py` itself measures (arrival time, not
                 an acoustic timestamp), so it is faithful to what the
                 running system actually saw, latency included
  - `chars`      length of the truncated preview line (`text[:80]`) is NOT
                 what we want -- this script does not have the real length
                 either, unless it is given the block-log's own `chars`
                 field, which reports the BLOCK's cumulative length, not the
                 single utterance's. This script does NOT offer a flag for
                 per-utterance length; by default it emits UNKNOWN (`None`)
                 for chars. The real per-utterance character counts used in
                 `tests/turn_fixture_real.py` were taken by hand from the
                 (offline, never-committed) transcript, not from this
                 script's output.
  - `wake`       True if this index closed a `(wake, ...)` block

`turn_end` is NOT produced here -- "was the speaker done, or only
thinking?" is a semantic judgement call a human has to make by reading the
actual (offline, never-committed) transcript. This script's output is
therefore always safe to keep, print, or hand to another tool: it never
contains a word of transcript text.

Usage:
    python3 extract-session-fixture.py LOGFILE [--start HH:MM] [--end HH:MM]

Output: one JSON object per line (JSONL) to stdout, one per utterance in the
requested window. Feed this into a hand-annotated `Utterance` fixture
(`tests/turn_fixture_real.py`) -- text there MUST be a placeholder, per
AGENTS.md / the PII policy on this repo, never anonymized-but-still-real
transcript text.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict


UTTERANCE_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \[(\d+)\]")
BLOCK_RE = re.compile(
    r"^\[(\d\d):(\d\d):(\d\d)\]\s+block #(\d+) \((\w+), (\d+) chars\)")


@dataclass(frozen=True)
class Event:
    index: int
    time_s: int          # seconds since local midnight -- ordering only
    pause_s: float | None = None    # filled in a second pass
    wake: bool = False


def _hms(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


def extract(lines: list[str], start_s: int | None, end_s: int | None
            ) -> list[Event]:
    utterances: dict[int, int] = {}       # index -> arrival time_s
    wake_after: set[int] = set()          # utterance index closed by a wake cut

    # A `block #N (...)` line reports the CUT, not which utterance closed it
    # -- but a wake cut is emitted synchronously with the utterance that
    # contained the wake word (gateway/blocks.py:124-127, `sentence()`
    # returns right after `_emit("wake")`). The immediately preceding
    # `[n]` line is therefore that utterance.
    # `last_seen_idx` tracks the most recent utterance UNCONDITIONALLY --
    # a `block #N (wake, ...)` line can immediately follow an utterance that
    # falls OUTSIDE the requested [start, end] window (e.g. the window ends
    # at 10:25:00 and utterance 41 arrives at 10:25:25 with an instant wake
    # cut). If this tracked only in-window utterances, that wake cut would
    # be misattributed to the last IN-WINDOW utterance instead -- silently
    # wrong `wake: true` on an utterance that never triggered one. The
    # window filter is applied only when writing to `utterances`/`wake_after`.
    last_seen_idx = None
    for line in lines:
        m = UTTERANCE_RE.match(line)
        if m:
            h, mi, s, idx = m.groups()
            idx = int(idx)
            t = _hms(h, mi, s)
            last_seen_idx = idx
            if start_s is not None and t < start_s:
                continue
            if end_s is not None and t > end_s:
                continue
            utterances[idx] = t
            continue
        m = BLOCK_RE.match(line)
        if m and m.group(5) == "wake" and last_seen_idx is not None:
            wake_after.add(last_seen_idx)

    ordered = sorted(utterances.items())
    events: list[Event] = []
    for i, (idx, t) in enumerate(ordered):
        pause = None
        if i + 1 < len(ordered):
            pause = float(ordered[i + 1][1] - t)
        events.append(Event(index=idx, time_s=t, pause_s=pause,
                             wake=idx in wake_after))
    return events


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logfile")
    ap.add_argument("--start", help="HH:MM, inclusive")
    ap.add_argument("--end", help="HH:MM, inclusive")
    args = ap.parse_args(argv)

    def to_s(hhmm: str | None) -> int | None:
        if not hhmm:
            return None
        h, m = hhmm.split(":")
        return int(h) * 3600 + int(m) * 60

    with open(args.logfile, encoding="utf-8") as f:
        lines = f.readlines()

    events = extract(lines, to_s(args.start), to_s(args.end))
    for e in events:
        print(json.dumps(asdict(e)))
    print(f"# {len(events)} utterances, {sum(e.wake for e in events)} wake "
          f"cuts", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
