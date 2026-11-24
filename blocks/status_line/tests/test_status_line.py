# CoThinker — tests for blocks/status_line/: the status-line block (issue
# #41, Wave B.4)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q blocks/status_line/tests/test_status_line.py

Ported from the archive `tests/test_status_line.py` (commit 3b4a843b): the
precedence section only -- `pick()` moved into `blocks/status_line/line.py`
with this split. Everything about the rendered German text stayed with
`gateway/stage_texts.py` and is ported in `tests/test_stage_texts.py`
instead.
"""
from __future__ import annotations

from pathlib import Path

import blocks.status_line as status_line_block
from blocks.status_line.line import PRECEDENCE, pick

StatusLineBlock = status_line_block.StatusLineBlock
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent, StateStore
from shared.surface import ViewIntent


# -- ported: precedence (archive tests/test_status_line.py) ----------------

def test_failed_beats_working():
    assert pick(["working", "failed"]) == "failed"


def test_working_beats_backlog():
    assert pick(["backlog", "working"]) == "working"


def test_backlog_beats_listening():
    assert pick(["listening", "backlog"]) == "backlog"


def test_listening_is_the_floor():
    assert pick([]) == "listening"


def test_an_unknown_state_is_ignored():
    assert pick(["nonsense"]) == "listening"


def test_precedence_matches_pick_order():
    assert PRECEDENCE == ("failed", "working", "backlog", "listening")


# -- new: the Block wrapping pick() -----------------------------------------

def make_block(tmp_path):
    block = StatusLineBlock()
    emitted = []
    ctx = BlockContext(
        instance=None, session_dir=Path(tmp_path),
        state=StateStore(Path(tmp_path) / block.id), agent=None, llm=None,
        language="de", clock=lambda: 0.0, emit=emitted.append,
        artefact=lambda name, text: None)
    block.start(ctx, {})
    return block, emitted


def dispatch_tick(block, components):
    return block.on_event(CoreEvent("tick", {"at": 0.0, "components": components}))


def test_block_id_matches_the_template_registry_entry():
    assert StatusLineBlock.id == "status-line"
    assert StatusLineBlock.api_version == BLOCK_API_VERSION


def test_first_tick_with_no_backlog_or_turn_emits_listening(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 0, "open_block_chars": 0})
    assert len(emitted) == 1
    intent = emitted[0]
    assert isinstance(intent, ViewIntent)
    assert intent.id == "status:line"
    assert intent.block == "status-line"
    assert intent.kind == "status"
    assert intent.payload["state"] == "listening"


def test_backlog_over_one_wins_over_listening(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 3, "open_block_chars": 0})
    assert emitted[-1].payload["state"] == "backlog"
    assert emitted[-1].payload["detail"]["count"] == 3


def test_backlog_of_exactly_one_does_not_trigger_backlog_state(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 1, "open_block_chars": 0})
    assert emitted[-1].payload["state"] == "listening"


def test_turn_running_wins_over_backlog(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 3, "turn_running": True})
    assert emitted[-1].payload["state"] == "working"


def test_run_failed_wins_over_working(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"turn_running": True, "run_failed": True})
    assert emitted[-1].payload["state"] == "failed"


def test_repeated_identical_tick_does_not_re_emit(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 0})
    dispatch_tick(block, {"queue_backlog": 0})
    dispatch_tick(block, {"queue_backlog": 0})
    assert len(emitted) == 1


def test_a_changed_backlog_count_re_emits(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 3})
    dispatch_tick(block, {"queue_backlog": 5})
    assert len(emitted) == 2
    assert emitted[-1].payload["detail"]["count"] == 5


def test_a_tick_missing_turn_running_does_not_reset_it_sticky(tmp_path):
    """A plain ticker tick (no `turn_running`/`run_failed` keys) must not
    flip a still-running turn back to `listening` -- the ticker fires once
    a second with only `queue_backlog`/`open_block_chars`, while
    `turn_running` only arrives via `gateway.consumer.publish_status()`."""
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"turn_running": True})
    dispatch_tick(block, {"queue_backlog": 0, "open_block_chars": 12})
    assert emitted[-1].payload["state"] == "working"


def test_non_tick_events_are_ignored(tmp_path):
    block, emitted = make_block(tmp_path)
    block.on_event(CoreEvent("utterance", {"text": "hallo"}))
    assert emitted == []


def test_the_emitted_payload_never_carries_a_clock_value(tmp_path):
    block, emitted = make_block(tmp_path)
    dispatch_tick(block, {"queue_backlog": 3, "turn_running": True, "run_failed": True})
    assert set(emitted[-1].payload) == {"state", "detail"}
    assert "at" not in emitted[-1].payload
    assert "at" not in emitted[-1].payload["detail"]
