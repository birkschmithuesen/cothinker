# CoThinker — shared/blocks.py: block contract and block host (stdlib only)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_blocks.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared import blocks as sb
from shared.modules import CoreEvent, StateStore


class Echo:
    """Turns every utterance into one block_output."""
    api_version = sb.BLOCK_API_VERSION

    def __init__(self, id_):
        self.id = id_
        self.seen = []
        self.started_with = None

    def start(self, ctx, config):
        self.started_with = dict(config)

    def on_event(self, ev):
        self.seen.append(ev.kind)
        if ev.kind == "utterance":
            return [CoreEvent("block_output", {"from": self.id, "text": ev.payload["text"]})]
        return []

    def stop(self):
        self.seen.append("stopped")


class Crash(Echo):
    def on_event(self, ev):
        raise RuntimeError("boom")


class Loop(Echo):
    def on_event(self, ev):
        return [CoreEvent("block_output", {"again": True})]


def ctx_for(tmp):
    def make(block_id):
        return sb.BlockContext(instance=None, session_dir=Path(tmp),
                               state=StateStore(Path(tmp) / block_id), agent=None, llm=None,
                               language="de", clock=lambda: 0.0,
                               emit=lambda intent: None, artefact=lambda name, text: None)
    return make


def test_start_passes_per_block_config(tmp_path):
    a, b = Echo("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({"a": {"x": 1}})
    assert a.started_with == {"x": 1} and b.started_with == {}


def test_utterance_fans_out_to_both_blocks(tmp_path):
    a, b = Echo("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    n = host.dispatch(CoreEvent("utterance", {"text": "hallo"}))
    assert n == 6


def test_block_output_routed_back_to_every_active_block(tmp_path):
    a, b = Echo("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    host.dispatch(CoreEvent("utterance", {"text": "hallo"}))
    assert a.seen.count("block_output") == 2


def test_inactive_block_not_called(tmp_path):
    a, b = Echo("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    host.set_active(["a"])
    before = len(b.seen)
    host.dispatch(CoreEvent("utterance", {"text": "x"}))
    assert len(b.seen) == before


def test_crashing_block_does_not_stop_others(tmp_path):
    crash = Crash("c")
    other = Echo("d")
    host = sb.BlockHost([crash, other], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    n = host.dispatch(CoreEvent("utterance", {"text": "y"}))
    assert n >= 2


def test_crash_is_logged_with_block_id_and_exception_text(tmp_path):
    crash = Crash("c")
    other = Echo("d")
    logs = []
    host = sb.BlockHost([crash, other], ctx_for=ctx_for(tmp_path), log=logs.append)
    host.start({})
    host.dispatch(CoreEvent("utterance", {"text": "y"}))
    assert any("c" in str(line) and "boom" in str(line) for line in logs)


def test_fanout_depth_is_bounded(tmp_path):
    host = sb.BlockHost([Loop("l")], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    n = host.dispatch(CoreEvent("tick", {}))
    assert n == sb.MAX_FANOUT_DEPTH


def test_unknown_event_kind_raises_value_error(tmp_path):
    a, b = Echo("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    with pytest.raises(ValueError):
        host.dispatch(CoreEvent("not-a-kind", {}))


def test_api_version_mismatch_raises_block_error(tmp_path):
    old = Echo("old")
    old.api_version = 99
    host = sb.BlockHost([old], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    with pytest.raises(sb.BlockError):
        host.start({})


def test_duplicate_block_ids_raise_block_error(tmp_path):
    dup = [Echo("same"), Echo("same")]
    with pytest.raises(sb.BlockError):
        sb.BlockHost(dup, ctx_for=ctx_for(tmp_path), log=lambda line: None)


def test_stop_reaches_every_block(tmp_path):
    a, b = Echo("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    host.stop()
    assert a.seen[-1] == "stopped" and b.seen[-1] == "stopped"


def test_state_store_update_is_read_modify_write(tmp_path):
    store = StateStore(tmp_path / "s")
    store.update("n.json", lambda d: {**d, "n": d.get("n", 0) + 1})
    out = store.update("n.json", lambda d: {**d, "n": d.get("n", 0) + 1})
    assert out == {"n": 2} and store.read("n.json") == {"n": 2}
