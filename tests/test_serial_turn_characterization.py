# CoThinker — characterization of the serial turn and of --lanes alone
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Written BEFORE ADR 0024's change and green on the commit before it:
what the change must leave as it is.

- The serial path (no `--lanes`): every level-2 pending a turn settles is
  settled INSIDE that turn (between `run_started` and `run_finished`), and
  only after the turn's proposals were read (`proposals_read`) -- the
  order turn_opened -> worker -> ingest -> turn_closed, read off the trace.
  The decider runs on its own thread, so WHICH turn takes an order is
  timing; the assertions hold for any split.
- Without `--policy` the serial run carries no order text and no `tasks`.
- `--lanes` without `--policy` runs no turn of the serial worker at all.

    python3 -m pytest -q tests/test_serial_turn_characterization.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consumer_harness import run_consumer  # noqa: E402
from fake_worker import ORDER_LINE  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "reflect_two_speakers.jsonl"
DECIDER_RULES = {"default": 0.0, "rules": [
    {"match": "Werkstatt am Freitag", "answers": {"beschluss": 0.95}},
    {"match": "Material bestellen", "answers": {"aufgabe": 0.95}},
]}
ORDER_ACTIONS = {
    "beschlossen": {"action": "decision", "text": "Werkstatt am Freitag."},
    "Tag `aufgabe`": {"action": "tag_entry", "tag": "aufgabe",
                      "text": "Sam bestellt bis Donnerstag Material."},
}
SETTLED_IN_A_TURN = ("arrived", "unanswered", "failed")


def _finals() -> list[str]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["text"] for line in lines if line.strip()]


def _events(session_dir: Path) -> list[dict]:
    path = session_dir / "stage-events.jsonl"
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _prompts(worker_log: Path) -> list[str]:
    if not worker_log.exists():
        return []
    return [json.loads(line).get("prompt", "") for line in
            worker_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run(tmp_path: Path, *flags: str, decider: bool = True):
    script = tmp_path / "decider-script.json"
    script.write_text(json.dumps(DECIDER_RULES), encoding="utf-8")
    env = {"FAKE_WORKER_ORDER_ACTIONS": json.dumps(ORDER_ACTIONS)}
    if decider:
        env |= {"COTHINKER_DECIDER": "script",
                "COTHINKER_DECIDER_SCRIPT": str(script)}
    return run_consumer(tmp_path, finals=_finals(), extra_args=flags,
                        timeout_s=30, wait_for_worker_turns=0, env_extra=env)


def test_serial_settles_level_2_pendings_inside_the_turn_after_ingest(tmp_path):
    run = _run(tmp_path, "--policy")
    assert run.returncode == 0 and "policy on: mode sitzung" in run.log

    events = _events(run.session_dir)
    assert any(e.get("event") == "run_started" for e in events)
    in_turn, read = False, False
    for e in events:
        name = e.get("event")
        if name == "run_started":
            in_turn, read = True, False
        elif name == "run_finished":
            in_turn = False
        elif name == "proposals_read" and in_turn:
            read = True
        elif (name == "pending_end" and e.get("kind") in ("decision", "entry")
              and e.get("outcome") in SETTLED_IN_A_TURN):
            assert in_turn and read, (e, events)


def test_serial_policy_gets_no_orders_turns(tmp_path):
    """D1: the serial path runs one turn per transcript block, never an
    extra `orders` turn. The install line is checked because it is
    deterministic; an `orders` turn on the serial path only shows when the
    decider thread happens to beat the next block."""
    run = _run(tmp_path, "--policy")
    assert run.returncode == 0 and "policy on: mode sitzung" in run.log
    assert "level-2 orders run on the serial worker" not in run.log
    assert not any(e.get("event") == "run_started" and e.get("kind") == "orders"
                   for e in _events(run.session_dir))


def test_serial_without_policy_carries_no_orders_and_no_tasks(tmp_path):
    run = _run(tmp_path)
    assert run.returncode == 0 and "policy on" not in run.log
    assert not any(ORDER_LINE.search(p) for p in _prompts(run.worker_log))
    assert "tasks" not in (run.stage_json or {})


def test_lanes_without_policy_runs_no_serial_turn(tmp_path):
    run = _run(tmp_path, "--lanes")
    assert run.returncode == 0 and "lanes on" in run.log
    assert "agent (" not in run.log           # run_turn's first log line
    events = _events(run.session_dir)
    assert not any(e.get("event") == "run_started" for e in events)
    stage = run.stage_json or {}
    assert stage.get("decisions") == [] and "tasks" not in stage
