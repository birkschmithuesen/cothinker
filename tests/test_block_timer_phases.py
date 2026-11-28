# CoThinker — blocks/timer: following the one-day format's cue clock
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_timer_phases.py"""
from __future__ import annotations

from blocks.timer import TimerBlock
from blocks.timer.clock import drift_s, planned_cumulative, projected_end
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def _ctx(tmp_path, clock):
    emitted = []
    ctx = BlockContext(instance=None, session_dir=tmp_path,
                       state=StateStore(tmp_path / "timer"), agent=None, llm=None,
                       language="en", clock=clock, emit=emitted.append,
                       artefact=lambda name, text: None)
    return ctx, emitted


def _phase_event(to, at):
    return CoreEvent("phase", {"from": None, "to": to, "cause": "operator", "at": at})


def _cue_config(follow_phases=True):
    return {
        "items": [
            {"label": "Check-in", "minutes": 5, "phase": "einstieg"},
            {"label": "Main topic", "minutes": 10, "phase": "diskussion"},
            {"label": "Closing", "minutes": 15, "phase": "abschluss"},
        ],
        "deadline": None,
        "follow_phases": follow_phases,
    }


def _reflect_config():
    return {
        "items": [
            {"label": "Check-in", "minutes": 5},
            {"label": "Main topic", "minutes": 10},
            {"label": "Closing", "minutes": 15},
        ],
        "deadline": None,
    }


def test_phase_change_moves_item_and_emits_once(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _cue_config())
    out = block.on_event(_phase_event("diskussion", at=50.0))
    assert out == ()
    assert len(emitted) == 2       # the start emit, then exactly one from the move
    payload = emitted[-1].payload
    assert payload["current"] == 2
    assert payload["current_text"] == "Main topic"
    assert payload["item_started_at"] == 50.0


def test_back_to_earlier_phase_moves_item_back(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _cue_config())
    block.on_event(_phase_event("abschluss", at=100.0))
    out = block.on_event(_phase_event("einstieg", at=5.0))
    assert out == ()
    assert len(emitted) == 3       # start, move to abschluss, move back to einstieg
    payload = emitted[-1].payload
    assert payload["current"] == 1
    assert payload["current_text"] == "Check-in"
    assert payload["item_started_at"] == 5.0


def test_drift_and_projected_end_match_hub_golden_on_a_cue_sequence(tmp_path):
    cumulative = planned_cumulative([5, 10, 15])
    expected_drift = drift_s(anchor=0.0, actual_start=360.0, index=1,
                              cumulative_min=cumulative, now=1200.0)
    expected_end = projected_end(anchor=0.0, cumulative_min=cumulative, drift=expected_drift)

    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _cue_config())
    block.on_event(_phase_event("diskussion", at=360.0))

    payload = emitted[-1].payload
    actual_drift = drift_s(anchor=payload["anchor_at"], actual_start=payload["item_started_at"],
                            index=payload["current"] - 1, cumulative_min=payload["planned_cumulative_min"],
                            now=1200.0)
    actual_end = projected_end(anchor=payload["anchor_at"],
                                cumulative_min=payload["planned_cumulative_min"], drift=actual_drift)

    assert actual_drift == expected_drift == 300.0
    assert actual_end == expected_end == 2100.0


def test_reflect_config_ignores_phase_events(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _reflect_config())
    out = block.on_event(_phase_event("diskussion", at=50.0))
    assert out == ()
    assert len(emitted) == 1       # the start emit only, the phase event is a no-op
    assert emitted[-1].payload["current"] == 1
