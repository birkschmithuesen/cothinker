# CoThinker — tests for gateway/block_host_wiring.py: the session host's
# block host (issue #38, Wave B.1)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_host_wiring.py"""
from __future__ import annotations

import pytest

from gateway import block_host_wiring as bhw
from shared.blocks import BLOCK_API_VERSION
from shared.modules import CoreEvent
from shared.surface import ViewIntent


class FakeBlock:
    """Records every event it sees and the `ctx` it started with."""
    api_version = BLOCK_API_VERSION

    def __init__(self, id_):
        self.id = id_
        self.seen = []
        self.ctx = None
        self.stopped = False
        self.emit_on = None       # kind that triggers ctx.emit()
        self.output_on = None     # kind that triggers a block_output

    def start(self, ctx, config):
        self.ctx = ctx

    def on_event(self, ev):
        self.seen.append(ev)
        out = []
        if self.emit_on is not None and ev.kind == self.emit_on:
            self.ctx.emit(ViewIntent(id=f"{self.id}:1", block=self.id, kind="status",
                                     audience="room", payload={"text": "hi"}))
        if self.output_on is not None and ev.kind == self.output_on:
            out.append(CoreEvent("block_output", {"from": self.id}))
        return out

    def stop(self):
        self.stopped = True


def make_blocks(tmp_path, *, registry=None, sink=None, log=None):
    logs = []
    return bhw.build_session_blocks(
        session_dir=tmp_path,
        sink=sink or (lambda intent: None),
        log=log or logs.append,
        registry=registry if registry is not None else {},
        clock=lambda: 0.0,
    ), logs


def test_registered_block_receives_utterance(tmp_path):
    fake = FakeBlock("capture-bullets")
    blocks, _ = make_blocks(tmp_path, registry={"capture-bullets": lambda: fake})
    blocks.dispatch("utterance", {"text": "hallo"})
    assert any(ev.kind == "utterance" for ev in fake.seen)


def test_unknown_template_block_is_skipped_and_logged_once(tmp_path):
    blocks, logs = make_blocks(tmp_path, registry={})
    assert blocks.get("capture-bullets") is None
    missing = [line for line in logs
               if "no block registered for 'capture-bullets'" in line]
    assert len(missing) == 1


def test_dispatch_never_raises_on_unknown_kind(tmp_path):
    blocks, _ = make_blocks(tmp_path)
    assert blocks.dispatch("not-a-kind", {}) == 0


def test_emit_reaches_intents_and_sink(tmp_path):
    fake = FakeBlock("capture-bullets")
    fake.emit_on = "utterance"
    sunk = []
    blocks, _ = make_blocks(tmp_path, registry={"capture-bullets": lambda: fake},
                            sink=sunk.append)
    blocks.dispatch("utterance", {"text": "hallo"})
    assert [i.id for i in blocks.intents.current()] == ["capture-bullets:1"]
    assert [i.id for i in sunk] == ["capture-bullets:1"]


def test_artefact_rejects_path_names(tmp_path):
    fake = FakeBlock("capture-bullets")
    blocks, _ = make_blocks(tmp_path, registry={"capture-bullets": lambda: fake})
    with pytest.raises(ValueError):
        fake.ctx.artefact("../evil.md", "x")
    with pytest.raises(ValueError):
        fake.ctx.artefact("sub/dir.md", "x")
    fake.ctx.artefact("bullets.md", "- one\n")
    assert (tmp_path / "artefacts" / "capture-bullets" / "bullets.md").read_text() == "- one\n"


def test_stop_reaches_blocks(tmp_path):
    fake = FakeBlock("capture-bullets")
    blocks, _ = make_blocks(tmp_path, registry={"capture-bullets": lambda: fake})
    blocks.stop()
    assert fake.stopped


def test_reflect_template_loads_with_empty_registry(tmp_path):
    blocks, logs = make_blocks(tmp_path, registry={})
    assert blocks.dispatch("tick", {"at": 0.0, "components": {}}) == 1  # the listener only
    assert len(logs) == 12  # every Reflect block id is unregistered today


