#!/usr/bin/env python3
# CoThinker -- baseline: what the 2.5 s pause cut does to a known conversation
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Print the yardstick every later turn detector has to beat.

    python3 scripts/measure-turn-cuts.py
    python3 scripts/measure-turn-cuts.py --pause-s 4.0 --blocks

No audio, no model, no network: the whole thing is text logic over
tests/turn_fixture.py. It measures gateway/blocks.py as it is today and
changes nothing.

Reading the output: an improvement lowers `early cuts` WITHOUT raising
`merges`. Try `--pause-s 30` once to see why both numbers are printed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from turn_fixture import FIXTURE            # noqa: E402
from turn_metrics import measure, replay    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause-s", type=float, default=2.5,
                    help="silence that closes a block (default: today's value)")
    ap.add_argument("--max-s", type=float, default=90.0,
                    help="length cut in seconds of speaking time")
    ap.add_argument("--min-chars", type=int, default=25,
                    help="below this an utterance is not sent alone")
    ap.add_argument("--blocks", action="store_true",
                    help="also list every block and the reason it was cut")
    a = ap.parse_args()

    kw = dict(pause_s=a.pause_s, max_s=a.max_s, min_chars=a.min_chars)
    m = measure(FIXTURE, **kw)

    print("fixture:    tests/turn_fixture.py")
    print(f"pause_s:    {a.pause_s}")
    for line in m.lines():
        print(line)

    if a.blocks:
        # A second replay: cheap (microseconds) and keeps `measure()` from
        # having to carry the blocks around for one optional listing.
        print()
        _, blocks = replay(FIXTURE, **kw)
        for b in blocks:
            print(f"  [{b.reason:6s}] {b.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
