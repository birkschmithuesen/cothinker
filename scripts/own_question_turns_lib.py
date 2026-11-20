#!/usr/bin/env python3
# CoThinker — S4e: per-turn listener-stream reconstruction
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Spec F1/F2: S4c/S4d read bullets' `transcript_so_far`, which drops the
asker's lines and every `is_question_readout` final -- the question itself.
A hub listener instead gets each final's filtered `content`, never a
`between_turns` final (workshop/hub.py, the `listener.on_stt` loop). This
module rebuilds that stream per turn from `rohtranskript.jsonl`:

- turn segmentation stays S4c/S4d's (`bullets-notes` groups by answer_id);
- Done stand-in (spec F3) = the group's last call `t_start_ms`;
- window = (previous Done, Done], first turn of a table opens 30 s before
  its first call, every window capped at 180 s;
- lines = rows with `filtered is None` and not `between_turns`, text only.

Pure functions; file reading only in `load_gp`/`read_jsonl`.
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_own_question_lib as lib  # noqa: E402

WINDOW_FIRST_LEAD_MS = 30_000
WINDOW_CAP_MS = 180_000
ROLLING_MAX_FINALS = 40
WORDS_PER_S = 2.5
LINE_GAP_S = 0.5
DONE_AFTER_LAST_S = 1.0
GP_BASE = Path.home() / f".hermes/profiles/{getpass.getuser()}/cache/scratch/gp-bullets"
GP_TABLES = ("t01", "t02", "t03")


def read_jsonl(path: Path) -> list:
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def listener_lines(raw_rows: List[dict], lo_ms: float, hi_ms: float) -> List[dict]:
    out = []
    for r in raw_rows:
        t = r.get("t_wall")
        text = (r.get("text") or "").strip()
        if not isinstance(t, (int, float)) or not (lo_ms < t <= hi_ms) or not text:
            continue
        if r.get("filtered") is not None or r.get("between_turns"):
            continue
        out.append({"t_wall": float(t), "text": text})
    return out


def gp_turns(table: str, trace_rows: List[dict], raw_rows: List[dict]) -> List[dict]:
    turns = []
    prev_done: Optional[float] = None
    for group in lib.group_bullets_calls(trace_rows):
        done = float(group[-1]["t_start_ms"])
        lo = (float(group[0]["t_start_ms"]) - WINDOW_FIRST_LEAD_MS) if prev_done is None else prev_done
        lo = max(lo, done - WINDOW_CAP_MS)
        cards = next((lib.parse_questions_shown(c) for c in group if lib.parse_questions_shown(c)), [])
        turns.append({"table": table, "answer_id": group[0].get("answer_id"), "synthetic": False,
                      "cards": cards, "lines": listener_lines(raw_rows, lo, done), "done_ms": done,
                      "question_end_ms": None})
        prev_done = done
    return turns


def synthetic_turn(entry: dict, cards: List[str]) -> dict:
    t_s = 0.0
    lines = []
    for text in entry["lines"]:
        t_end = t_s + len(text.split()) / WORDS_PER_S
        lines.append({"t_wall": t_end * 1000.0, "text": text})
        t_s = t_end + LINE_GAP_S
    return {"table": entry["table"], "answer_id": entry["answer_id"], "synthetic": True, "cards": list(cards),
            "lines": lines, "done_ms": lines[-1]["t_wall"] + DONE_AFTER_LAST_S * 1000.0,
            "question_end_ms": lines[entry["question_line"]]["t_wall"]}


def checkpoints(turn: dict, max_finals: Optional[int] = ROLLING_MAX_FINALS) -> List[dict]:
    lines = turn["lines"] if max_finals is None else turn["lines"][:max_finals]
    return [{"t_wall": l["t_wall"], "text": "\n".join(x["text"] for x in turn["lines"][:i + 1])}
            for i, l in enumerate(lines)]


def full_text(turn: dict) -> str:
    return "\n".join(l["text"] for l in turn["lines"])


def question_end_ms(turn: dict, truth_text: Optional[str], threshold: float = 0.7) -> Optional[float]:
    if turn.get("question_end_ms") is not None:
        return turn["question_end_ms"]
    if not truth_text:
        return None
    for cp in checkpoints(turn, max_finals=None):
        if lib.containment_score(truth_text, cp["text"]) >= threshold:
            return cp["t_wall"]
    return None


def load_gp(base: Path = GP_BASE) -> List[dict]:
    turns = []
    for table in GP_TABLES:
        d = Path(base) / table
        turns.extend(gp_turns(table, read_jsonl(d / "trace.jsonl"), read_jsonl(d / "rohtranskript.jsonl")))
    return turns
