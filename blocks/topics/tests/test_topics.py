# CoThinker — tests for blocks/topics (issue #39, Wave B.2): the topics
# block driven by the block host.
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q blocks/topics/tests/test_topics.py"""
from __future__ import annotations

from pathlib import Path

from blocks.topics import TopicsBlock
from shared.blocks import BlockContext, BlockHost
from shared.modules import CoreEvent, StateStore


class FakeTitler:
    """Records requests; the test plays the model by calling the callbacks."""
    def __init__(self, on_title, on_fail):
        self.on_title = on_title
        self.on_fail = on_fail
        self.requests = []
        self.stopped = False

    def request(self, entry_id, provisional, block, previous=""):
        self.requests.append((entry_id, provisional, block, previous))
        return True

    def stop(self):
        self.stopped = True


class Rig:
    def __init__(self, tmp_path: Path, config=None):
        self.now = 1000.0
        self.intents = []
        self.titlers = []
        self.outputs = []

        def factory(on_title, on_fail):
            t = FakeTitler(on_title, on_fail)
            self.titlers.append(t)
            return t

        self.block = TopicsBlock(titler_factory=factory)

        class Listener:
            id = "listener"
            api_version = 1

            def start(inner, ctx, cfg):
                pass

            def on_event(inner, ev):
                if ev.kind == "block_output" and "topic" in ev.payload:
                    self.outputs.append(ev.payload["topic"])
                return ()

            def stop(inner):
                pass

        def ctx_for(block_id):
            return BlockContext(
                instance=None, session_dir=tmp_path,
                state=StateStore(tmp_path / ".blocks" / block_id),
                agent=None, llm=None, language="de", clock=lambda: self.now,
                emit=self.intents.append, artefact=lambda n, t: None)

        self.host = BlockHost([self.block, Listener()], ctx_for=ctx_for, log=lambda s: None)
        self.host.start({"topics": config or {"titles": True}})

    def decide(self, p_neu, p_ruek=None, block="Wir sprechen jetzt ueber das Budget.\nMehr Text."):
        self.host.dispatch(CoreEvent("block_output", {"decider_result": {
            "neues_thema": p_neu, "rueckkehr_thema": p_ruek,
            "block": block, "previous": "Davor war es um den Plan gegangen."}}))


def test_confirmed_change_emits_topic_and_intent(tmp_path):
    rig = Rig(tmp_path)
    rig.now += 120.0
    rig.decide(0.9)
    assert len(rig.outputs) == 1
    topic = rig.outputs[0]
    assert set(topic) == {"id", "title", "since", "provisional"}
    assert topic["provisional"] is True
    assert topic["title"]
    assert topic["since"] == rig.now
    assert len(rig.intents) == 1
    intent = rig.intents[0]
    assert intent.id == f"topic:{topic['id']}"
    assert intent.kind == "status" and intent.block == "topics"
    assert intent.payload == topic
    assert rig.titlers[0].requests[0][0] == topic["id"]


def test_below_threshold_emits_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.now += 120.0
    rig.decide(0.5, 0.2)
    assert rig.outputs == [] and rig.intents == []
    # Old enough but also not a return: still nothing, and a too-young
    # topic does not confirm even at p=0.95.
    rig.decide(0.95)          # confirms (age 120 s)
    rig.outputs.clear()
    rig.intents.clear()
    rig.now += 10.0
    rig.decide(0.95)          # topic only 10 s old
    assert rig.outputs == [] and rig.intents == []


def test_title_replaces_provisional_in_place(tmp_path):
    rig = Rig(tmp_path)
    rig.now += 120.0
    rig.decide(0.9)
    first = rig.intents[0]
    titler = rig.titlers[0]
    entry_id, provisional, _, _ = titler.requests[0]
    titler.on_title(entry_id, provisional, "Budget und Planung", 120)
    assert len(rig.intents) == 2
    second = rig.intents[1]
    assert second.id == first.id
    assert second.payload["title"] == "Budget und Planung"
    assert second.payload["provisional"] is False
    assert second.payload["id"] == first.payload["id"]
    assert second.payload["since"] == first.payload["since"]


def test_titler_failure_keeps_provisional(tmp_path):
    rig = Rig(tmp_path)
    rig.now += 120.0
    rig.decide(0.9)
    titler = rig.titlers[0]
    entry_id = titler.requests[0][0]
    titler.on_fail(entry_id, "timeout", 4000)
    assert len(rig.intents) == 1
    assert rig.intents[0].payload["provisional"] is True


def test_config_overrides_threshold(tmp_path):
    rig = Rig(tmp_path, {"titles": False, "threshold": 0.95, "min_topic_age_s": 5.0})
    rig.now += 6.0
    rig.decide(0.9)           # below the configured 0.95
    assert rig.outputs == []
    rig.decide(0.96)
    assert len(rig.outputs) == 1
    assert rig.titlers == []  # titles off: no titler built
    assert rig.outputs[0]["provisional"] is False   # nothing will replace it
