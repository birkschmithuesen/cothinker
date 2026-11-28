# CoThinker — meeting chat through the surface contract (issue #208)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Issue #208's acceptance, at contract level (ADR 0022 level 3):

1. a chat message `POST /actions` (live mode) becomes an agent request and
   its answer arrives on `/events` and in `/snapshot`;
2. without the voice path: no STT, no segmenter, no wake phrase;
3. both messages land in the session record (`protokoll.md`,
   `## Rohtranskript`), and a consumer restart adds nothing twice.

Real pieces: the contract app (`build_live_app`), `shared.actions_spool`,
`gateway.actions_reader.ActionsReader`, the block host with the real
block package discovery (`shared.block_registry.load_block`), `wire_meeting_chat`, `gateway.call_lane.CallLane`,
`gateway.stage_surface.StageSurface`, `gateway.stage_state.StageState`,
`gateway.raw_transcript.MinutesFile`, `surfaces.contract.live_player`.
Fake: the agent (a lane factory that answers at once) and the stage
writer (writes `state.content()` to `buehne.json`, as `StageWriter.write`
does, without its curation).

    python3 -m pytest -q tests/test_meeting_chat_contract.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))
import call_lane as call_lane_mod  # noqa: E402
from lanes import TIMEOUT_MARKER  # noqa: E402

from gateway.actions_reader import ActionsReader, dispatcher_for  # noqa: E402
from gateway.block_host_wiring import build_session_blocks, wire_meeting_chat  # noqa: E402
from gateway.raw_transcript import MinutesFile  # noqa: E402
from gateway.stage_state import StageState  # noqa: E402
from gateway.stage_surface import StageSurface  # noqa: E402
from gateway.sync_safe import atomic_write_text  # noqa: E402
from shared.actions_spool import ACTIONS_FILE  # noqa: E402
from surfaces.contract.__main__ import build_live_app  # noqa: E402
from surfaces.contract.models import StageStateSnapshot  # noqa: E402

MESSAGE = "Compare X and Y as an interactive page on the stage."
ANSWER = "X is faster, Y is cheaper; the page is on the stage."
CHAT = {"kind": "message", "intent_id": "chat:room", "text": MESSAGE}
NAME = {"kind": "edit", "intent_id": "participant:name", "text": "Person A"}


class FileWriter:
    """Stands in for `StageWriter`: publishes the state as `buehne.json`."""

    def __init__(self, path: Path, state: StageState) -> None:
        self.path, self.state = path, state

    def write(self) -> bool:
        atomic_write_text(self.path, json.dumps(self.state.content(), ensure_ascii=False))
        return True

    def warn(self, text: str) -> None:
        pass

    def clear_warning(self, only: str = "") -> None:
        pass


