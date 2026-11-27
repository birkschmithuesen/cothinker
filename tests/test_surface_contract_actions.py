"""`POST /actions` acts in live mode (issue #183): one write path for every
surface, through `shared.actions_spool`, so a surface can act through the
contract instead of only through the stage's own `/action`.

L1 (ADR 0022): the live-mode allowlist, rate limit and per-person cookie,
tested through the app's own `TestClient` (in-process, no network).
L2: a `TapAction` posted to a live-mode app lands in the session's action
spool, and `gateway/actions_reader.py` parses it into the same `UserAction`
the stage's own line would produce (byte-compatible lines).
L3: the same tap, read back by `ActionsReader` and dispatched through the
REAL block host (`shared.block_registry.load_block`, no stub), reaches
the real `speaker-queue` block and changes who holds the floor.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.block_registry import load_block
from gateway.actions_reader import ActionsReader, dispatcher_for
from gateway.block_host_wiring import build_session_blocks
from shared.actions_spool import ACTIONS_FILE
from surfaces.contract.__main__ import build_app, build_live_app

# Matches tests/test_stage_actions.py's own import idiom: `stage/stage.py`
# has no package `__init__.py`, so it is loaded as a top-level module named
# "stage" via this sys.path entry, not as "stage.stage" -- importing it
# the other way here would collide with that module identity in the same
# pytest session.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage"))
import stage as stage_module  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "live_session"
STEP_0 = json.loads((FIXTURE_DIR / "step-0.json").read_text(encoding="utf-8"))

QUEUE_TAP = {"kind": "tap", "intent_id": "queue:room", "target": "queue-button",
             "payload": {"op": "request"}}
PID_RE = re.compile(r"[0-9a-f]{16}")


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    live = tmp_path / "2026-11-24-today"
    live.mkdir()
    (live / "buehne.json").write_text(json.dumps(STEP_0), encoding="utf-8")
    return live


@pytest.fixture
def spool_dir(tmp_path: Path) -> Path:
    return tmp_path / "spool"


# ---- Fixture mode: unchanged "accepted, not acted on" behaviour ----------


def test_fixture_mode_accepts_without_acting(tmp_path: Path):
    app = build_app("meeting")
    with TestClient(app) as client:
        response = client.post("/actions", json=QUEUE_TAP)

    assert response.status_code == 202
    assert response.json() == {"received": "tap", "mode": "fixture"}
    assert not (tmp_path / "spool").exists()


# ---- Live mode: 202, the cookie, the spool line ---------------------------


def test_live_mode_accepted_action_is_202_and_sets_a_person_cookie(session_dir, spool_dir):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json=QUEUE_TAP)

    assert response.status_code == 202
    assert response.json() == {"received": "tap", "mode": "live"}
    pid = response.cookies.get("cothinker_pid")
    assert pid and PID_RE.fullmatch(pid)


def test_live_mode_writes_the_session_spool_file(session_dir, spool_dir):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json=QUEUE_TAP)

    pid = response.cookies["cothinker_pid"]
    path = spool_dir / session_dir.name / ACTIONS_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d["v"] == 1
    assert d["person"] == pid
    assert d["intent_id"] == "queue:room"
    assert d["kind"] == "tap"
    assert d["payload"] == {"op": "request"}


def test_live_mode_reuses_the_existing_cookie_as_the_same_person(session_dir, spool_dir):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        first = client.post("/actions", json=QUEUE_TAP)
        pid = first.cookies["cothinker_pid"]
        time.sleep(0.6)   # clear the per-person rate limit gap
        second = client.post("/actions", json=QUEUE_TAP)

    assert second.cookies.get("cothinker_pid") in (None, pid)
    path = spool_dir / session_dir.name / ACTIONS_FILE
    persons = [json.loads(line)["person"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert persons == [pid, pid]


# ---- Allowlist: queue:room request/done, listen:room open/close/toggle ----


@pytest.mark.parametrize("action", [
    {"kind": "tap", "target": "status-badge"},                                        # no intent_id: a navigation tap
    {"kind": "tap", "intent_id": "queue:room", "target": "x", "payload": {"op": "bogus"}},
    {"kind": "tap", "intent_id": "queue:room", "target": "x"},                          # no payload at all
    {"kind": "tap", "intent_id": "listen:room", "target": "x", "payload": {"op": "request"}},  # a queue op on the call window
    {"kind": "accept", "intent_id": "VI1"},
    {"kind": "message", "intent_id": "VI1", "person": "P3", "text": "hi"},
])
def test_live_mode_rejects_a_disallowed_action_with_422(session_dir, spool_dir, action):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json=action)

    assert response.status_code == 422
    assert not (spool_dir / session_dir.name / ACTIONS_FILE).exists()


def test_live_mode_allows_the_done_op_too(session_dir, spool_dir):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json={
            "kind": "tap", "intent_id": "queue:room", "target": "x", "payload": {"op": "done"}})

    assert response.status_code == 202


@pytest.mark.parametrize("op", ["open", "close", "toggle"])
def test_live_mode_allows_the_call_window_ops(session_dir, spool_dir, op):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json={
            "kind": "tap", "intent_id": "listen:room", "target": "listen-toggle", "payload": {"op": op}})

    assert response.status_code == 202
    line = json.loads((spool_dir / session_dir.name / ACTIONS_FILE).read_text().splitlines()[-1])
    assert line["intent_id"] == "listen:room" and line["payload"] == {"op": op}


# ---- Rate limit ------------------------------------------------------------


def test_live_mode_rate_limits_a_second_action_too_soon(session_dir, spool_dir):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        first = client.post("/actions", json=QUEUE_TAP)
        second = client.post("/actions", json=QUEUE_TAP)

    assert first.status_code == 202
    assert second.status_code == 429
    path = spool_dir / session_dir.name / ACTIONS_FILE
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


# ---- 503 before a live-pointer session resolves ---------------------------


def test_live_pointer_mode_answers_503_before_the_pointer_resolves(tmp_path, spool_dir):
    pointer = tmp_path / "session.path"
    app = build_live_app(session_pointer=pointer, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json=QUEUE_TAP)

    assert response.status_code == 503


# ---- L2: byte-compatible with the stage's own spool line ------------------


def test_the_contract_and_the_stage_write_byte_compatible_lines(session_dir, spool_dir, monkeypatch):
    """Whatever writes it, `gateway/actions_reader.py` must parse the line
    into the same `UserAction` -- proven here by writing the *same*
    action through both the stage's own `append_action` and the
    contract's `POST /actions`, into the same spool, and checking both
    lines parse to equal (intent_id, kind, person, payload)."""
    monkeypatch.setattr(stage_module, "SPOOL_DIR", spool_dir)
    monkeypatch.setattr(stage_module, "stage_file", lambda: session_dir / "buehne.json")

    stage_module.append_action("0123456789abcdef", "queue:room", "tap", {"op": "request"})

    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json=QUEUE_TAP)
    pid = response.cookies["cothinker_pid"]

    path = spool_dir / session_dir.name / ACTIONS_FILE
    got = []
    reader = ActionsReader(path, lambda a: got.append(a) or 1, lambda m: None)
    assert reader.poll() == 2
    assert (got[0].intent_id, got[0].kind, got[0].payload) == ("queue:room", "tap", {"op": "request"})
    assert got[0].person == "0123456789abcdef"
    assert (got[1].intent_id, got[1].kind, got[1].payload) == ("queue:room", "tap", {"op": "request"})
    assert got[1].person == pid


# ---- L3: through the real block host (production registry) ---------------


def test_a_queue_tap_through_the_contract_reaches_the_real_speaker_queue_block(session_dir, spool_dir, tmp_path):
    app = build_live_app(session_dir, spool_dir=spool_dir)
    with TestClient(app) as client:
        response = client.post("/actions", json=QUEUE_TAP)
    assert response.status_code == 202
    pid = response.cookies["cothinker_pid"]

    sink = []
    logs = []
    blocks = build_session_blocks(
        session_dir=tmp_path / "blocks-state", sink=sink.append, log=logs.append)
    assert load_block("speaker-queue").id == "speaker-queue", "the production registry must register speaker-queue"

    path = spool_dir / session_dir.name / ACTIONS_FILE
    reader = ActionsReader(path, dispatcher_for(blocks), logs.append)
    assert reader.poll() == 1

    queue_views = [i for i in sink if i.kind == "queue"]
    assert queue_views, f"speaker-queue never emitted a view intent; logs: {logs}"
    assert queue_views[-1].payload["speaking"]["pid"] == pid
