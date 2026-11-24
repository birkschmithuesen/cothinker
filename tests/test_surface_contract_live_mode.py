"""Live mode (ADR 0019, issue #141): the same contract app and models as
fixture mode, fed by a running meeting's `buehne.json` instead of a
replayed scenario. Read-only -- the test never pretends to be the
consumer's `StageWriter`, it only writes a recorded sequence of
invented `buehne.json` snapshots to simulate one running.

`tests/fixtures/live_session/step-*.json` is that recorded sequence:
three invented stage-state snapshots, no real meeting content.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from surfaces.contract.__main__ import build_app, build_live_app
from surfaces.contract.live_player import LivePlayer
from surfaces.contract.models import StageStateSnapshot, Task, TranscriptLine, ViewIntent

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "live_session"
STEPS = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURE_DIR.glob("step-*.json"))]
VIEW_INTENT_ADAPTER = TypeAdapter(ViewIntent)


def _write_step(session_dir: Path, step: dict) -> None:
    (session_dir / "buehne.json").write_text(json.dumps(step), encoding="utf-8")


def _next_event_payload(stream, deadline: float) -> dict:
    for line in stream.iter_lines():
        if time.monotonic() > deadline:
            break
        if line and line.startswith("data:"):
            return json.loads(line[len("data: "):])
    raise AssertionError("no SSE event arrived before the deadline")


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    _write_step(tmp_path, STEPS[0])
    return tmp_path


def test_diff_maps_a_queue_change_to_a_queue_intent(session_dir):
    player = LivePlayer(session_dir)
    before = player.snapshot
    after = StageStateSnapshot.model_validate(STEPS[1])

    intents = player._diff(before, after)

    assert [intent.kind for intent in intents] == ["queue"]
    assert intents[0].speaking.pid == STEPS[1]["queue"]["speaking"]["pid"]


def test_diff_maps_a_non_owned_field_change_to_a_status_intent(session_dir):
    player = LivePlayer(session_dir)
    before = StageStateSnapshot.model_validate(STEPS[1])
    after = StageStateSnapshot.model_validate(STEPS[2])

    intents = player._diff(before, after)

    assert [intent.kind for intent in intents] == ["queue", "status"]
    assert "headline" in intents[1].text


def test_diff_maps_a_tasks_change_to_a_tasks_intent_not_a_status_intent(session_dir):
    """issue #147 point 3: tasks is now an owned field, like queue/share."""
    player = LivePlayer(session_dir)
    before = player.snapshot
    after = before.model_copy(update={
        "tasks": [Task(text="Draft the migration runbook.", person="Tamar", due="")],
    })

    intents = player._diff(before, after)

    assert [intent.kind for intent in intents] == ["tasks"]
    assert intents[0].tasks[0].text == "Draft the migration runbook."


def test_diff_maps_a_transcript_recent_change_to_a_transcript_recent_intent(session_dir):
    player = LivePlayer(session_dir)
    before = player.snapshot
    after = before.model_copy(update={
        "transcript_recent": [TranscriptLine(speaker="Mira", started_at="2026-11-23T09:00:00+01:00", text="hi")],
    })

    intents = player._diff(before, after)

    assert [intent.kind for intent in intents] == ["transcript:recent"]
    assert intents[0].lines[0].speaker == "Mira"


def test_live_mode_snapshot_endpoint_serves_the_mapped_state(session_dir):
    app = build_live_app(session_dir)
    with TestClient(app) as client:
        response = client.get("/snapshot")

        assert response.status_code == 200
        body = response.json()
        assert body["headline"] == STEPS[0]["headline"]
        assert body["main"]["id"] == STEPS[0]["main"]["id"]


