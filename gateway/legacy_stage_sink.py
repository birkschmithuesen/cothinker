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

from typing import Callable, Mapping

from shared.surface import ViewIntent


def _apply_pending(writer: object, intent: ViewIntent,
                   ids: dict[str, str]) -> None:
    """`pending:<n>` of the decisions-tasks-questions block: the stage's own
    announce / settle / warn, exactly as the policy runner called them on
    the stage writer before it emitted intents. The intent id is the
    block's; the stage's pending id is kept per sink (`ids`), so a settle
    ends the very pending its announce put up."""
    op = intent.payload["op"]
    if op == "announce":
        stage_id = writer.announce(intent.payload["kind"], intent.payload["label"])
        if stage_id:
            ids[intent.id] = stage_id
    elif op == "settle":
        stage_id = ids.pop(intent.id, "")
        if stage_id:
            writer.settle(stage_id, intent.payload["outcome"])
    elif op == "warn":
        writer.warn(intent.payload["text"])


# One line per block, added by the pull request that builds it.
HANDLERS: dict[str, Callable[[object, ViewIntent, dict[str, str]], None]] = {
    "pending": _apply_pending,
}


def _prefix(intent_id: str) -> str:
    return intent_id.split(":", 1)[0]


def make_sink(writer: object, log: Callable[[str], None]) -> Callable[[ViewIntent], None]:
    """An unknown prefix is logged once, not once per intent."""
    logged: set[str] = set()
    pending_ids: dict[str, str] = {}

    def sink(intent: ViewIntent) -> None:
        prefix = _prefix(intent.id)
        handler = HANDLERS.get(prefix)
        if handler is None:
            if prefix not in logged:
                logged.add(prefix)
                log(f"legacy stage sink: no handler for intent prefix {prefix!r}")
            return
        handler(writer, intent, pending_ids)

    return sink
