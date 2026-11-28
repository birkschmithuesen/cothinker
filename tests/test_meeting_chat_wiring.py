# CoThinker — the session host's side of the meeting chat (issue #208)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`gateway.block_host_wiring.wire_meeting_chat` over the real block host
(real block package discovery): the record goes to the record writer, the request
to the agent, and a broken step never leaves a message pending.

    python3 -m pytest -q tests/test_meeting_chat_wiring.py
"""
from __future__ import annotations

from gateway.actions_reader import action_payload
from gateway.block_host_wiring import build_session_blocks, wire_meeting_chat
from shared.surface import UserAction

PID = "a1a1a1a1a1a1a1a1"


def _blocks(tmp_path):
    sink: list = []
    logs: list = []
    blocks = build_session_blocks(session_dir=tmp_path, sink=sink.append, log=logs.append)
    return blocks, sink, logs


def _send(blocks, text="Compare X and Y", at=1732352400.0):
    blocks.dispatch("action", action_payload(UserAction(
        intent_id="chat:room", kind="message", person=PID, payload={"text": text}, at=at)))


def _statuses(sink):
    chats = [i for i in sink if i.kind == "chat"]
    return [m["status"] for m in chats[-1].payload["messages"]] if chats else []


def test_the_record_comes_before_the_request(tmp_path):
    blocks, _, _ = _blocks(tmp_path)
    calls: list = []
    wire_meeting_chat(blocks, record=lambda text, speaker: calls.append(("record", speaker, text)),
                      ask=lambda mid, name, text: calls.append(("ask", name, text)))
    _send(blocks)
    assert calls == [("record", "Guest (Chat)", "Compare X and Y"), ("ask", "", "Compare X and Y")]


def test_without_an_agent_the_message_is_answered_unavailable(tmp_path):
    blocks, sink, _ = _blocks(tmp_path)
    wire_meeting_chat(blocks, record=lambda text, speaker: None, ask=None)
    _send(blocks)
    assert _statuses(sink) == ["unavailable"]


def test_a_failing_agent_call_marks_the_message_failed_and_logs_no_text(tmp_path):
    blocks, sink, logs = _blocks(tmp_path)

    def broken(mid, name, text):
        raise RuntimeError(text)

    log: list = []
    wire_meeting_chat(blocks, record=lambda text, speaker: None, ask=broken, log=log.append)
    _send(blocks, text="secret plan")
    assert _statuses(sink) == ["failed"]
    assert log and not any("secret" in line for line in log + logs)


def test_a_failing_record_still_asks_the_agent(tmp_path):
    blocks, _, _ = _blocks(tmp_path)
    asked: list = []

    def broken(text, speaker):
        raise OSError("disk full")

    wire_meeting_chat(blocks, record=broken, ask=lambda mid, name, text: asked.append(text),
                      log=lambda line: None)
    _send(blocks)
    assert asked == ["Compare X and Y"]
