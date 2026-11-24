# CoThinker — tests for blocks/knowledge_link: the knowledge-link block
# (issue #42, Wave B.5)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q blocks/knowledge_link/tests/test_knowledge_link.py"""
from __future__ import annotations

from blocks.knowledge_link import KnowledgeLinkBlock
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore


def make_block(tmp_path, project_dir):
    emitted = []
    session = tmp_path / "session"
    session.mkdir(exist_ok=True)
    ctx = BlockContext(
        instance=None, session_dir=session,
        state=StateStore(session / ".blocks" / "knowledge-link"),
        agent=None, llm=None, language="de", clock=lambda: 0.0,
        emit=emitted.append, artefact=lambda name, text: None)
    block = KnowledgeLinkBlock()
    config = {} if project_dir is None else {"project_dir": str(project_dir)}
    block.start(ctx, config)
    return block, emitted


def make_project(tmp_path):
    project = tmp_path / "project"
    (project / "hintergrund").mkdir(parents=True)
    (project / "hintergrund" / "klimaplan.md").write_text("# Klimaplan\n", encoding="utf-8")
    (project / "unrelated.md").write_text("# Other\n", encoding="utf-8")
    return project


def topic(topic_id, title):
    return CoreEvent("block_output", {
        "from": "topics", "output": "topic", "topic_id": topic_id, "title": title})


def test_topic_change_emits_knowledge_intent(tmp_path):
    block, emitted = make_block(tmp_path, make_project(tmp_path))
    assert block.on_event(topic("t1", "Der Klimaplan der Stadt")) == ()
    assert len(emitted) == 1
    intent = emitted[0]
    assert intent.id == "knowledge:t1"
    assert intent.block == "knowledge-link"
    assert intent.kind == "knowledge"
    assert intent.payload == {
        "topic_id": "t1", "query": "Der Klimaplan der Stadt",
        "hits": [{"kind": "hintergrund", "title": "Klimaplan",
                  "href": "hintergrund/klimaplan.md"}],
    }


def test_hits_are_wiki_root_relative(tmp_path):
    project = make_project(tmp_path)
    block, emitted = make_block(tmp_path, project)
    block.on_event(topic("t1", "Klimaplan"))
    block.on_event(CoreEvent("block_output", {
        "from": "minutes-document", "output": "minutes_changed"}))
    assert emitted
    for intent in emitted:
        assert intent.id == "knowledge:t1"
        for hit in intent.payload["hits"]:
            href = hit["href"]
            assert not href.startswith("/")
            assert str(project) not in href
            assert (project / href).is_file()


def test_no_vault_no_intent(tmp_path):
    block, emitted = make_block(tmp_path, None)
    block.on_event(topic("t1", "Klimaplan"))
    block.on_event(CoreEvent("block_output", {"output": "minutes_changed"}))
    assert emitted == []
    empty = tmp_path / "leer"
    empty.mkdir()
    block2, emitted2 = make_block(tmp_path, empty)
    block2.on_event(topic("t1", "Klimaplan"))
    assert emitted2 == []
