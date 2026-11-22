# CoThinker — InScribe template: the workshop format (issue #103)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The workshop half of the inscribe template: twelve phases in cue order,
advanced by the operator, plus an example instance with an invented cue
sheet in the same schema the format reads.

python3 -m pytest -q tests/test_inscribe_workshop_format.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from shared.templates import CORE_RULES, load_template

TEMPLATE_PATH = REPO / "templates" / "inscribe" / "template.toml"
CUE_SHEET_PATH = REPO / "instances" / "example-workshop" / "content" / "cue-sheet.json"

CUE_IDS_IN_ORDER = ("0", "0b", "1", "1b", "2", "3", "4", "5", "6", "7", "8", "9")


def _load():
    return load_template(TEMPLATE_PATH, format="workshop")


def test_workshop_format_loads_with_12_phases_in_cue_order():
    template = _load()
    assert tuple(p.id for p in template.phases) == CUE_IDS_IN_ORDER


def test_phase_ids_match_example_cue_sheet_nr():
    template = _load()
    cue_sheet = json.loads(CUE_SHEET_PATH.read_text(encoding="utf-8"))
    nrs = tuple(cue["nr"] for cue in cue_sheet["cues"])
    assert nrs == CUE_IDS_IN_ORDER
    assert tuple(p.id for p in template.phases) == nrs


def test_every_phase_block_is_declared_for_the_format():
    template = _load()
    block_ids = {b.id for b in template.blocks}
    assert block_ids, "the workshop format must declare at least one block"
    for phase in template.phases:
        unknown = [b for b in phase.blocks if b not in block_ids]
        assert not unknown, f"phase {phase.id!r} names undeclared blocks {unknown}"


def test_operator_enters_every_cue():
    template = _load()
    for phase in template.phases:
        assert phase.enter_when == ("operator",), (
            f"phase {phase.id!r} must be advanced by the operator only, got {phase.enter_when}"
        )


def test_core_rules_present():
    template = _load()
    for rule in CORE_RULES:
        assert rule in template.rules


def test_room_and_table_blocks_partition():
    template = _load()
    room = {b.id for b in template.blocks if b.config.get("runs_in") == "room"}
    table = {b.id for b in template.blocks if b.config.get("runs_in") == "table"}
    all_ids = {b.id for b in template.blocks}
    assert room, "at least one block must run once per room"
    assert table, "at least one block must run once per table"
    assert room & table == set()
    assert room | table == all_ids


# Reference cue -> block membership, transcribed from the plan's twelve-cue
# table (docs/superpowers/plans/2026-11-21-inscribe-template.md, "Workshop
# format — twelve cues (last state)"). Checked content, not just shape: this
# is what caught a phase silently losing or swapping a block.
CUE_BLOCKS = {
    "0": frozenset({"timer"}),
    "0b": frozenset({"timer", "intro-round"}),
    "1": frozenset({"timer", "intro-round"}),
    "1b": frozenset({"timer", "intro-round", "speaking-share"}),
    "2": frozenset({"timer"}),
    "3": frozenset({
        "timer", "interview-round", "interview-questions",
        "capture-bullets", "capture-terms", "speaking-share",
    }),
    "4": frozenset({"timer"}),
    "5": frozenset({
        "timer", "setting-round", "capture-terms", "merge-terms",
        "contrast-pairs", "condense", "capture-bullets", "speaker-queue",
        "speaking-share", "graph",
    }),
    "6": frozenset({"timer", "write-text", "dramaturgy-checks", "poster", "image-generation"}),
    "7": frozenset({"timer"}),
    "8": frozenset({
        "timer", "feedback-round", "condense", "speaker-queue",
        "speaking-share", "graph",
    }),
    "9": frozenset({"timer", "release", "graph", "poster"}),
}

# Reference runs_in scope per declared block, derived from the archive hub
# source (workshop/hub.py default_listeners is the per-table listener
# factory; workshop/release.py ReleaseListener docstring: "one per table").
# speaking-share and graph aggregate across tables onto the wall / the room
# queue, so they are declared "room" even though some of their sources also
# touch per-table state (see the commit message for the full justification).
BLOCK_RUNS_IN = {
    "timer": "room",
    "speaker-queue": "room",
    "graph": "room",
    "speaking-share": "room",
    "release": "table",
    "intro-round": "table",
    "interview-round": "table",
    "interview-questions": "table",
    "capture-bullets": "table",
    "capture-terms": "table",
    "merge-terms": "table",
    "setting-round": "table",
    "contrast-pairs": "table",
    "condense": "table",
    "write-text": "table",
    "dramaturgy-checks": "table",
    "poster": "table",
    "image-generation": "table",
    "feedback-round": "table",
}


def test_phase_blocks_match_the_plans_cue_table():
    template = _load()
    assert {p.id for p in template.phases} == set(CUE_BLOCKS)
    for phase in template.phases:
        assert set(phase.blocks) == CUE_BLOCKS[phase.id], (
            f"phase {phase.id!r}: expected blocks {sorted(CUE_BLOCKS[phase.id])}, "
            f"got {sorted(phase.blocks)}"
        )


def test_block_runs_in_matches_reference_scope():
    template = _load()
    assert {b.id for b in template.blocks} == set(BLOCK_RUNS_IN)
    for block in template.blocks:
        assert block.config.get("runs_in") == BLOCK_RUNS_IN[block.id], (
            f"block {block.id!r}: expected runs_in {BLOCK_RUNS_IN[block.id]!r}, "
            f"got {block.config.get('runs_in')!r}"
        )


def test_release_runs_in_table_not_room():
    template = _load()
    release = next(b for b in template.blocks if b.id == "release")
    assert release.config.get("runs_in") == "table"
