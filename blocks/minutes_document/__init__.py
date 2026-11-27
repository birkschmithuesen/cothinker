# CoThinker — blocks/minutes_document: the minutes document block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The minutes-document block (ADR 0010, `docs/architecture/target.md` §5).

It renders the sections of the minutes document and hands them to the host
through `ctx.artefact("protokoll.md", head)`; the session host splices them
into the file in front of the raw section and is its only writer.

Input, `block_output` payload keys (other keys are ignored):

- `minutes_open {"path", "mode"}`: the host's seed, once; nothing is read
  before it.
- `entry {"tag", "text", "stamp", "names"}`
- `bullet {"text", "stamp"}` and `condensed {"text", "stamp"}`: filed as a
  `notiz` entry. Nothing produces these two yet.
- `topic {"title", "stamp"}`

Write events (`entry`, `bullet`, `condensed`, `topic`) must be dispatched
under the host's file lock: the artefact path is shared and the block reads
the file fresh each time, so an unlocked dispatch risks a stale read.

- `minutes_query {}`: asks for the document's state.

Output, all `block_output` payloads:

- `{"minutes_changed": {"name": "protokoll.md"}}`, only after a write that
  landed.
- `{"from": "minutes-document", "kind": "entry_result", "reason",
  "links_seen", "links_dead", "attempted"}` after every write event
  (`entry`, `bullet`, `condensed`, `topic`): what the write came to.
  `reason` is "" when it landed.
- `{"from": "minutes-document", "kind": "minutes_state", "is_v2", "mode",
  "topic_key", "topic_title"}` as the answer to `minutes_query`.

The session host reads these with its output listener; it calls no method
of the block. A producer that writes outside the host's file lock can
render from a stale read; the raw section is safe regardless, the host
re-reads it under its lock when it splices.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Sequence

from blocks.minutes_document.minutes import EntryResult, MinutesDocument
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.minutes_format import DEFAULT_MODE
from shared.modules import CoreEvent

ARTEFACT_NAME = "protokoll.md"

BLOCK_ID = "minutes-document"

_CHANGED = CoreEvent("block_output",
                     {"minutes_changed": {"name": ARTEFACT_NAME}})


class MinutesDocumentBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._doc: Optional[MinutesDocument] = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx

    def stop(self) -> None:
        pass

    # ----------------------------------------------------------- events
    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "block_output" or not isinstance(ev.payload, Mapping):
            return []
        p = ev.payload
        if "minutes_open" in p:
            self._open(p["minutes_open"])
            return []
        if "minutes_query" in p:
            return [self._state()]
        write_keys = ("entry", "bullet", "condensed", "topic")
        if not any(k in p for k in write_keys):
            return []
        doc = self._doc
        if doc is None:
            return [_result_event(EntryResult("not written"))]
        if "entry" in p:
            e = p["entry"] or {}
            res = doc.add_entry(e.get("tag"), e.get("text"),
                                stamp=e.get("stamp"), names=e.get("names"))
        elif "bullet" in p or "condensed" in p:
            e = p.get("bullet") or p.get("condensed") or {}
            res = doc.add_entry("notiz", e.get("text"), stamp=e.get("stamp"))
        else:
            e = p["topic"] or {}
            res = EntryResult(doc.open_topic(e.get("title"), e.get("stamp")))
        out = [_CHANGED] if res.reason == "" else []
        return out + [_result_event(res)]

    def _state(self) -> CoreEvent:
        key, title = self.current_topic()
        return CoreEvent("block_output", {
            "from": BLOCK_ID, "kind": "minutes_state", "is_v2": self.is_v2,
            "mode": self.mode, "topic_key": key, "topic_title": title})

    def _open(self, seed: object) -> None:
        if self._ctx is None or not isinstance(seed, Mapping):
            return
        raw_path = seed.get("path")
        if not raw_path:
            return
        ctx = self._ctx
        self._doc = MinutesDocument(
            Path(str(raw_path)), mode=str(seed.get("mode") or DEFAULT_MODE),
            write_head=lambda head: ctx.artefact(ARTEFACT_NAME, head))

    # ------------------------------------------------ the block's own state
    @property
    def is_v2(self) -> bool:
        return self._doc.is_v2 if self._doc is not None else False

    @property
    def mode(self) -> str:
        return self._doc.mode if self._doc is not None else DEFAULT_MODE

    def current_topic(self) -> tuple[str, str]:
        return self._doc.current_topic() if self._doc is not None else ("", "")


def _result_event(res: EntryResult) -> CoreEvent:
    return CoreEvent("block_output", {
        "from": BLOCK_ID, "kind": "entry_result", "reason": res.reason,
        "links_seen": res.links_seen, "links_dead": res.links_dead,
        "attempted": res.attempted})


def make_block() -> MinutesDocumentBlock:
    return MinutesDocumentBlock()
