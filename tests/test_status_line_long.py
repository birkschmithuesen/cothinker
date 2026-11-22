# CoThinker — tests for blocks/status_line/ group mode (issue #67)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_status_line_long.py

The long format's group status: `mode = "group"` makes blocks/status_line a
second user of the same package (issue #67). `tests/golden/status_line/
active.input.json` and `.expected.json` characterize interview-theater's
`cothinker_status.leite_ab()` on synthetic input (recorded by a throwaway,
uncommitted script against that sibling repo, never against this one) --
translated below into this repo's own `status:group`/`status:operator`
intent shape, since this block has no timestamps to run `leite_ab()`'s
freshness logic on in the first place.
"""
from __future__ import annotations

import json
from pathlib import Path

from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore
from shared.surface import ViewIntent
from blocks.status_line import StatusLineBlock

GOLDEN = Path(__file__).parent / "golden" / "status_line"


def make_group_block(tmp_path):
    block = StatusLineBlock()
    emitted = []
    ctx = BlockContext(
        instance=None, session_dir=Path(tmp_path),
        state=StateStore(Path(tmp_path) / block.id), agent=None, llm=None,
        language="en", clock=lambda: 0.0, emit=emitted.append,
        artefact=lambda name, text: None)
    block.start(ctx, {"mode": "group"})
    return block, emitted


def test_group_mode_actively_progressing_reports_the_most_advanced_progress_signal(tmp_path):
    """Golden case `active`: interview-theater's `leite_ab()` reports
    `denkt` (actively thinking) for an LLM run started 30s ago (< the
    180s timeout) -- here translated as the piece actively advancing
    through its `interviews` phase, reported via a `block_output` fact."""
    expected = json.loads((GOLDEN / "active.expected.json").read_text())
    assert expected == ["denkt", "2026-11-21T11:59:30+00:00"]

    block, emitted = make_group_block(tmp_path)
    block.on_event(CoreEvent("phase", {"id": "interviews"}))
    block.on_event(CoreEvent(
        "block_output",
        {"from": "interview-runner", "interviews": {"done": 2, "total": 5}}))

    group_intents = [i for i in emitted if i.id == "status:group"]
    operator_intents = [i for i in emitted if i.id == "status:operator"]
    assert group_intents
    assert operator_intents

    group_intent = group_intents[-1]
    assert isinstance(group_intent, ViewIntent)
    assert group_intent.audience == "room"
    assert group_intent.payload == {
        "phase": "interviews",
        "progress": {"interviews": {"done": 2, "total": 5}},
    }

    operator_intent = operator_intents[-1]
    assert operator_intent.audience == "operator"
    assert operator_intent.payload == {
        "phase": "interviews",
        "piece_rev": None,
        "interviews": {"done": 2, "total": 5},
        "findings": None,
    }


def test_group_mode_has_no_freshness_timeout_unlike_leite_ab(tmp_path):
    """Deliberate deviation from the source: `leite_ab()` lets `denkt` time
    out after `DENKT_TIMEOUT_S` (180s) and `hoert` go stale after
    `HOERT_FRISCH_S` (60s) by comparing `jetzt` against a stored timestamp.
    This block's `phase`/`block_output` facts carry no timestamps at all
    (per issue #67's interface), so a sticky progress fact never times out
    on its own -- it only changes when a new fact for the same key arrives."""
    block, emitted = make_group_block(tmp_path)
    block.on_event(CoreEvent(
        "block_output", {"from": "piece-writer", "piece_rev": 1}))
    block.on_event(CoreEvent("phase", {"id": "condense"}))

    operator_intent = [i for i in emitted if i.id == "status:operator"][-1]
    assert operator_intent.payload["piece_rev"] == 1
