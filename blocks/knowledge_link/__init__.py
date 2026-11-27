# CoThinker — blocks/knowledge_link: the knowledge-link block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Knowledge link (ADR 0010, `docs/architecture/target.md` §5): which
project notes a topic belongs to, found from the notes themselves.

The block owns the wiki layer (`blocks/knowledge_link/wiki.py`, a
`WikiLayer` over the wiki root -- the project folder, or `COTHINKER_WIKI_DIR`
when set, see `use_project`). It consumes `block_output` events `topic`
(the topic changed) and `minutes_changed` (the minutes -- and with them
possibly the notes -- changed) and answers with one view intent,
`knowledge:<topic_id>`, kind `knowledge` (issue #148), payload
`{"topic_id": ..., "query": ..., "hits": [{"kind", "title", "href"}, ...]}`,
`href` relative to the wiki root. Without a vault (no project folder, or
one without notes) it emits nothing.

A `block_output` payload names its kind in `output`:
`{"output": "topic", "topic_id": ..., "title": ...}` and
`{"output": "minutes_changed"}`.

Interim host handles: until the stage surface replaces the interim stage
path, the session host (`gateway/consumer.py`) calls `use_project`,
`create_topic_page`, `topic_title`, `wants_page`, `bare_title`,
`title_max`, `page_links`, `taken` and `has_vault` directly, and nothing
else does.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional, Sequence

from blocks.knowledge_link import wiki as _wiki
from blocks.knowledge_link.wiki import PAGE_LINKS, WikiLayer
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

BLOCK_ID = "knowledge-link"


class KnowledgeLinkBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._wiki: Optional[WikiLayer] = None
        self._topic: Optional[tuple[str, str]] = None   # (topic_id, title)
        self._last: dict[str, list[dict]] = {}          # topic_id -> hits emitted

    # -- block contract ------------------------------------------------
    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        project = config.get("project_dir") if config else None
        if project:
            self.use_project(project)

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "block_output":
            return ()
        output = ev.payload.get("output")
        if output == "topic":
            topic_id, title = ev.payload.get("topic_id"), ev.payload.get("title")
            if not isinstance(topic_id, str) or not topic_id or not isinstance(title, str):
                return ()
            self._topic = (topic_id, title)
            self._emit_knowledge(force=True)
        elif output == "minutes_changed" and self._topic is not None:
            self._emit_knowledge(force=False)
        return ()

    def stop(self) -> None:
        pass

    def _emit_knowledge(self, *, force: bool) -> None:
        if self._wiki is None or self._ctx is None or self._topic is None:
            return
        topic_id, title = self._topic
        hits = self._wiki.knowledge_hits(title, session_dir=self._ctx.session_dir)
        if hits is None:
            return
        if not force and self._last.get(topic_id) == hits:
            return
        self._last[topic_id] = hits
        self._ctx.emit(ViewIntent(
            id=f"knowledge:{topic_id}", block=self.id, kind="knowledge",
            audience="room",
            payload={"topic_id": topic_id, "query": title, "hits": hits}))

    # -- interim host handles ---------------------------------------------
    def use_project(self, project_dir) -> None:
        """Gives the block its vault: `COTHINKER_WIKI_DIR` (a separate,
        read-only wiki root) when set, else the round's documents folder.

        This is the ONLY place that reads `COTHINKER_WIKI_DIR`: the
        knowledge-link block is the only reader of it (issue #148) -- it
        is never added to the call lane's paths or `COTHINKER_EXTRA_DIRS`.
        """
        wiki_dir = os.environ.get("COTHINKER_WIKI_DIR", "").strip()
        self._wiki = WikiLayer(Path(wiki_dir) if wiki_dir else Path(project_dir))

    @property
    def has_vault(self) -> bool:
        return self._wiki is not None

    def title_max(self) -> int:
        return _wiki.TITLE_MAX

    def bare_title(self, line) -> str:
        return _wiki.bare_title(line)

    def wants_page(self, text) -> bool:
        return _wiki.wants_page(text)

    def topic_title(self, titel, mark: str, *, session_dir=None) -> tuple[str, int]:
        return _wiki.topic_title(titel, mark, self._wiki, session_dir=session_dir)

    def page_links(self, titel: str, session_dir=None) -> list[str]:
        if self._wiki is None:
            return []
        return self._wiki.page_links(titel, PAGE_LINKS, session_dir=session_dir)

    def taken(self, stem: str) -> bool:
        return self._wiki is not None and self._wiki.taken(stem)

    def create_topic_page(self, session_dir, project_dir, title, links, mark,
                          created, protocol_rel, *, report=None) -> Optional[Path]:
        return _wiki.create_topic_page(
            session_dir, project_dir, title, links, mark, created, protocol_rel,
            taken=self.taken, report=report)


def make_block() -> KnowledgeLinkBlock:
    return KnowledgeLinkBlock()