def test_on_output_sees_block_output(tmp_path):
    fake = FakeBlock("capture-bullets")
    fake.output_on = "utterance"
    blocks, _ = make_blocks(tmp_path, registry={"capture-bullets": lambda: fake})
    seen = []
    blocks.on_output(seen.append)
    blocks.dispatch("utterance", {"text": "hallo"})
    assert seen == [{"from": "capture-bullets"}]


def test_infomaniak_llm_adapter_maps_to_chat_and_raises_on_empty():
    from gateway.infomaniak_chat import ChatError, ChatReply

    class FakeClient:
        def __init__(self, text):
            self.text, self.seen = text, None

        def chat(self, model, system, user, schema, **kw):
            self.seen = (model, system, user, schema, kw)
            return ChatReply(text=self.text)

    ok = FakeClient('{"topics": []}')
    llm = bhw.InfomaniakLlm(ok, model="m")
    assert llm.complete("sys", "usr", timeout_s=7) == '{"topics": []}'
    assert ok.seen[:3] == ("m", "sys", "usr")
    assert ok.seen[4]["request_timeout_s"] == 7
    with pytest.raises(ChatError):
        bhw.InfomaniakLlm(FakeClient("  "), model="m").complete("s", "u")


def test_default_llm_for_only_condense_with_key(monkeypatch):
    monkeypatch.setenv("INFOMANIAK_API_KEY", "k")
    assert isinstance(bhw.default_llm_for("condense"), bhw.InfomaniakLlm)
    assert bhw.default_llm_for("capture-bullets") is None
    monkeypatch.delenv("INFOMANIAK_API_KEY")
    assert bhw.default_llm_for("condense") is None


def test_collect_returns_only_the_named_blocks_answers(tmp_path):
    from blocks.minutes_document import MinutesDocumentBlock
    blocks = bhw.build_session_blocks(
        session_dir=tmp_path, sink=lambda i: None, log=lambda *a: None,
        registry={"minutes-document": MinutesDocumentBlock})
    got = blocks.collect("block_output", {"minutes_query": {}}, "minutes-document")
    assert [o["kind"] for o in got] == ["minutes_state"]
    assert blocks.collect("block_output", {"minutes_query": {}}, "other") == []
    # nothing leaks into a plain dispatch afterwards
    assert blocks.collect("tick", {"at": 0.0, "components": {}}, "minutes-document") == []


def test_capture_bullets_gets_the_worker_failure_markers(tmp_path):
    from gateway.worker import FAILURE_MARKERS
    blocks = bhw.build_session_blocks(
        session_dir=tmp_path, sink=lambda i: None, log=lambda *a: None)
    got = blocks.collect(
        "agent_reply",
        {"text": FAILURE_MARKERS[0] + ' ```json\n{"verlauf": ["x"]}\n```', "turn": 1},
        "capture-bullets")
    assert [(o["kind"], o["failed"]) for o in got] == [("parse_failed", True)]


def test_registry_has_timer_queue_and_share_blocks():
    assert {"timer", "speaker-queue", "speaking-share"} <= set(bhw.REGISTRY)


def test_round_actions_reach_speaker_queue_through_the_host(tmp_path):
    emitted = []
    blocks = bhw.build_session_blocks(session_dir=tmp_path, sink=emitted.append,
                                      log=lambda m: None)
    blocks.dispatch("action", {"intent_id": "queue:room", "kind": "voice",
                               "person": None, "at": 1.0,
                               "payload": {"op": "round_start", "people": ["A"]}})
    blocks.dispatch("action", {"intent_id": "queue:room", "kind": "voice",
                               "person": None, "at": 2.0,
                               "payload": {"op": "next_person"}})
    assert any(i.id == "queue:room" and i.payload["mode"] == "round" for i in emitted)


def test_block_configs_override_the_template_config(tmp_path):
    emitted = []
    bhw.build_session_blocks(
        session_dir=tmp_path, sink=emitted.append, log=lambda m: None,
        block_configs={"timer": {"items": [{"label": "A", "minutes": 5}],
                                 "deadline": 99.0}})
    timer = [i for i in emitted if i.id == "timer:current"]
    assert timer and timer[0].payload["current_text"] == "A"
    assert timer[0].payload["meeting_deadline"] == 99.0
