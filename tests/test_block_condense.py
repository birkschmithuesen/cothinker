# CoThinker — tests for the condense block (LLM faked, no network)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_condense.py"""
from __future__ import annotations

import json

from blocks.condense import CondenseBlock, condense, load_prompt
from shared.blocks import BlockContext
from shared.language_guard import detect_language
from shared.llm import LlmPort
from shared.modules import CoreEvent, StateStore


class FakeLlm:
    """Answers from a list of replies (strings or exceptions), records calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user, *, timeout_s=60):
        self.calls.append((system, json.loads(user)))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def reply(*items):
    return json.dumps({"topics": [{"topic_id": i, "text": t, "quotes": q} for i, t, q in items]})


SEGMENTS = [
    {"id": 1, "text": "Wir müssen das Budget für das nächste Jahr festlegen und der Plan ist noch offen."},
    {"id": 2, "text": "As Anna said, we need more budget for the new project."},
    {"id": 3, "text": "Die Einladung an die Kunden wird nächste Woche verschickt."},
]
DE_TEXT = "Das Team spricht über das Budget und der Plan für das nächste Jahr ist noch offen."


def test_german_speech_english_quotes_gives_german_text():
    llm = FakeLlm(reply(("t1", DE_TEXT, ["we need more budget for the new project"])))
    res = condense(SEGMENTS, [{"id": "t1", "title": "Budget"}], "de", llm)
    assert isinstance(llm, LlmPort)
    assert detect_language(res.text) == "de"
    assert res.quotes == ["we need more budget for the new project"]
    assert res.topics_covered == ["t1"] and res.not_condensed == []


def test_quotes_are_verbatim():
    quotes = ["das Budget für das nächste Jahr festlegen", "Die Einladung an die Kunden"]
    llm = FakeLlm(reply(("t1", DE_TEXT, quotes)))
    res = condense(SEGMENTS, [{"id": "t1", "title": "Budget"}], "de", llm)
    assert res.quotes == quotes
    assert all(any(q in s["text"] for s in SEGMENTS) for q in res.quotes)


def test_hallucinated_quote_dropped():
    logged = []
    llm = FakeLlm(reply(("t1", DE_TEXT, ["we need more budget", "we need a lot more budget"])))
    res = condense(SEGMENTS, [{"id": "t1", "title": "Budget"}], "de", llm, log=logged.append)
    assert res.quotes == ["we need more budget"]
    assert any("we need a lot more budget" in m for m in logged)


def test_five_topics_five_covered():
    topics = [{"id": f"t{i}", "title": f"Thema {i}"} for i in range(5)]
    llm = FakeLlm(reply(*[(t["id"], DE_TEXT, []) for t in topics]))
    res = condense(SEGMENTS, topics, "de", llm)
    assert res.topics_covered == [t["id"] for t in topics]
    assert res.not_condensed == []
    assert len(llm.calls) == 1


def test_missing_topic_retried_then_listed():
    topics = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}, {"id": "c", "title": "C"}]
    llm = FakeLlm(reply(("a", DE_TEXT, []), ("b", DE_TEXT, [])),
                  reply(("a", DE_TEXT, [])))          # retry still lacks c
    res = condense(SEGMENTS, topics, "de", llm)
    assert len(llm.calls) == 2                        # exactly one retry
    assert [t["topic_id"] for t in llm.calls[1][1]["topics"]] == ["c"]
    assert res.topics_covered == ["a", "b"]
    assert res.not_condensed == ["c"]


def test_llm_failure_emits_nothing(tmp_path):
    emitted = []
    ctx = BlockContext(instance=None, session_dir=tmp_path, state=StateStore(tmp_path / "s"),
                       agent=None, llm=FakeLlm(RuntimeError("down")), language="de",
                       clock=lambda: 0.0, emit=emitted.append, artefact=lambda n, t: None)
    block = CondenseBlock()
    block.start(ctx, {"prompt": "prompts/condense.{lang}.md"})
    assert block.on_event(CoreEvent("block_output", {"topic": {"id": "t1", "title": "Budget"}})) == ()
    assert block.on_event(CoreEvent("segment", {"id": 1, "text": SEGMENTS[0]["text"]})) == ()
    out = block.on_event(CoreEvent("block_output", {"topic": {"id": "t1", "closed": True}}))
    assert out == () and emitted == []
    assert "JSON" in load_prompt("en")
