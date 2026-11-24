# CoThinker — tests of blocks/speaker_queue (the SpeakerQueueBlock)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block's own tests: feeds `action` CoreEvents (as the block host would
route a `UserAction`) and checks the `queue:room` view intent it emits plus
the `block_output` it returns on a change of who speaks.

    python3 -m pytest -q blocks/speaker_queue/tests/test_speaker_queue.py
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
