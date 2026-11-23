"""Fixture mode (ADR 0019 point 2, issue #132 acceptance #1):
`python -m surfaces.contract --fixtures meeting` must serve `/docs`, the
snapshot endpoint and the SSE push channel over the real app -- no
separate simulation.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from surfaces.contract.__main__ import build_app
from surfaces.contract.fixtures import load_scenario


def test_build_app_rejects_unknown_scenario():
    try:
        build_app("not-a-real-scenario")
    except ValueError as exc:
        assert "not-a-real-scenario" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown scenario")


def test_fixture_mode_serves_docs_snapshot_and_sse_stream():
    app = build_app("meeting")
    with TestClient(app) as client:
        docs = client.get("/docs")
        assert docs.status_code == 200
        assert "text/html" in docs.headers["content-type"]

        snapshot = client.get("/snapshot")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["headline"] == "Quarterly planning"
        assert body["main"]["id"]

        with client.stream("GET", "/events") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            lines = []
            for line in stream.iter_lines():
                if line:
                    lines.append(line)
                if any(l.startswith("event:") for l in lines) and \
                        any(l.startswith("data:") for l in lines):
                    break
            assert any(l == "event: view-intent" for l in lines)
            data_line = next(l for l in lines if l.startswith("data:"))
            payload = json.loads(data_line[len("data: "):])
            assert "kind" in payload


def test_meeting_scenario_matches_the_issue_132_beats():
    """agenda items advance, utterances with speakers, decisions, open
    questions, speaker queue, pause -- the scenario's invented timeline."""
    frames = load_scenario("meeting")
    kinds = {vi.kind for frame in frames for vi in frame.view_intents}
    assert "transcript:recent" in kinds   # utterances with speakers
    assert "status" in kinds              # pause
    assert "tasks" in kinds               # tasks named at the close
    agendas = [f.snapshot.agenda.current for f in frames]
    assert agendas == sorted(agendas)     # agenda items advance, never back
    decisions = [d.id for f in frames for d in f.snapshot.decisions]
    assert decisions                      # a decision is raised
    questions = [f.snapshot.question for f in frames if f.snapshot.question]
    assert any(q.state == "open" for q in questions)       # open question
    assert any(q.state == "resolved" for q in questions)   # later resolved
    speakers = {f.snapshot.agenda.speaker for f in frames}
    assert len(speakers) > 1              # the speaker queue turns over


def test_meeting_scenario_exercises_the_queue_snapshot_field():
    """issue #132 review finding: StageStateSnapshot.queue must be exercised
    by at least one frame, not left `None` throughout the fixture."""
    frames = load_scenario("meeting")
    queues = [f.snapshot.queue for f in frames if f.snapshot.queue is not None]
    assert queues, "no frame sets StageStateSnapshot.queue"
    speaking_pids = {q.speaking.pid for q in queues if q.speaking is not None}
    assert len(speaking_pids) > 1         # the speaker queue snapshot turns over too
