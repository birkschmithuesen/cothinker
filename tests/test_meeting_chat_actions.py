# CoThinker — the meeting chat's validation rules on POST /actions (issue #208)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""One test per rule, each red when only its rule is removed (ADR 0022
level 1, through the app's own `TestClient`):

R1 a `message` is accepted in live mode only for `intent_id == "chat:room"`;
R2 its text is at most `MESSAGE_MAX_CHARS` (the model's `max_length`);
R3 a blank text is refused;
R4 the per-person rate limit holds for messages too;
R5 the spooled person is the cookie pid, never the body's `person`;
R6 a `participant:name` edit needs a non-blank, one-line name of at most
   `NAME_MAX_CHARS`.

    python3 -m pytest -q tests/test_meeting_chat_actions.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.actions_spool import ACTIONS_FILE
from surfaces.contract.__main__ import build_app, build_live_app
from surfaces.contract.app import NAME_MAX_CHARS
from surfaces.contract.models import MESSAGE_MAX_CHARS

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "live_session"
STEP_0 = json.loads((FIXTURE_DIR / "step-0.json").read_text(encoding="utf-8"))


def _chat(text: str = "Compare X and Y", **extra) -> dict:
    return {"kind": "message", "intent_id": "chat:room", "text": text, **extra}


def _name(text: str) -> dict:
    return {"kind": "edit", "intent_id": "participant:name", "text": text}


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    live = tmp_path / "2026-11-27-today"
    live.mkdir()
    (live / "buehne.json").write_text(json.dumps(STEP_0), encoding="utf-8")
    return live


@pytest.fixture
def spool_dir(tmp_path: Path) -> Path:
    return tmp_path / "spool"


def _post(session_dir, spool_dir, action):
    with TestClient(build_live_app(session_dir, spool_dir=spool_dir)) as client:
        return client.post("/actions", json=action)


def _lines(session_dir, spool_dir) -> list[dict]:
    path = spool_dir / session_dir.name / ACTIONS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_chat_message_is_spooled_with_its_text(session_dir, spool_dir):
    response = _post(session_dir, spool_dir, _chat("  Compare X and Y  "))
    assert response.status_code == 202
    assert response.json() == {"received": "message", "mode": "live"}
    (line,) = _lines(session_dir, spool_dir)
    assert (line["intent_id"], line["kind"], line["payload"]) == (
        "chat:room", "message", {"text": "Compare X and Y"})


def test_r1_a_message_to_any_other_intent_is_refused(session_dir, spool_dir):
    response = _post(session_dir, spool_dir, _chat(intent_id="VI1"))
    assert response.status_code == 422
    assert _lines(session_dir, spool_dir) == []


def test_r2_a_message_one_over_the_limit_is_refused_and_one_at_it_is_not(session_dir, spool_dir):
    assert _post(session_dir, spool_dir, _chat("x" * (MESSAGE_MAX_CHARS + 1))).status_code == 422
    assert _post(session_dir, spool_dir, _chat("x" * MESSAGE_MAX_CHARS)).status_code == 202


def test_r3_a_blank_message_is_refused(session_dir, spool_dir):
    assert _post(session_dir, spool_dir, _chat("  \n\t ")).status_code == 422
    assert _lines(session_dir, spool_dir) == []


def test_r4_a_second_message_too_soon_is_rate_limited(session_dir, spool_dir):
    with TestClient(build_live_app(session_dir, spool_dir=spool_dir)) as client:
        first = client.post("/actions", json=_chat("one"))
        second = client.post("/actions", json=_chat("two"))
    assert (first.status_code, second.status_code) == (202, 429)
    assert [line["payload"]["text"] for line in _lines(session_dir, spool_dir)] == ["one"]


def test_r5_the_spooled_person_is_the_cookie_not_the_body(session_dir, spool_dir):
    response = _post(session_dir, spool_dir, _chat(person="someone-else"))
    (line,) = _lines(session_dir, spool_dir)
    assert line["person"] == response.cookies["cothinker_pid"]
    assert "someone-else" not in json.dumps(line)


@pytest.mark.parametrize("name", ["", "   ", "x" * (NAME_MAX_CHARS + 1), "Person\nA"])
def test_r6_a_bad_display_name_is_refused(session_dir, spool_dir, name):
    assert _post(session_dir, spool_dir, _name(name)).status_code == 422
    assert _lines(session_dir, spool_dir) == []


def test_r6_a_good_display_name_is_spooled_as_the_stages_join_writes_it(session_dir, spool_dir):
    assert _post(session_dir, spool_dir, _name(" Person A ")).status_code == 202
    (line,) = _lines(session_dir, spool_dir)
    assert (line["intent_id"], line["kind"], line["payload"]) == (
        "participant:name", "edit", {"name": "Person A"})


def test_an_edit_of_any_other_intent_is_still_refused(session_dir, spool_dir):
    action = {"kind": "edit", "intent_id": "VI1", "text": "x"}
    assert _post(session_dir, spool_dir, action).status_code == 422


def test_fixture_mode_accepts_a_chat_message_without_acting(tmp_path):
    with TestClient(build_app("meeting")) as client:
        response = client.post("/actions", json=_chat())
    assert response.json() == {"received": "message", "mode": "fixture"}


def test_fixture_mode_keeps_the_length_limit_too(tmp_path):
    with TestClient(build_app("meeting")) as client:
        response = client.post("/actions", json=_chat("x" * (MESSAGE_MAX_CHARS + 1)))
    assert response.status_code == 422
