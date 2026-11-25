# CoThinker — tests of blocks/call_window (the CallWindowBlock)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block's own tests (ADR 0022 level 1): feeds `action` and
`block_output` CoreEvents against a FAKE host handle (a real
`gateway.segmenter.BlockBuilder` is exercised by its own tests and by the
level-3 Reflect run, see `tests/test_call_window_reflect_run.py`) and
checks the `listen:room` view intent the block emits.

    python3 -m pytest -q blocks/call_window/tests/test_call_window.py
"""
from __future__ import annotations

from blocks.call_window import CallWindowBlock, LISTEN_OPS
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def _ctx(tmp_path, emitted):
    return BlockContext(instance=None, session_dir=tmp_path,
                        state=StateStore(tmp_path / "call-window"), agent=None,
                        llm=None, language="en", clock=lambda: 0.0,
                        emit=emitted.append, artefact=lambda name, text: None)


class FakeHost:
    """A fake segmenter: `open_call`/`close_call` flip `state()`'s shape,
    recording every call so the test can assert the block drove it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._state = "idle"

    def open_call(self, source: str) -> None:
        self.calls.append(("open", source))
        self._state = "listening"

    def close_call(self, source: str) -> None:
        self.calls.append(("close", source))
        self._state = "idle"

    def state(self) -> dict:
        return {"state": self._state, "since": None, "opened_by": None,
                "closes_in_s": None, "last_close": None}


def _action(op: str, person: str = "p1") -> CoreEvent:
    return CoreEvent("action", {"intent_id": "listen:room", "kind": "tap",
                                "person": person, "payload": {"op": op}, "at": 0.0})


def _new_block(tmp_path):
    emitted: list = []
    block = CallWindowBlock()
    block.start(_ctx(tmp_path, emitted), {})
    host = FakeHost()
    block.bind_host(open_call=host.open_call, close_call=host.close_call,
                    state=host.state)
    return block, host, emitted


def test_listen_ops_are_open_close_toggle():
    assert LISTEN_OPS == ("open", "close", "toggle")


def test_tap_open_calls_the_host_handle(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(_action("open"))
    assert host.calls == [("open", "tap")]


def test_tap_close_calls_the_host_handle(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(_action("open"))
    block.on_event(_action("close"))
    assert host.calls == [("open", "tap"), ("close", "tap")]


def test_toggle_opens_while_idle_then_closes_while_listening(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(_action("toggle"))
    assert host.calls == [("open", "tap")]
    block.on_event(_action("toggle"))
    assert host.calls == [("open", "tap"), ("close", "tap")]


def test_unknown_op_is_ignored(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(_action("bogus"))
    assert host.calls == []
    assert emitted == []


def test_action_emits_the_listen_room_intent(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(_action("open"))
    assert len(emitted) == 1
    intent = emitted[0]
    assert intent.id == "listen:room" and intent.kind == "listen"
    assert intent.payload["state"] == "listening"


def test_unbound_block_never_crashes_on_an_action(tmp_path):
    emitted: list = []
    block = CallWindowBlock()
    block.start(_ctx(tmp_path, emitted), {})
    out = block.on_event(_action("open"))
    assert out == ()
    assert emitted == []


def test_block_output_call_window_state_emits_the_intent(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(CoreEvent("block_output", {"call_window": {
        "state": "listening", "since": 12.0, "opened_by": "voice",
        "closes_in_s": 8.0, "last_close": None}}))
    assert len(emitted) == 1
    assert emitted[0].payload["opened_by"] == "voice"


def test_block_output_without_call_window_key_is_ignored(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    block.on_event(CoreEvent("block_output", {"other": "thing"}))
    assert emitted == []


def test_unchanged_state_does_not_re_emit(tmp_path):
    block, host, emitted = _new_block(tmp_path)
    payload = {"state": "idle", "since": None, "opened_by": None,
              "closes_in_s": None, "last_close": None}
    block.on_event(CoreEvent("block_output", {"call_window": payload}))
    block.on_event(CoreEvent("block_output", {"call_window": dict(payload)}))
    assert len(emitted) == 1
