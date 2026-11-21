#!/usr/bin/env python3
# CoThinker — gateway/legacy_stage_sink.py: view intents onto today's stage
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Routes the block host's view intents to today's `StageWriter` (issue
#38, Wave B.1) -- the interim module between the Surface API (ADR 0006)
and a stage that still only understands `buehne.json`.

A `ViewIntent.id` is "<block-id>:<suffix>" by the blocks' own convention;
`HANDLERS` keys on that block-id prefix, one line per block, the same way
`gateway/block_host_wiring.REGISTRY` does. The whole module is replaced,
not extended, by the stage surface of Wave C (ADR 0006/0008) -- it exists
only to bridge the gap.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping

from shared.surface import ViewIntent

# One line per block, added by the pull request that builds it.
def _links(writer: object, intent: ViewIntent) -> None:
    """`links:<topic_id>` (knowledge-link): the stage's link list, the same
    update as the surface action `links`."""
    items = [{"file": path, "label": path.rsplit("/", 1)[-1].removesuffix(".md")}
             for path in intent.payload.get("links", ())]
    writer.state.apply({"action": "links", "items": items})
    writer.write()


def _topic(writer: object, intent: ViewIntent) -> None:
    """A topic intent: the provisional one only needs the stage file
    rewritten (the lane integration pushes the history entry from the
    block's `topic` output); the final one swaps the provisional title in
    place, as the lane integration's titler callback did, then rewrites."""
    payload = intent.payload
    if not payload.get("provisional", True):
        old = payload.get("replaces")
        state = getattr(writer, "state", None)
        if old and state is not None:
            for entry in reversed(list(state.history)):
                if entry.title == old:
                    state.retitle(entry.id, old, payload["title"])
                    break
    writer.write()


HANDLERS: dict[str, Callable[[object, ViewIntent], None]] = {
    "links": _links,
    "topic": _topic,
}


def _log_only(writer: object, intent: ViewIntent) -> None:
    """Today's stage has no place for this intent: it stays on the intent
    board; this handler does nothing and nothing is drawn."""


# Keyed on the block-id prefix, i.e. "condense" for ids "condense:<topic>".
HANDLERS["condense"] = _log_only


def _prefix(intent_id: str) -> str:
    return intent_id.split(":", 1)[0]


def _apply_status(writer: object, intent: ViewIntent) -> None:
    """`status:line` (blocks/status_line/): the intent never carries a
    clock value, so the "zuletzt {at}" timestamp is stamped here, from
    this call's own clock, not from the block."""
    detail = intent.payload.get("detail", {})
    writer.set_status(intent.payload["state"],
                      at=datetime.now().strftime("%H:%M:%S"),
                      count=detail.get("count", 0),
                      failed=detail.get("failed"))


HANDLERS["status"] = _apply_status


def make_sink(writer: object, log: Callable[[str], None]) -> Callable[[ViewIntent], None]:
    """An unknown prefix is logged once, not once per intent."""
    logged: set[str] = set()

    def sink(intent: ViewIntent) -> None:
        prefix = _prefix(intent.id)
        handler = HANDLERS.get(prefix)
        if handler is None:
            if prefix not in logged:
                logged.add(prefix)
                log(f"legacy stage sink: no handler for intent prefix {prefix!r}")
            return
        handler(writer, intent)

    return sink
