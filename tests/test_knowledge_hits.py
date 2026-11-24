# CoThinker — tests for the knowledge hit extraction (issue #148)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`WikiLayer.knowledge_hits()`: the named-field replacement for the old
bare-path `artefact`/`links:` payload (issue #148, closes design partner's
issue #144) -- kind/title/href per hit, from a small invented wiki.

python3 -m pytest -q tests/test_knowledge_hits.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from blocks.knowledge_link import KnowledgeLinkBlock
from blocks.knowledge_link.wiki import WikiLayer
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore
from gateway import call_lane as call_lane_mod
from gateway import consumer


def make_wiki(tmp_path):
    """A small invented wiki: frontmatter `type:`/`title:`, a heading-only
    note, a plain note, and a wikilink between two of them."""
    root = tmp_path / "wiki"
    (root / "ablage").mkdir(parents=True)
    (root / "ablage" / "budget.md").write_text(
        "---\ntype: finance\ntitle: Budget overview\n---\n"
        "See [[heating-plan]] for the figures.\n", encoding="utf-8")
    (root / "ablage" / "heating-plan.md").write_text(
        "# Heating plan\n\nNo frontmatter, just a heading.\n", encoding="utf-8")
    (root / "notes.md").write_text("Plain note, no frontmatter, no heading.\n",
                                   encoding="utf-8")
    return root


def test_hit_kind_and_title_come_from_frontmatter(tmp_path):
    wiki = WikiLayer(make_wiki(tmp_path))
    hits = wiki.knowledge_hits("Budget and heating plan")
    by_href = {h["href"]: h for h in hits}
    assert by_href["ablage/budget.md"] == {
        "kind": "finance", "title": "Budget overview", "href": "ablage/budget.md"}


def test_hit_kind_falls_back_to_top_folder_title_to_heading(tmp_path):
    wiki = WikiLayer(make_wiki(tmp_path))
    hits = wiki.knowledge_hits("Budget and heating plan")
    by_href = {h["href"]: h for h in hits}
    assert by_href["ablage/heating-plan.md"] == {
        "kind": "ablage", "title": "Heating plan", "href": "ablage/heating-plan.md"}


def test_hit_title_falls_back_to_the_file_stem(tmp_path):
    wiki = WikiLayer(make_wiki(tmp_path))
    hits = wiki.knowledge_hits("Notes about everything")
    assert hits == [{"kind": "", "title": "notes", "href": "notes.md"}]


def test_no_vault_yields_none(tmp_path):
    wiki = WikiLayer(tmp_path / "does-not-exist")
    assert wiki.knowledge_hits("Budget") is None


# -------------------------------------------- COTHINKER_WIKI_DIR separation


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
    block.start(ctx, {})
    block.use_project(project_dir)
    return block, ctx, emitted


def topic(topic_id, title):
    return CoreEvent("block_output", {
        "from": "topics", "output": "topic", "topic_id": topic_id, "title": title})


def test_wiki_dir_env_set_reads_from_it_not_the_project_folder(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "session-only.md").write_text("# Session only note\n",
                                                  encoding="utf-8")
    wiki_dir = make_wiki(tmp_path)
    monkeypatch.setenv("COTHINKER_WIKI_DIR", str(wiki_dir))

    block, _, emitted = make_block(tmp_path, project_dir)
    block.on_event(topic("t1", "Session only budget heating plan"))

    assert len(emitted) == 1
    hrefs = {h["href"] for h in emitted[0].payload["hits"]}
    # Hits come from the wiki dir's notes, not the project folder's.
    assert hrefs and "session-only.md" not in hrefs
    assert hrefs <= {"ablage/budget.md", "ablage/heating-plan.md", "notes.md"}


def test_wiki_dir_env_unset_keeps_using_the_project_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("COTHINKER_WIKI_DIR", raising=False)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "klimaplan.md").write_text("# Klimaplan\n", encoding="utf-8")

    block, _, emitted = make_block(tmp_path, project_dir)
    block.on_event(topic("t1", "Klimaplan"))

    assert len(emitted) == 1
    assert emitted[0].payload["hits"] == [
        {"kind": "", "title": "Klimaplan", "href": "klimaplan.md"}]


def test_call_lane_paths_never_mention_cothinker_wiki_dir(tmp_path, monkeypatch):
    """The wiki root is read ONLY by the knowledge-link block (issue #148):
    it must never reach the call lane's own stable prefix, built by
    `gateway.consumer.build_lanes` from `folder`/`path`/`COTHINKER_EXTRA_DIRS`
    alone. This would catch a future mistake of threading the var into
    `extra_dirs` or `folder` there."""
    folder = tmp_path / "project"
    folder.mkdir()
    protocol = tmp_path / "protokoll.md"
    protocol.write_text("", encoding="utf-8")
    wiki_dir = tmp_path / "wiki-root-marker"
    wiki_dir.mkdir()
    monkeypatch.setenv("COTHINKER_WIKI_DIR", str(wiki_dir))
    monkeypatch.delenv("COTHINKER_EXTRA_DIRS", raising=False)

    lane = consumer.build_lanes(
        None, lambda *a, **k: None, call_command=["true"],
        session_id="s1", project_agenda="", state=None, writer=None,
        folder=folder, path=protocol)

    assert str(wiki_dir) not in lane.call_paths
    assert str(wiki_dir) not in call_lane_mod.call_paths_text(folder, protocol)
