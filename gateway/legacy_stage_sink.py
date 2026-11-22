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

# One line per block, added by the pull request that builds it.
HANDLERS: dict[str, Callable[[object, ViewIntent], None]] = {}


def _prefix(intent_id: str) -> str:
    return intent_id.split(":", 1)[0]


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
