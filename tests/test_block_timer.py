# CoThinker — blocks/timer: planned items, drift against plan, projected end
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_timer.py"""
from __future__ import annotations

from pathlib import Path

from blocks.timer import TimerBlock
from blocks.timer.clock import drift_s, planned_cumulative, projected_end
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def test_cumulative():
    assert planned_cumulative([5, 10, 15]) == (5, 15, 30)


def test_drift_with_overrun():
    cumulative = planned_cumulative([5, 10, 15])
    drift = drift_s(anchor=0.0, actual_start=360.0, index=1,
                     cumulative_min=cumulative, now=1200.0)
    assert drift == 300.0


def test_projected_end():
    cumulative = planned_cumulative([5, 10, 15])
    drift = drift_s(anchor=0.0, actual_start=360.0, index=1,
                     cumulative_min=cumulative, now=1200.0)
    end = projected_end(anchor=0.0, cumulative_min=cumulative, drift=drift)
    assert end == 2100.0


def test_no_anchor_is_none():
    cumulative = planned_cumulative([5, 10, 15])
    drift = drift_s(anchor=None, actual_start=360.0, index=1,
                     cumulative_min=cumulative, now=1200.0)
    assert drift is None
    assert projected_end(anchor=None, cumulative_min=cumulative, drift=drift) is None


def test_on_plan_is_zero():
    cumulative = planned_cumulative([5, 10, 15])
    drift = drift_s(anchor=0.0, actual_start=0.0, index=0,
                     cumulative_min=cumulative, now=200.0)
    assert drift == 0.0


def _ctx(tmp_path, clock):
    emitted = []
    ctx = BlockContext(instance=None, session_dir=tmp_path,
                       state=StateStore(tmp_path / "timer"), agent=None, llm=None,
                       language="en", clock=clock, emit=emitted.append,
                       artefact=lambda name, text: None)
    return ctx, emitted


def _config():
    return {
        "items": [
            {"label": "Check-in", "minutes": 5},
            {"label": "Main topic", "minutes": 10},
            {"label": "Closing", "minutes": 15},
        ],
        "deadline": None,
    }


def test_next_moves_item_and_emits(tmp_path):
    clock_values = iter([0.0, 360.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(clock_values))
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(CoreEvent("action", {"op": "next"}))
    assert out == ()
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert payload["current"] == 1
    assert payload["current_text"] == "Main topic"
    assert payload["next"] == 2
    assert payload["next_text"] == "Closing"
    assert payload["total"] == 3


def test_tick_does_not_emit(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(CoreEvent("tick", {}))
    assert out == ()
    assert emitted == []


def test_payload_has_absolute_times_only(tmp_path):
    clock_values = iter([100.0, 460.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(clock_values))
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(CoreEvent("action", {"op": "next"}))
    payload = emitted[0].payload
    assert payload["anchor_at"] == 100.0
    assert payload["item_started_at"] == 460.0
    assert payload["item_budget_s"] == 600.0
    assert payload["planned_cumulative_min"] == (5, 15, 30)
    assert "elapsed" not in payload and "remaining" not in payload
