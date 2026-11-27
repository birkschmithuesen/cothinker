# CoThinker — tests for gateway/consumer.py's block host wiring (issue
# #38, Wave B.1): feed_utterance() and its failure handling.
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_consumer_block_host.py"""
from __future__ import annotations

from gateway import block_host_wiring as bhw
from shared.block_registry import load_from
from shared.blocks import BLOCK_API_VERSION


class FakeMinutesFile:
    def __init__(self, calls):
        self.calls = calls

    def append_raw(self, text, speaker=None):
        self.calls.append(("append_raw", text, speaker))


class FakeBlocks:
    """A SessionRuntime stand-in that records dispatch() calls or breaks on
    demand, without needing a real BlockHost."""
    def __init__(self, calls, *, raise_on_dispatch=False):
        self.calls = calls
        self.log_lines = []
        self.raise_on_dispatch = raise_on_dispatch

    def dispatch(self, kind, payload):
        self.calls.append(("dispatch", kind, payload))
        if self.raise_on_dispatch:
            raise RuntimeError("dispatch is broken")
        return 1

    def log(self, line):
        self.log_lines.append(line)


class RaisingBlock:
    id = "capture-bullets"
    api_version = BLOCK_API_VERSION

    def start(self, ctx, config):
        pass

    def on_event(self, ev):
        raise RuntimeError("block is broken")

    def stop(self):
        pass


def test_feed_utterance_appends_raw_before_dispatch(tmp_path):
    calls = []
    minutes = FakeMinutesFile(calls)
    blocks = FakeBlocks(calls)
    bhw.feed_utterance(minutes, blocks, "hallo", "max", {"at": 1.0})
    assert calls[0] == ("append_raw", "hallo", "max")
    assert calls[1][:2] == ("dispatch", "utterance")
    assert calls[1][2]["text"] == "hallo" and calls[1][2]["speaker"] == "max"


def test_feed_utterance_survives_a_raising_block(tmp_path):
    logs = []
    session_blocks = bhw.build_session_blocks(
        session_dir=tmp_path, sink=lambda intent: None, log=logs.append,
        load=load_from({"capture-bullets": RaisingBlock}), clock=lambda: 0.0,
    )
    minutes = FakeMinutesFile([])
    bhw.feed_utterance(minutes, session_blocks, "hallo", "max", {"at": 1.0})
    assert minutes.calls == [("append_raw", "hallo", "max")]
    assert any("boom" not in line and "capture-bullets" in line and "broken" in line
               for line in logs)


def test_feed_utterance_keeps_raw_when_dispatch_breaks(tmp_path):
    calls = []
    minutes = FakeMinutesFile(calls)
    blocks = FakeBlocks(calls, raise_on_dispatch=True)
    bhw.feed_utterance(minutes, blocks, "hallo", "max", {"at": 1.0})
    assert calls[0] == ("append_raw", "hallo", "max")
    assert any("dispatch failed" in line for line in blocks.log_lines)
