# CoThinker — blocks/release/: publish a session's results to guests
# (issue #102)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The release block (issue #102): publishes the chosen results (posters,
texts, graph) to the guest view when the operator releases them, to the
target the instance configures.

Reaching the goodbye cue (`phase` event) only lets the block see that the
session is there; it never releases anything by itself. Only an `action`
event naming `RELEASE_INTENT_ID` (the operator's own gesture, `kind`
`"accept"` to release or `"reject"` to withdraw) builds and publishes a
release (`blocks.release.logic`). An instance that has not configured a
`target` for this block (`[[blocks]] id = "release"`, `target = "..."`)
makes the operator's action a no-op, the same way an unconfigured vault
makes `knowledge_link` a no-op.

The actual guest-view surface that renders the `artefact` intent this
block emits is a sibling task's concern (issue #102's "Out" line) -- this
block only publishes the intent and the `block_output`.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

from .logic import build_release, build_withdrawal

RELEASE_INTENT_ID = "release:control"


class ReleaseBlock:
    id = "release"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._target: Optional[str] = None
        self._released: Optional[dict] = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._target = config.get("target")

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "action" or ev.payload.get("intent_id") != RELEASE_INTENT_ID:
            return ()
        if not self._target:
            return ()
        at_ms = int(self._ctx.clock() * 1000)
        kind = ev.payload.get("kind")
        if kind == "accept":
            payload = ev.payload.get("payload") or {}
            self._released = build_release(
                version=payload.get("version"), quote=payload.get("quote"),
                prologue=payload.get("prologue"), at_ms=at_ms)
        elif kind == "reject" and self._released is not None:
            self._released = build_withdrawal(self._released, at_ms)
        else:
            return ()
        self._ctx.emit(ViewIntent(
            id="release:results", block=self.id, kind="artefact", audience="room",
            payload={"target": self._target, "released": self._released}))
        return (CoreEvent("block_output", {"from": self.id, "released": self._released}),)

    def stop(self) -> None:
        pass


def make_block() -> ReleaseBlock:
    return ReleaseBlock()
