# CoThinker — tests of blocks/speaker_queue (the SpeakerQueueBlock)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block's own tests: feeds `action` CoreEvents (as the block host would
route a `UserAction`) and checks the `queue:room` view intent it emits plus
the `block_output` it returns on a change of who speaks.

    python3 -m pytest -q tests/test_block_speaker_queue.py
"""
from __future__ import annotations

from pathlib import Path

from blocks.speaker_queue import SpeakerQueueBlock
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def _ctx(tmp_path, emitted, clock):
    return BlockContext(instance=None, session_dir=tmp_path,
                        state=StateStore(tmp_path / "speaker-queue"), agent=None,
                        llm=None, language="en", clock=clock,
                        emit=emitted.append, artefact=lambda name, text: None)


def _action(intent_id, kind, person, payload, at=0.0):
    return CoreEvent("action", {"intent_id": intent_id, "kind": kind,
                                "person": person, "payload": payload, "at": at})


def _new_block(tmp_path, clock=lambda: 0.0):
    emitted = []
    block = SpeakerQueueBlock()
    block.start(_ctx(tmp_path, emitted, clock), {})
    return block, emitted


def test_tap_request_queues_in_order(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_action("participant:name", "edit", "p1", {"name": "Alice"}))
    block.on_event(_action("participant:name", "edit", "p2", {"name": "Bob"}))
    block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    block.on_event(_action("queue:room", "tap", "p2", {"op": "request"}))
    payload = emitted[-1].payload
    assert payload["speaking"] == {"pid": "p1", "name": "Alice"}
    assert payload["waiting"] == [{"pid": "p2", "name": "Bob"}]


def test_done_hands_over(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_action("participant:name", "edit", "p1", {"name": "Alice"}))
    block.on_event(_action("participant:name", "edit", "p2", {"name": "Bob"}))
    block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    block.on_event(_action("queue:room", "tap", "p2", {"op": "request"}))
    block.on_event(_action("queue:room", "tap", "p1", {"op": "done"}))
    payload = emitted[-1].payload
    assert payload["speaking"] == {"pid": "p2", "name": "Bob"}
    assert payload["waiting"] == []


def test_unknown_pid_shows_placeholder_name(tmp_path):
    block, emitted = _new_block(tmp_path)
    out = block.on_event(_action("queue:room", "tap", "p9", {"op": "request"}))
    payload = emitted[-1].payload
    assert payload["speaking"]["pid"] == "p9"
    assert payload["speaking"]["name"]
    assert payload["speaking"]["name"] != ""
    assert out is not None


def test_name_edit_updates_payload(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    before_name = emitted[-1].payload["speaking"]["name"]
    block.on_event(_action("participant:name", "edit", "p1", {"name": "Alice"}))
    after_name = emitted[-1].payload["speaking"]["name"]
    assert before_name != "Alice"
    assert after_name == "Alice"


def test_holder_output_on_change_only(tmp_path):
    block, emitted = _new_block(tmp_path)
    out = block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    assert len(out) == 1
    assert out[0].kind == "block_output"
    assert out[0].payload["holder"] == {"before": None, "after": "p1", "at": 0.0}

    out = block.on_event(_action("queue:room", "tap", "p2", {"op": "request"}))
    assert out == ()

    out = block.on_event(_action("participant:name", "edit", "p1", {"name": "Alice"}))
    assert out == ()

    out = block.on_event(_action("queue:room", "tap", "p1", {"op": "done"}))
    assert len(out) == 1
    assert out[0].kind == "block_output"
    assert out[0].payload["holder"] == {"before": "p1", "after": "p2", "at": 0.0}


def test_double_request_is_idempotent(tmp_path):
    block, emitted = _new_block(tmp_path)
    out1 = block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    out2 = block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    assert len(out1) == 1
    assert out2 == ()
    payload = emitted[-1].payload
    assert payload["speaking"]["pid"] == "p1"
    assert payload["waiting"] == []


def _round_start(block, people, at=0.0):
    return block.on_event(_action("queue:room", "edit", None,
                                  {"op": "round_start", "people": people}, at))


def test_tap_mode_view_has_no_round(tmp_path):
    block, emitted = _new_block(tmp_path)
    block.on_event(_action("queue:room", "tap", "p1", {"op": "request"}))
    assert emitted[-1].payload["mode"] == "tap"
    assert emitted[-1].payload["round"] is None


def test_round_start_shows_round_view(tmp_path):
    block, emitted = _new_block(tmp_path)
    assert _round_start(block, ["Ann", "Ben", "Cy"]) == ()
    payload = emitted[-1].payload
    assert payload["mode"] == "round"
    assert payload["round"] == {"people": ["Ann", "Ben", "Cy"],
                                "speaker": "Ann", "next_speaker": "Ben"}


def test_next_person_voice_advances_speaker(tmp_path):
    block, emitted = _new_block(tmp_path)
    _round_start(block, ["Ann", "Ben", "Cy"])
    out = block.on_event(_action("queue:room", "voice", None, {"op": "next_person"}))
    assert out == ()
    assert emitted[-1].payload["round"]["speaker"] == "Ben"
    assert emitted[-1].payload["round"]["next_speaker"] == "Cy"


def test_round_wrap_emits_round_wrapped(tmp_path):
    block, emitted = _new_block(tmp_path)
    _round_start(block, ["Ann", "Ben"])
    out = block.on_event(_action("queue:room", "voice", None, {"op": "next_person"}, at=5.0))
    assert out == ()
    out = block.on_event(_action("queue:room", "voice", None, {"op": "next_person"}, at=9.0))
    assert len(out) == 1 and out[0].kind == "block_output"
    assert out[0].payload == {"from": "speaker-queue", "round_wrapped": {"at": 9.0}}
    assert emitted[-1].payload["round"]["speaker"] == "Ann"


def test_single_person_round_has_no_next_speaker(tmp_path):
    block, emitted = _new_block(tmp_path)
    _round_start(block, ["Ann"])
    assert emitted[-1].payload["round"]["next_speaker"] is None


def test_next_person_without_round_is_noop(tmp_path):
    block, emitted = _new_block(tmp_path)
    out = block.on_event(_action("queue:room", "voice", None, {"op": "next_person"}))
    assert out == ()
    assert emitted == []


def test_round_state_survives_restart(tmp_path):
    block, emitted = _new_block(tmp_path)
    _round_start(block, ["Ann", "Ben", "Cy"])
    block.on_event(_action("queue:room", "voice", None, {"op": "next_person"}))
    again, emitted2 = _new_block(tmp_path)
    assert emitted2[-1].payload["round"]["speaker"] == "Ben"   # restored view on start
    _round_start(again, ["Ann", "Ben", "Cy"])
    assert emitted2[-1].payload["round"]["speaker"] == "Ben"
    _round_start(again, ["Dee", "Eve"])
    assert emitted2[-1].payload["round"]["speaker"] == "Dee"


def test_corrupt_round_state_starts_without_a_round(tmp_path):
    (tmp_path / "speaker-queue").mkdir()
    (tmp_path / "speaker-queue" / "round.json").write_text("{not json",
                                                           encoding="utf-8")
    block, emitted = _new_block(tmp_path)
    assert emitted == []
    assert block._round is None
