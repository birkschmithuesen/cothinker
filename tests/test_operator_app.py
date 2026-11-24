"""The operator console's FastAPI app (issue #159 acceptance list):
status/projects/events/start/stop/project, its own `/operator/` prefix,
its own token (never the participant one), and the exact HTTP statuses
the issue names.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from surfaces.operator.app import create_app
from surfaces.operator.fixtures import ALLOWED_PROJECTS, FixtureOperatorBackend, OperatorFrame

TOKEN = "op-s3cr3t-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _app(backend=None, **kwargs):
    return create_app(backend or FixtureOperatorBackend(), TOKEN, **kwargs)


def test_status_without_a_token_is_rejected_with_401():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/operator/status")
        assert response.status_code == 401


def test_status_with_the_wrong_token_is_rejected_with_401():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/operator/status", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401


def test_wrong_token_is_never_echoed_back_in_the_response_body():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/operator/status", headers={"Authorization": "Bearer my-wrong-guess"})
        assert "my-wrong-guess" not in response.text


def test_status_with_the_right_token_returns_the_full_snapshot():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/operator/status", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        for key in ("services", "meeting", "stt", "session", "call", "errors", "disk", "running", "can_switch_project"):
            assert key in body


def test_projects_returns_the_allowlist_never_a_free_path():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/operator/projects", headers=AUTH)
        assert response.status_code == 200
        ids = {p["id"] for p in response.json()}
        assert ids == {p.id for p in ALLOWED_PROJECTS}


def test_start_returns_202_and_the_resulting_status():
    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/operator/start",
            json={"project_id": ALLOWED_PROJECTS[0].id, "title": "Quarterly planning"},
            headers=AUTH,
        )
        assert response.status_code == 202
        assert response.json()["status"]["running"] is True


def test_start_while_already_running_returns_409():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    app = _app(backend)
    with TestClient(app) as client:
        response = client.post(
            "/operator/start",
            json={"project_id": ALLOWED_PROJECTS[0].id, "title": "Quarterly planning"},
            headers=AUTH,
        )
        assert response.status_code == 409
        assert response.json()["status"]["running"] is True


def test_start_with_a_project_outside_the_allowlist_returns_400_and_nothing_started():
    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/operator/start",
            json={"project_id": "not-a-real-project", "title": "Nope"},
            headers=AUTH,
        )
        assert response.status_code == 400
        assert response.json()["status"]["running"] is False

        follow_up = client.get("/operator/status", headers=AUTH)
        assert follow_up.json()["running"] is False


def test_stop_without_participants_returns_202():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    app = _app(backend)
    with TestClient(app) as client:
        response = client.post("/operator/stop", json={"confirm": True}, headers=AUTH)
        assert response.status_code == 202
        assert response.json()["status"]["running"] is False


def test_stop_with_participants_returns_409_with_the_participant_count():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    backend.current = backend.current.model_copy(update={
        "meeting": backend.current.meeting.model_copy(update={"count": 4}),
    })
    app = _app(backend)
    with TestClient(app) as client:
        response = client.post("/operator/stop", json={"confirm": True}, headers=AUTH)
        assert response.status_code == 409
        body = response.json()
        assert body["participant_count"] == 4
        assert body["status"]["running"] is True


def test_stop_with_participants_and_force_returns_202():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    backend.current = backend.current.model_copy(update={
        "meeting": backend.current.meeting.model_copy(update={"count": 4}),
    })
    app = _app(backend)
    with TestClient(app) as client:
        response = client.post("/operator/stop", json={"confirm": True, "force": True}, headers=AUTH)
        assert response.status_code == 202
        assert response.json()["status"]["running"] is False


def test_project_switch_while_running_returns_409():
    backend = FixtureOperatorBackend()
    backend.start(ALLOWED_PROJECTS[0].id, "Quarterly planning")
    app = _app(backend)
    with TestClient(app) as client:
        response = client.post("/operator/project", json={"project_id": ALLOWED_PROJECTS[1].id}, headers=AUTH)
        assert response.status_code == 409
        assert response.json()["status"]["running"] is True


def test_project_switch_while_stopped_returns_202_and_switches():
    app = _app()
    with TestClient(app) as client:
        response = client.post("/operator/project", json={"project_id": ALLOWED_PROJECTS[1].id}, headers=AUTH)
        assert response.status_code == 202
        assert response.json()["status"]["session"]["project"]["id"] == ALLOWED_PROJECTS[1].id


def test_project_switch_with_unknown_id_returns_400():
    app = _app()
    with TestClient(app) as client:
        response = client.post("/operator/project", json={"project_id": "not-a-real-project"}, headers=AUTH)
        assert response.status_code == 400


def test_events_stream_without_a_token_is_rejected_with_401():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/operator/events")
        assert response.status_code == 401


def test_events_stream_pushes_an_operator_status_event_with_the_full_snapshot():
    frames = [OperatorFrame(delay_s=0.01, status=FixtureOperatorBackend().current)]
    backend = FixtureOperatorBackend(frames=frames)
    app = _app(backend, push_interval_s=0.02)
    with TestClient(app) as client:
        with client.stream("GET", "/operator/events", headers=AUTH) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            lines: list[str] = []
            for line in stream.iter_lines():
                if line:
                    lines.append(line)
                if any(l == "event: operator-status" for l in lines) and any(l.startswith("data:") for l in lines):
                    break
            assert any(l == "event: operator-status" for l in lines)
            data_line = next(l for l in lines if l.startswith("data:"))
            payload = json.loads(data_line[len("data: "):])
            assert "running" in payload
            assert "services" in payload


def test_events_push_never_exceeds_the_configured_rate():
    backend = FixtureOperatorBackend()
    app = _app(backend, push_interval_s=0.2)
    with TestClient(app) as client:
        with client.stream("GET", "/operator/events", headers=AUTH) as stream:
            timestamps: list[float] = []
            deadline = time.monotonic() + 2.0
            for line in stream.iter_lines():
                if line and line.startswith("event:"):
                    timestamps.append(time.monotonic())
                    if len(timestamps) >= 1:
                        backend.current = backend.current.model_copy(update={"running": not backend.current.running})
                    if len(timestamps) >= 3:
                        break
                if time.monotonic() > deadline:
                    break
            assert len(timestamps) >= 2
            gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
            assert all(gap >= 0.18 for gap in gaps), gaps
