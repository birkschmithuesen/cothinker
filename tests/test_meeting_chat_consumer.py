# CoThinker — the meeting chat on the consumer's real start path (issue #208)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The real `gateway/consumer.py` process (`tests/consumer_harness.py`, fake
STT with no speech at all, fake worker) reads one chat message from the
action spool and answers it as its flags say:

(a) no `--lanes`: `unavailable`, the message recorded;
(b) `--lanes` with the fake worker as call-lane worker: `answered`, the
    agent's answer recorded;
(c) `--lanes --no-agent`: `unavailable` (the call lane is built regardless
    of `--no-agent`, consumer.py `if a.lanes:` -> `build_lanes`).

The spool line is seeded BEFORE the consumer starts: the session folder is
`<date.today()>-<slugify(--title)>` (consumer.py `day =
date.today().isoformat()` and `session_paths`), so its name is known in
advance; it is seeded for today and for tomorrow, so a run that crosses
midnight still finds it (the other folder stays unread).

    python3 -m pytest -q tests/test_meeting_chat_consumer.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from gateway.consumer import slugify
from shared.actions_spool import append_action
from tests.consumer_harness import FAKE_WORKER, run_consumer

TITLE = "Smoke Test"            # the harness's --title
PID = "a1a1a1a1a1a1a1a1"
MESSAGE = "Compare X and Y as an interactive page on the stage."
ANSWER = "X is faster, Y is cheaper."


def _seed_spool(spool: Path) -> None:
    for day in (date.today(), date.today() + timedelta(days=1)):
        folder = f"{day.isoformat()}-{slugify(TITLE)}"
        append_action(spool, folder, PID, "participant:name", "edit", {"name": "Person A"})
        append_action(spool, folder, PID, "chat:room", "message", {"text": MESSAGE})


def _chat(session_dir: Path) -> list[dict]:
    try:
        stage = json.loads((session_dir / "buehne.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return (stage.get("chat") or {}).get("messages") or []


def _settled(session_dir: Path) -> bool:
    return any(m["role"] == "participant" and m["status"] != "pending" for m in _chat(session_dir))


def _run(tmp_path: Path, *extra: str, reply: str = ""):
    spool = tmp_path / "spool"
    _seed_spool(spool)
    run = run_consumer(tmp_path, finals=[], timeout_s=60,
                       extra_args=("--spool-dir", str(spool), *extra),
                       env_extra={"FAKE_WORKER_REPLY": reply} if reply else None,
                       until=_settled)
    assert not run.timed_out, run.log[-2000:]
    raw = run.protocol_text.split("## Rohtranskript", 1)[1]
    return _chat(run.session_dir), raw


def test_without_lanes_the_real_consumer_answers_unavailable_and_records(tmp_path):
    chat, raw = _run(tmp_path)
    assert [(m["name"], m["status"]) for m in chat] == [("Person A", "unavailable")]
    assert f"*Person A (Chat):* {MESSAGE}" in raw


def test_with_lanes_the_real_consumer_answers_through_the_call_lane(tmp_path):
    chat, raw = _run(tmp_path, "--lanes", "--lane-call-worker-command",
                     f"{sys.executable} {FAKE_WORKER}", reply=ANSWER)
    assert [(m["role"], m["status"]) for m in chat] == [("participant", "answered"), ("agent", "answered")]
    assert chat[1]["text"] == ANSWER
    assert f"*Person A (Chat):* {MESSAGE}" in raw
    assert f"*Robo (Chat):* {ANSWER}" in raw


def test_with_lanes_and_no_agent_the_real_consumer_answers_unavailable(tmp_path):
    chat, raw = _run(tmp_path, "--lanes", "--no-agent", "--lane-call-worker-command",
                     f"{sys.executable} {FAKE_WORKER}", reply=ANSWER)
    assert [(m["role"], m["status"]) for m in chat] == [("participant", "unavailable")]
    assert "Robo (Chat)" not in raw
