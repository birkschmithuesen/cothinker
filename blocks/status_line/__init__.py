# CoThinker — blocks/status_line/: the status-line block (issue #41, Wave
# B.4)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The status-line block (ADR 0008, ADR 0018), two users of the same
package (issue #67):

`mode = "meeting"` (default, Wave B.4): picks the winning operating state
(`listening`/`backlog`/`working`/`failed`) from `tick.components` and emits
the view intent `status:line` only when the picked state or its detail
actually changes.

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
`gateway.stage_surface.StageSurface` from its own clock at the moment it
applies an intent to the `StageWriter`, not by this block. One consequence, noted
here on purpose: that timestamp now advances only on a state/detail
transition, not on every single utterance the way the old direct
`publish_status()` -> `set_status()` call did -- the alternative (putting
a clock value in the payload, or re-emitting unconditionally) would have
made the "only on change" rule pointless.

`mode = "group"` (issue #67): the long format's group status (phase,
progress of the piece), for the room and the operator. It tracks `phase`
events and `block_output` facts (`piece_rev`, `interviews`, `findings`,
each sticky the same way) and emits `status:group` (audience `room`) and
`status:operator` (audience `operator`) only when the picked phase or
progress actually changes. The progress derivation itself
(`pick_progress()` and the two payload shapes) lives in
`blocks/status_line/group.py` -- internals specific to this second user,
kept out of the first user's file.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from blocks.status_line.group import group_payload, operator_payload
from blocks.status_line.line import pick
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent


class StatusLineBlock:
    id = "status-line"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: BlockContext | None = None
        self._mode = "meeting"
        self._queue_backlog = 0
        self._turn_running = False
        self._run_failed = False
        self._last_emitted: tuple | None = None
        self._phase: str | None = None
        self._piece_rev = None
        self._interviews = None
        self._findings = None
        self._last_emitted_group: tuple | None = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._mode = config.get("mode", "meeting")

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if self._mode == "group":
            return self._on_event_group(ev)
        return self._on_event_meeting(ev)

    def _on_event_group(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "phase":
            self._phase = ev.payload.get("id")
        elif ev.kind == "block_output":
            if "piece_rev" in ev.payload:
                self._piece_rev = ev.payload["piece_rev"]
            elif "interviews" in ev.payload:
                self._interviews = ev.payload["interviews"]
            elif "findings" in ev.payload:
                self._findings = ev.payload["findings"]
            else:
                return ()
        else:
            return ()

        current = (self._phase, repr(self._piece_rev), repr(self._interviews),
                   repr(self._findings))
        if current == self._last_emitted_group:
            return ()
        self._last_emitted_group = current
        self._ctx.emit(ViewIntent(
            id="status:group", block=self.id, kind="status", audience="room",
            payload=group_payload(self._phase, self._piece_rev,
                                   self._interviews, self._findings)))
        self._ctx.emit(ViewIntent(
            id="status:operator", block=self.id, kind="status",
            audience="operator",
            payload=operator_payload(self._phase, self._piece_rev,
                                      self._interviews, self._findings)))
        return ()

    def _on_event_meeting(self, ev: CoreEvent) -> Sequence[CoreEvent]:
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


def make_block() -> StatusLineBlock:
    return StatusLineBlock()
