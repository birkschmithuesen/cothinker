"""Fixture mode (ADR 0019 point 2, issue #132 acceptance #1):
`python -m surfaces.contract --fixtures meeting` must serve `/docs`, the
snapshot endpoint and the SSE push channel over the real app -- no
separate simulation.
"""
from __future__ import annotations

import json
import time

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


def test_meeting_scenario_emits_voice_changes():
    """issue #164 acceptance: "fixture mode emits voice changes" -- voice
    is scripted independently of the speaker queue to make the
    distinction legible (a room display reading voice.speaking must not
    depend on anyone tapping to join the queue)."""
    frames = load_scenario("meeting")
    kinds = {vi.kind for frame in frames for vi in frame.view_intents}
    assert "voice" in kinds

    voices = [f.snapshot.voice for f in frames if f.snapshot.voice is not None]
    assert voices, "no frame sets StageStateSnapshot.voice"
    speakers = {v.speaking for v in voices if v.speaking is not None}
    assert len(speakers) > 1   # voice.speaking turns over too

    voice_speakers_by_frame = [f.snapshot.voice.speaking for f in frames if f.snapshot.voice is not None]
    queue_speakers_by_frame = [f.snapshot.queue.speaking.name for f in frames
                               if f.snapshot.queue is not None and f.snapshot.queue.speaking is not None]
    assert voice_speakers_by_frame != queue_speakers_by_frame   # independent of the speaker queue


def test_meeting_scenario_exercises_the_queue_snapshot_field():
    """issue #132 review finding: StageStateSnapshot.queue must be exercised
    by at least one frame, not left `None` throughout the fixture."""
    frames = load_scenario("meeting")
    queues = [f.snapshot.queue for f in frames if f.snapshot.queue is not None]
    assert queues, "no frame sets StageStateSnapshot.queue"
    speaking_pids = {q.speaking.pid for q in queues if q.speaking is not None}
    assert len(speaking_pids) > 1         # the speaker queue snapshot turns over too


def test_meeting_scenario_shows_one_listening_phase():
    """issue #180, ADR 0024: the fixture demonstrates the call window
    opening (voice) and closing at least once, not left idle throughout
    -- so a surface can design the listen:room intent against real data."""
    frames = load_scenario("meeting")
    kinds = {vi.kind for frame in frames for vi in frame.view_intents}
    assert "listen" in kinds
    states = [f.snapshot.listen.state for f in frames if f.snapshot.listen is not None]
    assert "listening" in states
    assert "idle" in states
    closes = [f.snapshot.listen.last_close for f in frames
             if f.snapshot.listen is not None and f.snapshot.listen.last_close is not None]
    assert closes, "no frame shows the call window's last_close"


def test_meeting_scenario_agenda_times_are_relative_to_now():
    """issue #147 point 2: a hardcoded epoch (2024-11-23) made the item's
    remaining time overrun by two years. Fixture times must be anchored to
    the moment the scenario is loaded, not to a fixed past date."""
    before = time.time()
    frames = load_scenario("meeting")
    after = time.time()

    first_agenda = frames[0].snapshot.agenda
    assert before <= first_agenda.item_started_at <= after
    assert first_agenda.meeting_deadline > after   # still ahead, not overrun


def test_meeting_scenario_fills_the_whole_agenda_items_list():
    """issue #147 point 4: fixture mode must fill `agenda.items`, not leave
    it empty, so a surface can design against the whole-agenda shape."""
    frames = load_scenario("meeting")
    agendas = [f.snapshot.agenda for f in frames if f.snapshot.agenda is not None]
    assert agendas
    for agenda in agendas:
        assert agenda.items, "agenda.items must be filled, not empty"
        assert len(agenda.items) == agenda.total

    timer_intents = [vi for f in frames for vi in f.view_intents if vi.kind == "timer"]
    assert timer_intents
    assert all(ti.items for ti in timer_intents), "timer intents must carry agenda.items too"
