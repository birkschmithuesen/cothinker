#!/usr/bin/env python3
# CoThinker — S4e: annotation aid, stdout only
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Prints real session speech -- NEVER redirect this into a file inside the
repo (leak guard, AGENTS.md raw-material rule).

    env -u PYTHONPATH python3 scripts/own-question-turn-dump.py --turn t01 a-3
    env -u PYTHONPATH python3 scripts/own-question-turn-dump.py --coverage
    env -u PYTHONPATH python3 scripts/own-question-turn-dump.py --check-synthetic
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import measure_own_question_lib as lib  # noqa: E402
import own_question_turns_lib as tl  # noqa: E402
from workshop.own_question_match import card_match  # noqa: E402

V1_TRUTH = REPO / "scripts/own_question_ground_truth.json"
SYNTHETIC = REPO / "scripts/own_question_synthetic_turns.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--turn", nargs=2, metavar=("TABLE", "ANSWER_ID"))
    ap.add_argument("--all", action="store_true", help="dump every real turn")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--check-synthetic", action="store_true")
    a = ap.parse_args(argv)
    turns = tl.load_gp()
    by_key = {(t["table"], t["answer_id"]): t for t in turns}
    if a.turn or a.all:
        keys = [tuple(a.turn)] if a.turn else list(by_key)
        for key in keys:
            t = by_key[key]
            print(f"=== {key[0]} {key[1]}  done_ms={t['done_ms']:.0f}  lines={len(t['lines'])}")
            for i, c in enumerate(t["cards"]):
                print(f"  card {i}: {c}")
            for l in t["lines"]:
                print(f"  [{(l['t_wall'] - t['done_ms']) / 1000:7.1f}s] {l['text']}")
    if a.coverage:
        print(f"real turns: {len(turns)}")
        v1 = json.loads(V1_TRUTH.read_text(encoding="utf-8"))["turns"]
        asked = [e for e in v1 if e["asked"]]
        n = sum(1 for e in asked
                if lib.containment_score(by_key[(e["table"], e["answer_id"])]["cards"][e["truth_card_index"]],
                                         tl.full_text(by_key[(e["table"], e["answer_id"])])) >= 0.7)
        print(f"content coverage >= 0.7: {n}/{len(asked)}")
    if a.check_synthetic:
        syn = json.loads(SYNTHETIC.read_text(encoding="utf-8"))["turns"]
        bad = [s["answer_id"] for s in syn
               if card_match(s["truth_question"], by_key[(s["carrier"]["table"], s["carrier"]["answer_id"])]["cards"])
               is not None]
        print(f"synthetic: {len(syn)} turns, {len(bad)} truth questions match a carrier card {bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