class AnsweringLane:
    """The fake agent: a call-lane factory whose lane answers every job at
    once with ANSWER (or fails it), recording what it was given."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.jobs: list = []

    def __call__(self, session_id, *, on_result, on_dropped, merge):
        self.on_result = on_result
        return self

    def submit(self, job) -> bool:
        self.jobs.append(job)
        result = ({"ok": True, "final": ANSWER, "error": ""} if self.ok
                  else {"ok": False, "final": "", "error": "boom"})
        self.on_result(job, result, 0.1)
        return False

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def stats(self) -> dict:
        return {}

    def waiting_count(self) -> int:
        return 0


class HoldingLane(AnsweringLane):
    """The fake agent that answers nothing by itself: the test ends each job
    the way the real lane can -- result, timeout, meta answer, dropped."""

    def __call__(self, session_id, *, on_result, on_dropped, merge):
        self.on_result, self.on_dropped, self.merge = on_result, on_dropped, merge
        return self

    def submit(self, job) -> bool:
        self.jobs.append(job)
        return False


class Session:
    """One consumer-shaped session over temp dirs, without STT or segmenter."""

    def __init__(self, root: Path, *, lane: AnsweringLane | None) -> None:
        self.session_dir = root / "2026-11-27-chat"
        self.session_dir.mkdir(exist_ok=True)
        self.spool_dir = root / "spool"
        self.protocol = self.session_dir / "protokoll.md"
        if not self.protocol.exists():
            self.protocol.write_text("# Chat test\n\n## Rohtranskript\n", encoding="utf-8")
        self.lane = lane
        self.logs: list[str] = []
        self.state = StageState()
        self.writer = FileWriter(self.session_dir / "buehne.json", self.state)
        surface = StageSurface(self.writer, self.state, self.logs.append)
        cell: list = []

        def sink(intent) -> None:   # the consumer's block_sink, minus the agenda
            if cell:
                current = {i.id: i for i in cell[0].intents.current()}
                current.setdefault(intent.id, intent)
                surface.render(list(current.values()))

        self.blocks = build_session_blocks(session_dir=self.session_dir, sink=sink,
                                           log=self.logs.append)
        cell.append(self.blocks)
        self.writer.write()
        self.minutes = MinutesFile(self.protocol, threading.RLock())
        ask = None
        if lane is not None:
            self.call_lane = call_lane_mod.CallLane(
                call_command=[], call_session_id="chat-test", project_agenda="PROJECT",
                state=self.state, writer=self.writer, call_lane_factory=lane,
                log=lambda *a: self.logs.append(" ".join(map(str, a))))
            self.call_lane.attach(self.blocks.dispatch)
            ask = self.call_lane.handle_chat
        wire_meeting_chat(self.blocks, record=self.minutes.append_raw, ask=ask,
                          log=self.logs.append)
        self.reader = ActionsReader(self.spool_dir / self.session_dir.name / ACTIONS_FILE,
                                    dispatcher_for(self.blocks), self.logs.append)

    def app(self):
        return build_live_app(self.session_dir, spool_dir=self.spool_dir)

    def raw_section(self) -> str:
        return self.protocol.read_text(encoding="utf-8").split("## Rohtranskript", 1)[1]


def _chat_events(stream, deadline: float):
    for line in stream.iter_lines():
        if time.monotonic() > deadline:
            break
        if line and line.startswith("data:"):
            payload = json.loads(line[len("data: "):])
            if payload["kind"] == "chat":
                yield payload


@pytest.fixture
def session(tmp_path):
    return Session(tmp_path, lane=AnsweringLane())


def _post_name_then_message(client) -> None:
    assert client.post("/actions", json=NAME).status_code == 202
    time.sleep(0.6)    # the per-person rate limit (MIN_ACTION_GAP_S)
    assert client.post("/actions", json=CHAT).status_code == 202


def test_a_chat_message_is_answered_on_events_and_in_the_snapshot(session):
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
        with client.stream("GET", "/events") as stream:
            assert session.reader.poll() == 2
            answered = None
            for intent in _chat_events(stream, time.monotonic() + 5.0):
                if any(m["role"] == "agent" for m in intent["messages"]):
                    answered = intent
                    break
        snapshot = client.get("/snapshot").json()

    assert answered is not None, f"no chat intent with the agent's answer; logs: {session.logs}"
    assert answered["id"] == "chat:room"
    mine, agent = answered["messages"]
    assert (mine["role"], mine["name"], mine["text"], mine["status"]) == (
        "participant", "Person A", MESSAGE, "answered")
    assert (agent["role"], agent["text"], agent["reply_to"]) == ("agent", ANSWER, mine["id"])
    chat = StageStateSnapshot.model_validate(snapshot).chat
    assert [m.text for m in chat.messages] == [MESSAGE, ANSWER]


def test_the_stage_path_of_the_answer_is_the_call_lanes_own(session):
    """Issue #208: results that belong on the stage still go there -- the
    call lane's `call_answer` panel carries the chat's answer too."""
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
    session.reader.poll()
    assert session.state.call_answer == f"Robo: {ANSWER}"


def test_a_chat_message_reaches_the_call_lane_without_the_voice_path(session):
    """No STT, no segmenter, no call window, no wake phrase: the message is
    one job on the call lane, the lane a spoken wake order goes to."""
    assert "robo" not in MESSAGE.lower()
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
    session.reader.poll()

    assert len(session.lane.jobs) == 1
    job = session.lane.jobs[0]
    assert MESSAGE in job.prompt
    assert job.meta["calls"][0]["chat_id"] == session.state.chat["messages"][0]["id"]
    assert session.state.listen is None      # the call window never opened


def test_both_messages_land_in_the_session_record(session):
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
    session.reader.poll()

    raw = session.raw_section()
    assert f"*Person A (Chat):* {MESSAGE}" in raw
    assert f"*Robo (Chat):* {ANSWER}" in raw
    assert raw.index("Person A (Chat)") < raw.index("Robo (Chat)")


def test_a_restart_replays_the_spool_without_asking_or_recording_twice(tmp_path):
    first = Session(tmp_path, lane=AnsweringLane())
    with TestClient(first.app()) as client:
        _post_name_then_message(client)
    first.reader.poll()
    before = first.protocol.read_text(encoding="utf-8")

    again = Session(tmp_path, lane=AnsweringLane())    # same dirs: a consumer restart
    again.reader.poll()                                 # the spool replays from byte 0

    assert again.lane.jobs == []
    assert again.protocol.read_text(encoding="utf-8") == before


def test_without_an_agent_that_answers_chat_the_message_is_unavailable(tmp_path):
    session = Session(tmp_path, lane=None)
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
    session.reader.poll()

    statuses = [m["status"] for m in session.state.chat["messages"]]
    assert statuses == ["unavailable"]
    assert f"*Person A (Chat):* {MESSAGE}" in session.raw_section()


def _held(tmp_path):
    """A session whose lane holds the job; one named message is in it."""
    session = Session(tmp_path, lane=HoldingLane())
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
    session.reader.poll()
    assert [m["status"] for m in session.state.chat["messages"]] == ["pending"]
    return session


def _statuses(session) -> list[str]:
    return [m["status"] for m in session.state.chat["messages"] if m["role"] == "participant"]


def test_a_dropped_chat_job_marks_the_message_failed(tmp_path):
    session = _held(tmp_path)
    session.lane.on_dropped(session.lane.jobs[0])
    assert _statuses(session) == ["failed"]


def test_a_timed_out_chat_job_marks_the_message_failed(tmp_path):
    session = _held(tmp_path)
    session.lane.on_result(session.lane.jobs[0],
                           {"ok": False, "final": "", "error": f"{TIMEOUT_MARKER} 120s"}, 120.0)
    assert session.state.mirror == call_lane_mod.TIMEOUT_ABORT_TEXT     # the timeout branch ran
    assert _statuses(session) == ["failed"]


def test_a_meta_answer_marks_the_message_failed_and_shows_no_agent_message(tmp_path):
    session = _held(tmp_path)
    session.lane.on_result(session.lane.jobs[0], {"ok": True, "final": "none", "error": ""}, 1.0)
    assert _statuses(session) == ["failed"]
    assert [m["role"] for m in session.state.chat["messages"]] == ["participant"]


def test_a_merged_job_answers_every_chat_message_in_it(tmp_path):
    """Spec Q-c: a merged job answers every chat id it carries."""
    session = Session(tmp_path, lane=HoldingLane())
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
        time.sleep(0.6)
        assert client.post("/actions", json={**CHAT, "text": "And summarise the risks."}).status_code == 202
    session.reader.poll()
    first, second = session.lane.jobs
    merged = session.lane.merge(first, second)
    assert len(merged.meta["calls"]) == 2

    session.lane.on_result(merged, {"ok": True, "final": ANSWER, "error": ""}, 1.0)

    assert _statuses(session) == ["answered", "answered"]


def test_a_typed_order_enters_the_call_lanes_context_like_a_spoken_line(tmp_path):
    session = _held(tmp_path)
    assert f"Person A (Chat): {MESSAGE}" in session.lane.jobs[0].prompt


def test_a_failed_agent_turn_marks_the_message_failed(tmp_path):
    session = Session(tmp_path, lane=AnsweringLane(ok=False))
    with TestClient(session.app()) as client:
        _post_name_then_message(client)
    session.reader.poll()

    assert [m["status"] for m in session.state.chat["messages"]] == ["failed"]
