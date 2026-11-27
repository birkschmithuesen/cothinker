# CoThinker — blocks/timer: planned items, drift against plan, projected end
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q blocks/timer/tests/test_timer.py"""
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


def _act(op, intent_id="timer:current", kind="voice", **extra):
    return CoreEvent("action", {"intent_id": intent_id, "kind": kind, "person": None,
                                "payload": {"op": op, **extra}, "at": 0.0})


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
    out = block.on_event(_act("next"))
    assert out == ()
    assert len(emitted) == 2
    payload = emitted[-1].payload
    assert payload["current"] == 2
    assert payload["current_text"] == "Main topic"
    assert payload["next"] == 3
    assert payload["next_text"] == "Closing"
    assert payload["total"] == 3


def test_tick_does_not_emit(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(CoreEvent("tick", {}))
    assert out == ()
    assert len(emitted) == 1      # the start emit only


def test_payload_has_absolute_times_only(tmp_path):
    clock_values = iter([100.0, 460.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(clock_values))
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    payload = emitted[-1].payload
    assert payload["anchor_at"] == 100.0
    assert payload["item_started_at"] == 460.0
    assert payload["item_budget_s"] == 600.0
    assert payload["planned_cumulative_min"] == (5, 15, 30)
    assert "elapsed" not in payload and "remaining" not in payload


def test_next_at_last_item_stays_on_it(tmp_path):
    # Since D.4: `next` on the last item keeps the index and
    # restarts that item's clock (emits again), it is no longer a no-op.
    clock_values = iter([0.0, 1.0, 2.0, 3.0, 4.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(clock_values))
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    block.on_event(_act("next"))
    assert block._index == 2
    out = block.on_event(_act("next"))
    assert out == ()
    assert block._index == 2
    assert len(emitted) == 4      # start + three moves


def test_back_at_first_item_is_noop(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(_act("back"))
    assert out == ()
    assert len(emitted) == 1      # the start emit only
    assert block._index == 0


def test_first_item_view_is_one_based(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    block.on_event(_act("back"))
    payload = emitted[-1].payload
    assert payload["current"] == 1 and payload["next"] == 2
    assert set(payload) == {"current", "current_text", "next", "next_text", "total",
                            "item_started_at", "item_budget_s", "meeting_deadline",
                            "anchor_at", "planned_cumulative_min",
                            "drift_s", "projected_end"}


def test_last_item_has_no_next(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    block.on_event(_act("next"))
    payload = emitted[-1].payload
    assert payload["current"] == 3 and payload["next"] is None
    assert payload["next_text"] is None


def test_next_on_last_item_restarts_its_clock(tmp_path):
    times = iter([0.0, 10.0, 20.0, 30.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(times))
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    block.on_event(_act("next"))
    block.on_event(_act("next"))
    assert emitted[-1].payload["current"] == 3
    assert emitted[-1].payload["item_started_at"] == 30.0


def test_item_without_minutes_has_no_budget(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, {"items": [{"label": "A", "minutes": None},
                                {"label": "B", "minutes": 2}], "deadline": 99.0})
    block.on_event(_act("next"))
    block.on_event(_act("back"))
    assert emitted[-1].payload["item_budget_s"] is None
    assert emitted[-1].payload["meeting_deadline"] == 99.0


def test_goto_jumps_to_the_given_index(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(_act("goto", index=2))
    assert out == ()
    payload = emitted[-1].payload
    assert payload["current"] == 3 and payload["current_text"] == "Closing"


def test_goto_out_of_range_is_noop(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(_act("goto", index=5))
    assert out == ()
    assert len(emitted) == 1      # the start emit only
    assert block._index == 0


def test_goto_same_index_is_noop(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(_act("goto", index=0))
    assert out == ()
    assert len(emitted) == 1      # the start emit only


def test_goto_keeps_an_already_started_item_clock(tmp_path):
    times = iter([0.0, 1.0, 2.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(times))
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))              # index 1, started at 1.0
    block.on_event(_act("goto", index=0))      # back to index 0, started at 0.0
    assert emitted[-1].payload["item_started_at"] == 0.0


def test_round_wrapped_output_advances_item(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    out = block.on_event(CoreEvent("block_output", {
        "from": "speaker-queue", "round_wrapped": {"at": 4.0}}))
    assert out == ()
    assert emitted[-1].payload["current"] == 2


def test_other_block_output_is_ignored(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(CoreEvent("block_output", {"holder": {"before": None, "after": "p1", "at": 1.0}}))
    block.on_event(_act("next", intent_id="queue:room"))
    assert len(emitted) == 1      # the start emit only


def test_start_emits_the_initial_payload(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 7.0)
    block = TimerBlock()
    block.start(ctx, _config())
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert (payload["current"], payload["next"], payload["total"]) == (1, 2, 3)
    assert payload["item_started_at"] == 7.0
    assert payload["item_budget_s"] == 300.0
    assert payload["anchor_at"] == 7.0


def test_start_anchors_on_the_configured_start_at_not_service_start(tmp_path):
    # issue #203: the service can start well after the meeting's planned
    # start (an operator opens the stack late). `start_at` in the config
    # is the PLANNED start (e.g. from agenda.md's `Start:` header, see
    # gateway/consumer.py::AgendaController); the block must anchor on it
    # instead of the moment it happens to be started.
    ctx, emitted = _ctx(tmp_path, lambda: 900.0)
    block = TimerBlock()
    config = _config()
    config["start_at"] = 100.0
    block.start(ctx, config)
    assert emitted[0].payload["anchor_at"] == 100.0
    assert emitted[0].payload["item_started_at"] == 100.0


def test_start_without_start_at_falls_back_to_the_clock(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 42.0)
    block = TimerBlock()
    block.start(ctx, _config())
    assert emitted[0].payload["anchor_at"] == 42.0


def test_payload_carries_drift_against_the_original_plan(tmp_path):
    # Plan: item 0 is 0-5min, item 1 is 5-15min. The service anchors at 0,
    # but "next" only fires at 360s -- item 1 starts a minute late against
    # its planned 300s start, so the plan has drifted by 60s.
    clock_values = iter([0.0, 360.0])
    ctx, emitted = _ctx(tmp_path, lambda: next(clock_values))
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    payload = emitted[-1].payload
    assert payload["drift_s"] == 60.0
    assert payload["projected_end"] == 1860.0


def test_payload_drift_is_zero_on_plan(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    block = TimerBlock()
    block.start(ctx, _config())
    assert emitted[0].payload["drift_s"] == 0.0


def test_start_without_items_emits_nothing(tmp_path):
    ctx, emitted = _ctx(tmp_path, lambda: 0.0)
    TimerBlock().start(ctx, {"items": [], "deadline": None})
    assert emitted == []


def test_anchor_survives_restart_without_a_move(tmp_path):
    ctx, _ = _ctx(tmp_path, lambda: 7.0)
    TimerBlock().start(ctx, _config())
    ctx2, emitted2 = _ctx(tmp_path, lambda: 900.0)
    TimerBlock().start(ctx2, _config())
    assert emitted2[-1].payload["anchor_at"] == 7.0


def test_item_state_survives_restart(tmp_path):
    ctx, _ = _ctx(tmp_path, lambda: 7.0)
    block = TimerBlock()
    block.start(ctx, _config())
    block.on_event(_act("next"))
    ctx2, emitted2 = _ctx(tmp_path, lambda: 500.0)
    again = TimerBlock()
    again.start(ctx2, _config())
    again.on_event(_act("back"))
    payload = emitted2[-1].payload
    assert payload["current"] == 1
    assert payload["anchor_at"] == 7.0
    assert payload["item_started_at"] == 7.0
    again.on_event(_act("next"))
    assert emitted2[-1].payload["current"] == 2
    assert emitted2[-1].payload["item_started_at"] == 500.0


def test_corrupt_state_starts_fresh(tmp_path):
    (tmp_path / "timer").mkdir()
    (tmp_path / "timer" / "timer.json").write_text("{not json", encoding="utf-8")
    ctx, emitted = _ctx(tmp_path, lambda: 50.0)
    block = TimerBlock()
    block.start(ctx, _config())
    assert emitted[-1].payload["current"] == 1
    assert emitted[-1].payload["anchor_at"] == 50.0


def test_bad_values_in_state_start_fresh(tmp_path):
    (tmp_path / "timer").mkdir()
    (tmp_path / "timer" / "timer.json").write_text(
        '{"index": 1, "anchor": 5, "started": {"x": 1}}', encoding="utf-8")
    ctx, emitted = _ctx(tmp_path, lambda: 50.0)
    block = TimerBlock()
    block.start(ctx, _config())
    assert emitted[-1].payload["current"] == 1
    assert emitted[-1].payload["anchor_at"] == 50.0
