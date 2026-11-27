# CoThinker — tests for the --lanes orders turn helpers (issue #178)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Level 1 (ADR 0022) for ADR 0026's pieces in gateway/consumer.py: the
decider result hook that enqueues an orders turn, the filter of an orders
turn's reply, and the writer's hand-off of a filed task to level 2.

    python3 -m pytest -q tests/test_lane_orders.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))

import consumer  # noqa: E402
from stage_state import StageState  # noqa: E402

DECIDED = "Wir machen das so: Werkstatt am Freitag."
TASK = {"action": "tag_entry", "tag": "aufgabe",
        "text": "Sam bestellt bis Donnerstag Material.",
        "person": "Sam", "due": "Donnerstag"}


# --------------------------------------------------- StageWriter hand-off
class _Protocol:
    """The minutes port's `add_entry`: every entry lands."""

    def __init__(self) -> None:
        self.entries: list = []

    def add_entry(self, tag, text, stamp=None, names=None):
        self.entries.append((tag, text))
        return SimpleNamespace(reason="", links_seen=0, links_dead=0)


class _Policy:
    def __init__(self, current=("A1",)) -> None:
        self.current = set(current)
        self.delivered_ids: list = []
        self.filed: list = []

    def marker_for(self, oid) -> str:
        return "*(autonom)*" if oid in self.current else ""

    def delivered(self, oid) -> None:
        self.delivered_ids.append(oid)

    def task_filed(self, oid, action) -> None:
        self.filed.append((oid, action))


class _OldPolicy(_Policy):
    """A policy from before issue #129: no `task_filed` hook."""
    task_filed = None


def _writer(tmp_path: Path, policy):
    w = consumer.StageWriter(tmp_path, StageState(), log=lambda *a: None,
                             proposals=tmp_path / "vorschlag.jsonl")
    w.protocol = _Protocol()
    w.policy = policy
    return w


def _propose(tmp_path: Path, *actions) -> None:
    with open(tmp_path / "vorschlag.jsonl", "a", encoding="utf-8") as f:
        for a in actions:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def test_a_filed_answer_to_an_order_reaches_task_filed(tmp_path):
    policy = _Policy()
    w = _writer(tmp_path, policy)
    _propose(tmp_path, {**TASK, "order": "A1"})
    assert w.ingest(spoken=DECIDED) == 0
    assert policy.filed == [("A1", {**TASK, "order": "A1"})]
    assert policy.delivered_ids == ["A1"]


def test_an_entry_without_a_current_order_is_no_task(tmp_path):
    policy = _Policy()
    w = _writer(tmp_path, policy)
    _propose(tmp_path, TASK, {**TASK, "order": "A7"})
    w.ingest(spoken=DECIDED)
    assert policy.filed == [] and policy.delivered_ids == []


def test_a_policy_without_the_hook_still_gets_its_delivery(tmp_path):
    policy = _OldPolicy()
    w = _writer(tmp_path, policy)
    _propose(tmp_path, {**TASK, "order": "A1"})
    w.ingest(spoken=DECIDED)
    assert policy.delivered_ids == ["A1"]


# ------------------------------------------------------- orders_turn_hook
def test_the_hook_enqueues_one_orders_block_when_orders_wait():
    got = []
    hook = consumer.orders_turn_hook(lambda: True, got.append,
                                     log=lambda *a: None)
    hook(object(), SimpleNamespace(block=DECIDED), "pause")
    [b] = got
    assert b.reason == consumer.ORDERS_REASON == "orders"
    assert b.wake_word is False and b.text == DECIDED


def test_the_hook_costs_nothing_when_no_order_waits():
    got = []
    hook = consumer.orders_turn_hook(lambda: False, got.append,
                                     log=lambda *a: None)
    hook(object(), SimpleNamespace(block=DECIDED), "pause")
    assert got == []


def test_the_hook_never_raises_into_the_decider_thread():
    def broken(_block):
        raise RuntimeError("queue gone")
    said = []
    hook = consumer.orders_turn_hook(lambda: True, broken,
                                     log=lambda *a: said.append(a))
    hook(object(), SimpleNamespace(block=DECIDED), "pause")
    assert said and said[0][0] == "orders turn not enqueued:"


# ---------------------------------------------------------- order_answers
def test_an_orders_turn_keeps_only_the_answers_to_orders():
    answers = [{"action": "decision", "text": "Werkstatt am Freitag.",
                "order": "A1"},
               {**TASK, "order": "A2"}]
    others = [{"action": "topic", "title": "Werkstatt", "order": "A3"},
              {"action": "main", "markdown": "x", "quote": "x", "order": "A4"},
              {"action": "decision", "text": "ohne Auftrag"},
              {"action": "notice", "kind": "risk", "text": "n"},
              {"action": "decision", "text": "kaputte Id", "order": "A 1!"},
              "kein Objekt"]
    assert consumer.order_answers(answers + others) == answers
