# CoThinker — level-2 orders reach the stage under --lanes (issue #178)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Level 2 (`--policy`) announces a pending for every decision and task it
recognises and queues a finished order for the language agent. Under
`--lanes` no turn ever took those orders, so every pending ended
`discarded` after `PENDING_TIMEOUT_S` and `buehne.json` kept `decisions`
empty and never got `tasks` (issue #178; ADR 0026).

Drives the REAL `gateway/consumer.py` (tests/consumer_harness.py) with the
two-speaker fixture. Faked is only the outside world (ADR 0022): the STT
stream, the decider model (`COTHINKER_DECIDER=script`, deterministic) and
the language model (tests/fake_worker.py answering the orders its prompt
carries).

    python3 -m pytest -q tests/test_level2_orders_under_lanes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consumer_harness import run_consumer  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "reflect_two_speakers.jsonl"
PENDING_TIMEOUT_S = 60.0          # gateway/stage_state.py, never raised (#178)

# The decider: the fixture's decision and task sentences cross level 2's
# `sitzung` thresholds (0.7); every other question answers 0.0.
DECIDER_RULES = {"default": 0.0, "rules": [
    {"match": "Werkstatt am Freitag", "answers": {"beschluss": 0.95}},
    {"match": "Material bestellen", "answers": {"aufgabe": 0.95}},
]}
# The language model: how it formulates each finished order.
ORDER_ACTIONS = {
    "beschlossen": {"action": "decision", "text": "Werkstatt am Freitag."},
    "Tag `aufgabe`": {"action": "tag_entry", "tag": "aufgabe",
                      "text": "Sam bestellt bis Donnerstag Material.",
                      "person": "Sam", "due": "Donnerstag"},
}


def _finals() -> list[str]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["text"] for line in lines if line.strip()]


def _stage(session_dir: Path) -> dict:
    try:
        return json.loads((session_dir / "buehne.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _filled(session_dir: Path) -> bool:
    stage = _stage(session_dir)
    return bool(stage.get("decisions")) and bool(stage.get("tasks"))


def _events(session_dir: Path) -> list[dict]:
    path = session_dir / "stage-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _settled(session_dir: Path) -> bool:
    return len([e for e in _events(session_dir)
                if e.get("event") == "pending_end"
                and e.get("kind") in ("decision", "entry")]) == 2


def _run_lanes(tmp_path: Path, actions=ORDER_ACTIONS, until=_filled, verlauf=()):
    script = tmp_path / "decider-script.json"
    script.write_text(json.dumps(DECIDER_RULES), encoding="utf-8")
    return run_consumer(
        tmp_path, finals=_finals(), extra_args=("--policy", "--lanes"),
        timeout_s=45, until=until,
        env_extra={"COTHINKER_DECIDER": "script",
                   "COTHINKER_DECIDER_SCRIPT": str(script),
                   "FAKE_WORKER_ORDER_ACTIONS": json.dumps(actions),
                   "FAKE_WORKER_ORDER_VERLAUF": json.dumps(list(verlauf))})


def test_the_fixture_fills_decisions_and_tasks_under_policy_lanes(tmp_path):
    run = _run_lanes(tmp_path)

    assert run.returncode == 0, run.log[-2000:]
    assert "policy on: mode sitzung" in run.log
    stage = run.stage_json or {}
    assert [d["text"] for d in stage.get("decisions", [])] == [
        "Werkstatt am Freitag."], run.log[-2000:]
    assert stage.get("tasks") == [{"text": "Sam bestellt bis Donnerstag Material.",
                                   "person": "Sam", "due": "Donnerstag"}], run.log[-2000:]


def test_the_pendings_settle_arrived_within_the_timeout(tmp_path):
    """`arrived` comes only from the orders' own turn, and the trace's
    `duration_s` says how long each pending stood (#178: fix the cause,
    never raise PENDING_TIMEOUT_S)."""
    run = _run_lanes(tmp_path)

    events = _events(run.session_dir)
    acted = [e for e in events if e.get("event") == "policy_action"]
    assert sorted(e.get("kind") for e in acted) == ["stage_decision", "tag_entry"]
    ends = [e for e in events if e.get("event") == "pending_end"
            and e.get("kind") in ("decision", "entry")]
    assert {e["kind"]: e.get("outcome") for e in ends} == {
        "decision": "arrived", "entry": "arrived"}, ends
    assert all(e["duration_s"] < PENDING_TIMEOUT_S for e in ends), ends


def test_an_orders_turn_applies_only_answers_and_settles_the_rest(tmp_path):
    """The agent answers the decision order with a `topic` and the task
    order with nothing: under --lanes the topic has its own owner, so it
    is not applied, and turn_closed settles both orders `unanswered`."""
    run = _run_lanes(tmp_path, actions={"beschlossen": {
        "action": "topic", "title": "Werkstatt", "markdown": "- Freitag"}},
        until=_settled)

    stage = run.stage_json or {}
    assert stage.get("decisions") == [] and "tasks" not in stage
    assert "Werkstatt" not in json.dumps(stage.get("history", []))
    ends = {e["kind"]: e.get("outcome") for e in _events(run.session_dir)
            if e.get("event") == "pending_end"
            and e.get("kind") in ("decision", "entry")}
    assert ends == {"decision": "unanswered", "entry": "unanswered"}, ends


VERLAUF_ITEM = "Die Runde plant die Werkstatt im Detail."


def test_an_orders_turn_files_no_minutes_items(tmp_path):
    """Under --lanes no agent writes the Verlauf (ADR 0026): minutes items
    an orders turn's reply carries besides its answers are not filed."""
    run = _run_lanes(tmp_path, verlauf=[VERLAUF_ITEM])

    assert (run.stage_json or {}).get("decisions"), run.log[-2000:]
    assert "orders turn: 1 items outside the orders not filed" in run.log
    assert VERLAUF_ITEM not in run.protocol_text
