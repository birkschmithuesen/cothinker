# CoThinker — tests of blocks/graph's GraphBlock (issue #48)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q blocks/graph/tests/test_graph.py"""
from __future__ import annotations

from pathlib import Path

from blocks.graph import GraphBlock
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def _ctx(tmp_path, emitted):
    return BlockContext(
        instance=None, session_dir=tmp_path,
        state=StateStore(tmp_path / ".blocks" / "graph"), agent=None, llm=None,
        language="de", clock=lambda: 0.0, emit=emitted.append,
        artefact=lambda name, text: None)


def _topic_event(title: str) -> CoreEvent:
    return CoreEvent("block_output",
                     {"topic": {"title": title, "set_at": "", "markdown": ""}})


def test_topic_change_emits_graph(tmp_path) -> None:
    emitted = []
    block = GraphBlock()
    block.start(_ctx(tmp_path, emitted), {})
    block.on_event(_topic_event("Thema A"))
    assert len(emitted) == 1
    assert emitted[0].id == "graph:session" and emitted[0].block == "graph"
    assert emitted[0].kind == "graph"


def test_minutes_changed_emits_graph(tmp_path) -> None:
    emitted = []
    block = GraphBlock()
    block.start(_ctx(tmp_path, emitted), {})
    block.on_event(CoreEvent("block_output", {"minutes_changed": True}))
    assert len(emitted) == 1
    assert emitted[0].id == "graph:session" and emitted[0].block == "graph"
    assert emitted[0].kind == "graph"


def test_unchanged_graph_emits_nothing(tmp_path) -> None:
    emitted = []
    block = GraphBlock()
    block.start(_ctx(tmp_path, emitted), {})
    block.on_event(_topic_event("Thema A"))
    block.on_event(_topic_event("Thema A"))
    assert len(emitted) == 1


def test_payload_shape_matches_page_script(tmp_path) -> None:
    emitted = []
    block = GraphBlock()
    block.start(_ctx(tmp_path, emitted), {})
    block.on_event(_topic_event("Thema A"))
    payload = emitted[0].payload
    assert set(payload) == {"seed", "nodes", "edges", "hidden"}
    assert isinstance(payload["seed"], str)
    assert isinstance(payload["nodes"], list) and payload["nodes"]
    assert isinstance(payload["edges"], list)
    assert isinstance(payload["hidden"], int)
    node = payload["nodes"][0]
    assert {"id", "label", "kind", "ring", "href", "time", "tag", "parent",
            "hidden", "grace"} <= set(node)
