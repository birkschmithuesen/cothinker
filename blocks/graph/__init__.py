# CoThinker — blocks/graph/: the session graph block (ADR 0010, issue #48)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`graph`: the session graph the stage tab draws (ADR 0010, issue #48).

Until this block existed, `stage/stage.py` rebuilt the graph itself, once
per HTTP request. Now it is built here, from the block host's own
`block_output` events, and handed to the stage as one view intent
(`graph:session`, kind `graph`) only when the graph actually changed --
the stage surface writes that intent's payload into `buehne.json` under
`session_graph`, and `stage/stage.py::session_graph_payload()` reads it
back out (ADR 0006: a surface never calls a block).

TWO SIGNALS, from two sibling blocks not yet built in this repository
(Wave C): a `{"topic": {...}}` output from the `topics` block, one row
per topic change, in the same shape `blocks/graph/session_graph.py`'s
`topics_from_stage()` already reads out of `buehne.json["history"]`
today (`title`, `set_at`, `markdown`, `collapsed_markdown`, `id`); and a
`{"minutes_changed": true}` output from the `minutes-document` block,
meaning the protocol file on disk has new decisions/tasks/questions to
re-read. Both just trigger a rebuild -- the rebuild itself re-reads the
protocol file fresh every time, so a `minutes_changed` needs no payload
of its own.

THE HYSTERESIS MOVES HERE WITH THE BLOCK (issue #48 scope): one
`Hysteresis` instance for the block's whole lifetime, exactly the grace
window `stage/stage.py` used to keep in a module global.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from blocks.graph.session_graph import build_session_graph, protocol_head, topics_from_stage
from graph.neighbours import HardEdges
from graph.ring_select import Hysteresis
from graph.vault_notes import DEFAULT_EXCLUDE, scan_vault
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

PROTOCOL_FILE = "protokoll.md"
TOPICS_STATE_FILE = "topics.json"


def _vault_index(root: Path | None, exclude: Sequence[str]):
    """`(NoteIndex, HardEdges)`, or `(None, None)` without a vault root."""
    if root is None:
        return None, None
    try:
        index = scan_vault(root, exclude=exclude)
    except OSError:
        return None, None
    return index, HardEdges(index)


class GraphBlock:
    """Reflect's `graph` block: the session graph (ADR 0010).

    Owns the ring-selection hysteresis across the whole session -- moved
    here from `stage/stage.py`'s module globals (issue #48) -- and the
    session's topic history, since the block no longer reads it back out
    of `buehne.json`: it is told about every topic through `block_output`.
    """

    id = "graph"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: BlockContext | None = None
        self._hysteresis = Hysteresis()
        self._vault_root: Path | None = None
        self._vault_exclude: Sequence[str] = DEFAULT_EXCLUDE
        self._history: list[dict] = []
        self._last_digest: str | None = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        root = config.get("vault_root")
        self._vault_root = Path(root) if root else None
        exclude = config.get("vault_exclude")
        if exclude is not None:
            self._vault_exclude = tuple(exclude)
        self._history = list(ctx.state.read(TOPICS_STATE_FILE).get("history") or [])

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "block_output":
            return ()
        payload = ev.payload if isinstance(ev.payload, Mapping) else {}
        topic = payload.get("topic")
        changed = isinstance(topic, Mapping)
        if changed:
            self._history.append(dict(topic))
            self._ctx.state.write(TOPICS_STATE_FILE, {"history": self._history})
        elif payload.get("minutes_changed"):
            changed = True
        if not changed:
            return ()
        graph = self._build()
        digest = hashlib.sha256(
            json.dumps(graph, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if digest == self._last_digest:
            return ()
        self._last_digest = digest
        self._ctx.emit(ViewIntent(id="graph:session", block=self.id, kind="graph",
                                  audience="room", payload=graph))
        return ()

    def stop(self) -> None:
        pass

    def _build(self) -> dict:
        ctx = self._ctx
        try:
            text = (ctx.session_dir / PROTOCOL_FILE).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        topics = topics_from_stage({"history": self._history}, text)
        index, edges = _vault_index(self._vault_root, self._vault_exclude)
        title = next((line[2:].strip() for line in protocol_head(text).splitlines()
                     if line.startswith("# ") and line[2:].strip()), "")

        def resolve(name: str) -> str | None:
            """A wikilink target -> its vault node id, or None (issue #48
            scope: only the vault, not the whole project's files -- those
            are the stage's own concern, not this block's)."""
            name = str(name or "").strip()
            if not name or index is None:
                return None
            note = index.resolve(name)
            return "vault:" + note.rel if note is not None else None

        return build_session_graph(
            topics, session_id="file:" + PROTOCOL_FILE,
            session_label=title or "Sitzung", resolve=resolve,
            index=index, edges=edges, hysteresis=self._hysteresis)
