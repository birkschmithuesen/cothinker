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

Output: `block_output {"minutes_changed": {"name": "protokoll.md"}}`, only
after a write that landed.

Methods the session host reads until the main agent turn becomes a block
(nothing else calls them): `is_v2`, `mode`, `current_topic()`,
`current_topic_title()`, `last_result()`.

`last_result()` and the result slot behind it are per thread: the host
dispatches synchronously on the caller's thread and reads the result right
after. A producer that writes outside the host's file lock can render from
a stale read; the raw section is safe regardless, the host re-reads it
under its lock when it splices.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Mapping, Optional, Sequence

from blocks.minutes_document.minutes import EntryResult, MinutesDocument
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.minutes_format import DEFAULT_MODE
from shared.modules import CoreEvent

ARTEFACT_NAME = "protokoll.md"

_CHANGED = CoreEvent("block_output",
                     {"minutes_changed": {"name": ARTEFACT_NAME}})


class MinutesDocumentBlock:
    id = "minutes-document"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._doc: Optional[MinutesDocument] = None
        self._tl = threading.local()

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
        if self._doc is None and any(
                k in p for k in ("entry", "bullet", "condensed", "topic")):
            self._tl.result = EntryResult("not written")
            return []
        doc = self._doc
        res: Optional[EntryResult] = None
        if "entry" in p:
            e = p["entry"] or {}
            res = doc.add_entry(e.get("tag"), e.get("text"),
                                stamp=e.get("stamp"), names=e.get("names"))
        elif "bullet" in p or "condensed" in p:
            e = p.get("bullet") or p.get("condensed") or {}
            res = doc.add_entry("notiz", e.get("text"), stamp=e.get("stamp"))
        elif "topic" in p:
            e = p["topic"] or {}
            res = EntryResult(doc.open_topic(e.get("title"), e.get("stamp")))
        if res is None:
            return []
        self._tl.result = res
        return [_CHANGED] if res.reason == "" else []

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

    # ------------------------------------------- reads for the session host
    @property
    def is_v2(self) -> bool:
        return self._doc.is_v2 if self._doc is not None else False

    @property
    def mode(self) -> str:
        return self._doc.mode if self._doc is not None else DEFAULT_MODE

    def current_topic(self) -> tuple[str, str]:
        return self._doc.current_topic() if self._doc is not None else ("", "")

    def current_topic_title(self) -> str:
        return self.current_topic()[1]

    def last_result(self) -> Optional[EntryResult]:
        """The result of this thread's last write event; clears it."""
        res = getattr(self._tl, "result", None)
        self._tl.result = None
        return res
