# CoThinker — tests for gateway/stage_surface.py (issue #46, Wave C.1)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_stage_surface.py"""
from __future__ import annotations

import json

from gateway.stage_state import StageState

from gateway.stage_surface import StageSurface
from shared.surface import IntentBoard, ViewIntent


class FakeWriter:
    def __init__(self):
        self.statuses = []
        self.writes = 0

    def set_status(self, state, *, at="", count=0, failed=None):
        self.statuses.append((state, at))

    def write(self):
        self.writes += 1
        return True


def intent(intent_id, kind, payload, block="b"):
    return ViewIntent(id=intent_id, block=block, kind=kind, audience="room",
                      payload=payload)


def make(**kw):
    state = StageState(**kw)
    writer = FakeWriter()
    logs = []
    return StageSurface(writer, state, logs.append), state, writer, logs


def fingerprint(state):
    return json.dumps({k: v for k, v in state.content().items() if k != "updated"},
                      ensure_ascii=False, sort_keys=True)


def test_topic_rule_confirmed_and_provisional():
    surface, state, writer, _ = make()
    surface.render([intent("topic:t1", "status",
                           {"title": "Budget", "since": 1.0, "provisional": True})])
    assert state.headline == "Budget"
    surface.render([intent("topic:t2", "status",
                           {"title": "Roadmap", "since": 2.0, "provisional": False})])
    assert state.main.title == "Roadmap"
    assert writer.writes == 2


def test_pending_rule_announces_then_settles():
    surface, state, _, _ = make()
    announce = intent("pending:1", "status",
                      {"pending_kind": "decision", "label": "Beschluss", "settled": False})
    surface.render([announce])
    assert [p.label for p in state.pending] == ["Beschluss"]
    surface.render([announce])                       # unchanged: not announced twice
    assert len(state.pending) == 1
    surface.render([intent("pending:1", "status",
                           {"pending_kind": "decision", "label": "Beschluss",
                            "settled": True})])
    assert state.pending == []


def test_status_rule_sets_operating_line():
    surface, _, writer, _ = make()
    assert surface.name == "stage" and list(surface.actions()) == []
    assert surface.kinds == {"prompt", "status", "artefact", "graph", "timer",
                             "queue", "share"}
    surface.render([intent("status:line", "status",
                           {"state": "working", "detail": ""})])
    assert writer.statuses == [("working", "")]


def test_links_rule_sets_links():
    surface, state, _, _ = make()
    surface.render([intent("links:t1", "artefact", {"links": ["notes/a.md"]})])
    assert state.content()["links"] == [{"file": "notes/a.md", "label": "notes/a.md"}]


def test_mirror_rule_sets_mirror():
    surface, state, _, _ = make()
    surface.render([intent("mirror:1", "prompt",
                           {"text": "Wer fehlt noch?", "kind": "coach"})])
    assert state.content()["mirror"] == "Wer fehlt noch?"


def test_condense_rule_is_log_only():
    surface, state, writer, logs = make()
    before = fingerprint(state)
    surface.render([intent("condense:t1", "artefact", {"text": "Kurz", "quotes": []})])
    assert fingerprint(state) == before
    assert writer.writes == 0
    assert any("condense:t1" in line for line in logs)


def test_queue_rule_sets_queue_key_and_clears_it():
    surface, state, _, _ = make()
    queue = {"mode": "tap", "speaking": {"pid": "a", "name": "Ann"},
             "waiting": [{"pid": "b", "name": "Bo"}], "round": None}
    surface.render([intent("queue:room", "queue", queue)])
    assert state.content()["queue"] == queue
    surface.render([])
    assert "queue" not in state.content()


def test_share_rule_sets_share_key():
    surface, state, _, _ = make()
    share = {"shares": [{"person": "Ann", "share": 1.0}], "total_words": 10}
    surface.render([intent("share:room", "share", share)])
    assert state.content()["share"] == share


def test_graph_rule_sets_session_graph_key():
    surface, state, _, _ = make()
    graph = {"nodes": [{"id": "n1"}], "edges": []}
    surface.render([intent("graph:session", "graph", graph)])
    assert state.content()["session_graph"] == graph


def test_timer_rule_sets_agenda_keeping_people():
    surface, state, _, _ = make()
    state.agenda = {"current": 1, "people": ["Ann", "Bo"], "speaker": "Ann",
                    "next_speaker": "Bo"}
    timer = {"current": 2, "current_text": "B", "next": None, "next_text": None,
             "total": 2, "item_started_at": 100.0, "item_budget_s": 300,
             "meeting_deadline": 900.0, "anchor_at": 50.0,
             "planned_cumulative_min": [5.0, 10.0]}
    surface.render([intent("timer:current", "timer", timer)])
    agenda = state.content()["agenda"]
    assert agenda["anchor_at"] == 50.0
    assert agenda["planned_cumulative_min"] == [5.0, 10.0]
    assert agenda["current"] == 2 and agenda["people"] == ["Ann", "Bo"]
    assert agenda["speaker"] == "Ann"


def test_fingerprint_ignores_clock():
    ticks = [0.0]
    surface, state, _, _ = make(now=lambda: ticks[0])
    board = IntentBoard(clock=lambda: ticks[0])
    for i in (
        intent("queue:room", "queue", {"mode": "tap", "speaking": None,
                                       "waiting": [], "round": None}),
        intent("share:room", "share", {"shares": [], "total_words": 0}),
        intent("timer:current", "timer", {"current": 1, "current_text": "A",
                                          "next": None, "next_text": None,
                                          "total": 1, "item_started_at": 1.0,
                                          "item_budget_s": 60,
                                          "meeting_deadline": 900.0,
                                          "anchor_at": 0.0,
                                          "planned_cumulative_min": [1.0]}),
        intent("status:line", "status", {"state": "listening", "detail": ""}),
    ):
        board.put(i)
    surface.render(board.current())
    first = fingerprint(state)
    ticks[0] = 4000.0
    surface.render(board.current())
    assert fingerprint(state) == first


def test_unknown_kind_dropped_and_logged_once():
    surface, state, writer, logs = make()
    before = fingerprint(state)
    odd = [intent("choice:1", "choice_list", {"options": []}),
           intent("choice:2", "choice_list", {"options": []})]
    surface.render(odd)
    surface.render(odd)
    assert fingerprint(state) == before and writer.writes == 0
    assert len([line for line in logs if "choice_list" in line]) == 1
