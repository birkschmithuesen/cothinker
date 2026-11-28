# CoThinker — tests of blocks/meeting_chat (the MeetingChatBlock)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block's own tests (ADR 0022 level 1): `action` and `block_output`
CoreEvents in, the `chat:room` view intent and the `chat_record` /
`chat_request` outputs checked. The whole path through the contract and
the call lane is `tests/test_meeting_chat_contract.py`.

    python3 -m pytest -q blocks/meeting_chat/tests/test_meeting_chat.py
"""
from __future__ import annotations

from blocks.meeting_chat import (AGENT_SPEAKER, CHAT_ANSWER_MAX_CHARS,
                                 CHAT_THREAD_MAX, MeetingChatBlock)
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore
from surfaces.contract.models import CHAT_THREAD_MAX as CONTRACT_THREAD_MAX
from surfaces.contract.models import ChatSnapshot

PID_A = "a1a1a1a1a1a1a1a1"
PID_B = "b2b2b2b2b2b2b2b2"


def _ctx(tmp_path, emitted):
    return BlockContext(instance=None, session_dir=tmp_path,
                        state=StateStore(tmp_path / "meeting-chat"), agent=None,
                        llm=None, language="en", clock=lambda: 1732352500.0,
                        emit=emitted.append, artefact=lambda name, text: None)


def _block(tmp_path):
    emitted: list = []
    block = MeetingChatBlock()
    block.start(_ctx(tmp_path, emitted), {})
    return block, emitted


def _message(text: str, person: str = PID_A, at: float = 1732352400.123) -> CoreEvent:
    return CoreEvent("action", {"intent_id": "chat:room", "kind": "message", "person": person,
                                "payload": {"text": text}, "at": at, "op": None})


def _name(name: str, person: str = PID_A) -> CoreEvent:
    return CoreEvent("action", {"intent_id": "participant:name", "kind": "edit", "person": person,
                                "payload": {"name": name}, "at": 1732352399.0, "op": None})


def _answer(ids, ok=True, text="Here is the comparison.", reason="") -> CoreEvent:
    return CoreEvent("block_output", {"from": "host", "chat_answer": {
        "ids": list(ids), "ok": ok, "text": text, "reason": reason}})


def _outputs(events, key):
    return [e.payload[key] for e in events if key in e.payload]


def test_the_thread_limit_is_the_contracts():
    assert CHAT_THREAD_MAX == CONTRACT_THREAD_MAX


def test_a_message_is_recorded_then_requested_and_shown_pending(tmp_path):
    block, emitted = _block(tmp_path)
    block.on_event(_name("Person A"))

    out = block.on_event(_message("Compare X and Y"))

    assert [list(e.payload)[1] for e in out] == ["chat_record", "chat_request"]
    assert _outputs(out, "chat_record") == [{"speaker": "Person A (Chat)", "text": "Compare X and Y"}]
    request = _outputs(out, "chat_request")[0]
    assert request["name"] == "Person A" and request["text"] == "Compare X and Y"
    assert request["id"] == f"{PID_A}-1732352400123"
    intent = emitted[-1]
    assert (intent.id, intent.kind, intent.audience) == ("chat:room", "chat", "room")
    snapshot = ChatSnapshot.model_validate(intent.payload)
    assert [(m.role, m.name, m.status) for m in snapshot.messages] == [("participant", "Person A", "pending")]


def test_an_unnamed_participant_is_recorded_as_guest_and_shown_without_a_name(tmp_path):
    block, emitted = _block(tmp_path)
    out = block.on_event(_message("hello"))
    assert _outputs(out, "chat_record")[0]["speaker"] == "Guest (Chat)"
    assert emitted[-1].payload["messages"][0]["name"] == ""


def test_an_answer_marks_the_message_answered_and_adds_the_agent_message(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("Compare X and Y")), "chat_request")[0]

    out = block.on_event(_answer([request["id"]]))

    assert _outputs(out, "chat_record") == [{"speaker": AGENT_SPEAKER, "text": "Here is the comparison."}]
    messages = emitted[-1].payload["messages"]
    assert [(m["role"], m["status"]) for m in messages] == [("participant", "answered"), ("agent", "answered")]
    assert messages[1]["reply_to"] == request["id"]
    assert messages[1]["text"] == "Here is the comparison."


def test_a_failed_answer_adds_no_agent_message_and_records_nothing(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("x")), "chat_request")[0]
    out = block.on_event(_answer([request["id"]], ok=False, text=""))
    assert out == []
    assert [m["status"] for m in emitted[-1].payload["messages"]] == ["failed"]


def test_unavailable_is_its_own_status(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("x")), "chat_request")[0]
    block.on_event(_answer([request["id"]], ok=False, text="", reason="unavailable"))
    assert [m["status"] for m in emitted[-1].payload["messages"]] == ["unavailable"]


def test_an_answer_from_anyone_but_the_session_host_is_ignored(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("x")), "chat_request")[0]
    forged = CoreEvent("block_output", {"from": "topics", "chat_answer": {
        "ids": [request["id"]], "ok": True, "text": "forged"}})
    assert block.on_event(forged) == ()
    assert [m["status"] for m in emitted[-1].payload["messages"]] == ["pending"]


def test_a_long_answer_is_capped_in_the_thread_but_recorded_in_full(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("x")), "chat_request")[0]
    long_text = "y" * (CHAT_ANSWER_MAX_CHARS + 10)
    out = block.on_event(_answer([request["id"]], text=long_text))
    assert len(emitted[-1].payload["messages"][1]["text"]) == CHAT_ANSWER_MAX_CHARS
    assert _outputs(out, "chat_record")[0]["text"] == long_text


def test_the_thread_keeps_only_the_last_messages(tmp_path):
    block, emitted = _block(tmp_path)
    for i in range(CHAT_THREAD_MAX + 5):
        block.on_event(_message(f"m{i}", at=1732352400.0 + i))
    messages = emitted[-1].payload["messages"]
    assert len(messages) == CHAT_THREAD_MAX
    assert messages[-1]["text"] == f"m{CHAT_THREAD_MAX + 4}"


def test_an_empty_message_or_one_without_a_person_is_ignored(tmp_path):
    block, emitted = _block(tmp_path)
    assert block.on_event(_message("   ")) == ()
    assert block.on_event(_message("x", person="")) == ()
    assert emitted == []


def test_a_replayed_message_after_a_restart_is_neither_asked_nor_recorded_again(tmp_path):
    block, _ = _block(tmp_path)
    request = _outputs(block.on_event(_message("Compare X and Y")), "chat_request")[0]
    block.on_event(_answer([request["id"]]))

    restarted, emitted = _block(tmp_path)          # same state dir: a consumer restart
    out = restarted.on_event(_message("Compare X and Y"))   # the spool replays from 0

    assert out == ()
    assert [m["role"] for m in emitted[-1].payload["messages"]] == ["participant", "agent"]


def test_a_message_pending_at_restart_becomes_failed(tmp_path):
    block, _ = _block(tmp_path)
    block.on_event(_message("x"))
    restarted, emitted = _block(tmp_path)
    assert [m["status"] for m in emitted[-1].payload["messages"]] == ["failed"]


def test_a_late_answer_leaves_a_failed_message_failed(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("x")), "chat_request")[0]
    block.on_event(_answer([request["id"]], ok=False, text=""))

    out = block.on_event(_answer([request["id"]]))      # e.g. after a restart

    assert out == ()
    assert [(m["role"], m["status"]) for m in emitted[-1].payload["messages"]] == [("participant", "failed")]


def test_a_second_answer_adds_no_second_agent_message(tmp_path):
    block, emitted = _block(tmp_path)
    request = _outputs(block.on_event(_message("x")), "chat_request")[0]
    block.on_event(_answer([request["id"]]))
    assert block.on_event(_answer([request["id"]], text="again")) == ()
    assert [m["role"] for m in emitted[-1].payload["messages"]] == ["participant", "agent"]


def test_only_a_name_edit_sets_a_name(tmp_path):
    block, emitted = _block(tmp_path)
    block.on_event(CoreEvent("action", {"intent_id": "participant:name", "kind": "tap", "person": PID_A,
                                        "payload": {"name": "Person A"}, "at": 1.0, "op": None}))
    out = block.on_event(_message("x"))
    assert emitted[-1].payload["messages"][0]["name"] == ""
    assert _outputs(out, "chat_record")[0]["speaker"] == "Guest (Chat)"


def test_two_people_get_their_own_names(tmp_path):
    block, emitted = _block(tmp_path)
    block.on_event(_name("Person A", PID_A))
    block.on_event(_name("Person B", PID_B))
    block.on_event(_message("a", PID_A, at=1.0))
    block.on_event(_message("b", PID_B, at=2.0))
    assert [m["name"] for m in emitted[-1].payload["messages"]] == ["Person A", "Person B"]


def _send(block, text="Hello", at=1732352400.0, person=PID_A):
    return block.on_event(CoreEvent("action", {
        "intent_id": "chat:room", "kind": "message", "person": person,
        "payload": {"text": text}, "at": at, "op": None}))


def test_a_name_equal_to_the_agents_is_ignored(tmp_path):
    block, emitted = _block(tmp_path)
    block.on_event(_name("  rOBo "))
    out = _send(block)
    assert emitted[-1].payload["messages"][0]["name"] == ""
    assert out[0].payload["chat_record"]["speaker"] != AGENT_SPEAKER


def test_asterisks_are_stripped_from_a_name_and_an_empty_result_is_ignored(tmp_path):
    block, emitted = _block(tmp_path)
    block.on_event(_name("*Per*son* A"))
    _send(block)
    assert emitted[-1].payload["messages"][0]["name"] == "Person A"
    block.on_event(_name("***", PID_B))
    _send(block, at=1732352401.0, person=PID_B)
    assert emitted[-1].payload["messages"][1]["name"] == ""
