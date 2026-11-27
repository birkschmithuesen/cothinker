# CoThinker — the meeting chat's contract shapes (issue #208)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`surfaces/contract/models.py` is the single source of the chat's shapes:
the `chat` intent kind is known everywhere it must be, the fixture plays a
chat exchange against those models, and live mode maps a changed `chat`
field of `buehne.json` to one `chat:room` intent.

    python3 -m pytest -q tests/test_meeting_chat_models.py
"""
from __future__ import annotations

import json

from pydantic import TypeAdapter

from shared.surface import INTENT_KINDS
from surfaces.contract.fixtures import load_scenario
from surfaces.contract.live_player import LivePlayer
from surfaces.contract.models import (ChatIntent, MessageAction, StageStateSnapshot,
                                      ViewIntent)

VIEW_INTENT = TypeAdapter(ViewIntent)
CHAT = {"messages": [{"id": "m1", "role": "participant", "name": "Person A",
                      "text": "Compare X and Y", "at": "2026-11-27T10:00:00+01:00",
                      "reply_to": None, "status": "pending"}]}


def test_chat_is_a_known_intent_kind_on_both_sides():
    assert "chat" in INTENT_KINDS
    parsed = VIEW_INTENT.validate_python({"kind": "chat", "id": "chat:room", **CHAT})
    assert isinstance(parsed, ChatIntent)


def test_a_message_action_needs_no_person_from_the_surface():
    action = MessageAction(intent_id="chat:room", text="hello")
    assert action.person == ""


def test_the_fixture_plays_a_chat_message_then_its_answer():
    frames = load_scenario("meeting")
    chats = [vi for f in frames for vi in f.view_intents if vi.kind == "chat"]
    assert [[m.status for m in c.messages] for c in chats] == [["pending"], ["answered", "answered"]]
    assert chats[-1].messages[1].role == "agent"
    assert chats[-1].messages[1].reply_to == chats[-1].messages[0].id
    assert frames[-1].snapshot.chat.messages == chats[-1].messages


def test_live_mode_maps_a_chat_change_to_one_chat_intent(tmp_path):
    fixture = load_scenario("meeting")[0].snapshot.model_dump()
    fixture.pop("chat", None)
    (tmp_path / "buehne.json").write_text(json.dumps(fixture), encoding="utf-8")
    player = LivePlayer(tmp_path)
    after = StageStateSnapshot.model_validate({**fixture, "chat": CHAT})

    intents = player._diff(player.snapshot, after)

    assert [(i.kind, i.id) for i in intents] == [("chat", "chat:room")]
    assert intents[0].messages[0].text == "Compare X and Y"
