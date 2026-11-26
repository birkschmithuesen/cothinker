#!/usr/bin/env python3
# CoThinker - Reflect rounds and time: integration of timer, speaker-queue and speaking-share
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""An invented session -- three speakers, two agenda items, two taps --
run through the real session block host (`gateway/block_host_wiring.py`),
the real `AgendaController` and `StageState`, the way `gateway/consumer.py`
wires them. Times are explicit (`at`), state lives under tmp_path.

python3 -m pytest -q tests/test_reflect_rounds_time.py
"""
from __future__ import annotations

import pytest

from gateway import consumer as k
from gateway import stage_state as ss
from gateway.block_host_wiring import build_session_blocks, feed_utterance

PEOPLE = ["Anna", "Ben", "Carla"]
AGENDA = "- [ ] Ankommen\n- [ ] Budget (10 min)\n"
T0 = 1000.0


class _Protocol:
    """Stands in for the transcript protocol: only the raw append."""

    def __init__(self) -> None:
        self.lines: list = []

    def append_raw(self, text, speaker) -> None:
        self.lines.append((speaker, text))


class _Session:
    """The consumer's wiring: controller, block host, stage state."""

    def __init__(self, tmp_path, people) -> None:
        session = tmp_path / "session"
        session.mkdir()
        (session / "agenda.md").write_text(AGENDA, encoding="utf-8")
        self.now = T0
        self.state = ss.StageState()
        self.ctrl = k.AgendaController(
            session / "agenda.md", session, people=list(people),
            now=lambda: self.now)
        self.log: list[str] = []
        self.blocks = build_session_blocks(
            session_dir=session, sink=self._sink, log=self.log.append,
            clock=lambda: self.now, block_configs=self.ctrl.block_configs())
        self.wrapped: list = []
        self.blocks.on_output(
            lambda p: self.wrapped.append(p) if "round_wrapped" in p else None)
        self.ctrl.attach(self.blocks)

    def _sink(self, intent) -> None:
        # Same shape as consumer.block_sink.
        if self.ctrl.observe(intent):
            self.state.agenda = self.ctrl.payload()

    def action(self, intent_id, kind, payload, at, person=None) -> None:
        self.now = at       # blocks read their clock; `at` rides on the action
        self.blocks.dispatch("action", {
            "intent_id": intent_id, "kind": kind, "person": person,
            "payload": payload, "at": at})

    def stage_agenda(self) -> dict:
        return self.state.content()["agenda"]


@pytest.fixture
def tap_session(tmp_path):
    s = _Session(tmp_path, people=[])
    for pid, name in (("p1", "Anna"), ("p2", "Ben"), ("p3", "Carla")):
        s.action("participant:name", "edit", {"name": name}, T0, person=pid)
    s.action("queue:room", "tap", {"op": "request"}, T0 + 1, person="p3")
    s.action("queue:room", "tap", {"op": "request"}, T0 + 2, person="p1")
    return s


@pytest.fixture
def round_session(tmp_path):
    return _Session(tmp_path, people=PEOPLE)


def test_queue_order_on_stage(tap_session):
    view = tap_session.blocks.intents.current()
    queue = [i for i in view if i.id == "queue:room"]
    assert len(queue) == 1
    payload = queue[0].payload
    assert payload["mode"] == "tap"
    order = [payload["speaking"]["name"]] + [w["name"] for w in payload["waiting"]]
    assert order == ["Carla", "Anna"]
    # the controller takes the queue view in as an agenda intent (the tap
    # queue itself only shows on the IntentBoard, not on the stage)
    assert tap_session.ctrl.observe(queue[0]) is True


def test_shares_sum_to_one(tap_session):
    proto = _Protocol()
    for i, (speaker, text) in enumerate([
            ("Anna", "wir sollten zuerst das Budget klaeren"),
            ("Ben", "ich sehe das etwas anders als Anna"),
            ("Carla", "mir ist wichtig dass alle mitreden koennen hier"),
            ("Anna", "gut dann machen wir eine kurze Runde")]):
        feed_utterance(proto, tap_session.blocks, text, speaker,
                       {"at": T0 + i})
    assert len(proto.lines) == 4
    tap_session.blocks.dispatch("tick", {"now": T0 + 100})
    share = [i for i in tap_session.blocks.intents.current()
             if i.id == "share:room"]
    assert len(share) == 1
    shares = share[0].payload["shares"]
    assert sorted(r["person"] for r in shares) == sorted(PEOPLE)
    assert sum(r["share"] for r in shares) == pytest.approx(1.0)


def test_voice_next_item_moves_timer(round_session):
    s = round_session
    assert s.stage_agenda()["current"] == 1
    s.action("timer:current", "voice", {"op": "next"}, T0 + 60)
    agenda = s.stage_agenda()
    assert agenda["current"] == 2
    assert agenda["current_text"] == "Budget"
    assert agenda["item_started_at"] == T0 + 60
    assert agenda["item_budget_s"] == 600
    assert agenda["next"] is None


def test_round_wrap_moves_timer(round_session):
    s = round_session
    assert s.stage_agenda()["speaker"] == "Anna"
    s.action("queue:room", "voice", {"op": "next_person"}, T0 + 10)
    s.action("queue:room", "voice", {"op": "next_person"}, T0 + 20)
    agenda = s.stage_agenda()
    assert agenda["speaker"] == "Carla" and agenda["current"] == 1
    assert s.wrapped == []
    s.action("queue:room", "voice", {"op": "next_person"}, T0 + 30)
    assert s.wrapped == [{"from": "speaker-queue",
                          "round_wrapped": {"at": T0 + 30}}]
    agenda = s.stage_agenda()
    assert agenda["current"] == 2
    assert agenda["speaker"] == "Anna"
    assert agenda["item_started_at"] == T0 + 30
