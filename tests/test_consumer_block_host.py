# CoThinker — tests for gateway/consumer.py's block host wiring (issue
# #38, Wave B.1): feed_utterance() and the legacy stage sink it writes to.
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_consumer_block_host.py"""
from __future__ import annotations

from gateway import block_host_wiring as bhw
from gateway import legacy_stage_sink as lss
from shared.blocks import BLOCK_API_VERSION
from shared.surface import ViewIntent


class FakeMinutesFile:
    def __init__(self, calls):
        self.calls = calls

    def append_raw(self, text, speaker=None):
        self.calls.append(("append_raw", text, speaker))


class FakeBlocks:
    """A SessionBlocks stand-in that records dispatch() calls or breaks on
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
        registry={"capture-bullets": RaisingBlock}, clock=lambda: 0.0,
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


def test_legacy_sink_unknown_prefix_logged_once():
    logs = []
    writer = object()
    sink = lss.make_sink(writer, logs.append)
    sink(ViewIntent(id="capture-bullets:1", block="capture-bullets", kind="status",
                    audience="room", payload={}))
    sink(ViewIntent(id="capture-bullets:2", block="capture-bullets", kind="status",
                    audience="room", payload={}))
    unknown_logs = [line for line in logs if "capture-bullets" in line]
    assert len(unknown_logs) == 1


def test_legacy_sink_links_row_updates_state_and_writes():
    from gateway.stage_state import StageState

    class FakeWriter:
        def __init__(self):
            self.state = StageState()
            self.writes = 0

        def write(self):
            self.writes += 1

    writer = FakeWriter()
    sink = lss.make_sink(writer, lambda line: None)
    sink(ViewIntent(id="links:T1", block="knowledge-link", kind="artefact",
                    audience="room",
                    payload={"links": ["notes/Alpha.md", "Beta.md"]}))
    assert writer.state.links == [
        {"file": "notes/Alpha.md", "label": "Alpha"},
        {"file": "Beta.md", "label": "Beta"},
    ]
    assert writer.writes == 1
