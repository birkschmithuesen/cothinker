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
from surfaces.contract.models import StageStateSnapshot, ViewIntent

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
