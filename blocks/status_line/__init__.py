# CoThinker — blocks/status_line/: the status-line block (issue #41, Wave
# B.4)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The status-line block (ADR 0008, ADR 0018): picks the winning operating
state (`listening`/`backlog`/`working`/`failed`) from `tick.components` and
emits the view intent `status:line` only when the picked state or its
detail actually changes.

`tick.components` is filled by two different callers today: the session
ticker (`gateway/consumer.py`'s `ticker()`, once a second, only carries
`queue_backlog` and `open_block_chars`) and `publish_status()` (on every
utterance, also carries `turn_running` and `run_failed`). A key missing
from one caller's `components` must not reset what the other caller last
reported -- otherwise the operating line would flip back to `listening`
every second while a turn is still running. Each field is therefore
sticky: it only updates when its key is present in the event.

The intent never carries a clock value (issue #41's hard requirement): the
"zuletzt {at}" timestamp the stage renders for `listening` is stamped by
`gateway.legacy_stage_sink` from its own clock at the moment it applies an
intent to the `StageWriter`, not by this block. One consequence, noted
here on purpose: that timestamp now advances only on a state/detail
transition, not on every single utterance the way the old direct
`publish_status()` -> `set_status()` call did -- the alternative (putting
a clock value in the payload, or re-emitting unconditionally) would have
made the "only on change" rule pointless.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from blocks.status_line.line import pick
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent


class StatusLineBlock:
    id = "status-line"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: BlockContext | None = None
        self._queue_backlog = 0
        self._turn_running = False
        self._run_failed = False
        self._last_emitted: tuple | None = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "tick":
            return ()
        components = ev.payload.get("components", {})
        if "queue_backlog" in components:
            self._queue_backlog = components["queue_backlog"]
        if "turn_running" in components:
            self._turn_running = components["turn_running"]
        if "run_failed" in components:
            self._run_failed = components["run_failed"]

        states = ["listening"]
        if self._queue_backlog > 1:
            states.append("backlog")
        if self._turn_running:
            states.append("working")
        if self._run_failed:
            states.append("failed")
        state = pick(states)
        detail = {"count": self._queue_backlog, "failed": self._run_failed}

        current = (state, detail["count"], detail["failed"])
        if current != self._last_emitted:
            self._last_emitted = current
            self._ctx.emit(ViewIntent(
                id="status:line", block=self.id, kind="status",
                audience="room", payload={"state": state, "detail": detail}))
        return ()

    def stop(self) -> None:
        pass