def test_a_changed_buehne_json_produces_a_view_intent_within_two_seconds(session_dir):
    app = build_live_app(session_dir)
    with TestClient(app) as client:
        with client.stream("GET", "/events") as stream:
            assert stream.status_code == 200
            started = time.monotonic()
            _write_step(session_dir, STEPS[1])

            payload = _next_event_payload(stream, started + 2.0)

            assert time.monotonic() - started < 2.0
            assert payload["kind"] == "queue"
            assert payload["speaking"]["pid"] == STEPS[1]["queue"]["speaking"]["pid"]

        snapshot = client.get("/snapshot").json()
        assert snapshot["queue"]["speaking"]["pid"] == STEPS[1]["queue"]["speaking"]["pid"]


def test_live_pointer_mode_answers_503_before_the_pointer_resolves(tmp_path):
    """issue #161: a contract started before the consumer has opened its
    session must not crash (and so not crash-loop into a 502); it answers
    a plain 503 until the pointer names a readable session."""
    pointer = tmp_path / "session.path"
    app = build_live_app(session_pointer=pointer)
    with TestClient(app) as client:
        response = client.get("/snapshot")

        assert response.status_code == 503


def test_live_pointer_mode_serves_the_session_the_pointer_names_not_an_older_or_a_future_one(tmp_path):
    """issue #161: a prepared future-dated folder (only `agenda.md`, no
    `buehne.json` yet) and an older finished session both sit on disk; the
    contract must serve neither -- only the session the pointer names."""
    future = tmp_path / "2099-01-01-prepared"
    future.mkdir()
    (future / "agenda.md").write_text("- [ ] one\n", encoding="utf-8")

    older = tmp_path / "2020-01-01-old"
    older.mkdir()
    _write_step(older, STEPS[2])

    current = tmp_path / "2026-11-24-today"
    current.mkdir()
    _write_step(current, STEPS[0])

    pointer = tmp_path / "session.path"
    pointer.write_text(str(current), encoding="utf-8")

    app = build_live_app(session_pointer=pointer)
    with TestClient(app) as client:
        response = client.get("/snapshot")

        assert response.status_code == 200
        body = response.json()
        assert body["headline"] == STEPS[0]["headline"]


def test_live_pointer_mode_switches_session_when_the_pointer_changes_without_restarting(tmp_path):
    """issue #161: the consumer restarting into a new session must switch
    the already-running contract process, not require a restart of it."""
    first = tmp_path / "2026-11-24-first"
    first.mkdir()
    _write_step(first, STEPS[0])

    second = tmp_path / "2026-11-24-second"
    second.mkdir()
    _write_step(second, STEPS[2])

    pointer = tmp_path / "session.path"
    pointer.write_text(str(first), encoding="utf-8")

    app = build_live_app(session_pointer=pointer)
    with TestClient(app) as client:
        first_body = client.get("/snapshot").json()
        assert first_body["headline"] == STEPS[0]["headline"]

        pointer.write_text(str(second), encoding="utf-8")

        deadline = time.monotonic() + 5.0
        while True:
            response = client.get("/snapshot")
            if response.status_code == 200 and response.json()["headline"] == STEPS[2]["headline"]:
                break
            if time.monotonic() > deadline:
                raise AssertionError("contract did not follow the pointer switch in time")
            time.sleep(0.1)


def test_fixture_and_live_mode_payloads_validate_against_the_same_models(session_dir):
    fixture_app = build_app("meeting")
    with TestClient(fixture_app) as client:
        StageStateSnapshot.model_validate(client.get("/snapshot").json())
        with client.stream("GET", "/events") as stream:
            payload = _next_event_payload(stream, time.monotonic() + 5.0)
            VIEW_INTENT_ADAPTER.validate_python(payload)

    live_app = build_live_app(session_dir)
    with TestClient(live_app) as client:
        StageStateSnapshot.model_validate(client.get("/snapshot").json())
        with client.stream("GET", "/events") as stream:
            started = time.monotonic()
            _write_step(session_dir, STEPS[1])
            payload = _next_event_payload(stream, started + 2.0)
            VIEW_INTENT_ADAPTER.validate_python(payload)
