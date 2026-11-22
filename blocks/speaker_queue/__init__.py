# CoThinker — blocks/speaker_queue: participants tap to request and leave the floor
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `speaker-queue` (ADR 0010, issue #30): participants tap to
request the floor and tap when done; the block keeps the order and tells
the stage who speaks and who waits.

Consumes `action` events (a `UserAction` the host turned into a `CoreEvent`,
its fields carried flat in the payload): `participant:name` (`kind` `edit`,
`payload` `{"name": ...}`) records a display name for `person`; `queue:room`
(`kind` `tap`, `payload` `{"op": "request" | "done"}`) is a floor request or
hand-off by `person`.

Emits the view intent `queue:room` (`kind` `queue`, audience `room`) on
every `action` it consumes, and a `block_output` `{"holder": {"before",
"after", "at"}}` only when who speaks actually changes.

A tap from a participant id this block never saw a `participant:name` edit
for still works: it gets a placeholder display name instead of blocking the
floor request on a missing name.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from shared.modules import CoreEvent
from shared.surface import ViewIntent

from .queue import SpeakQueue

BLOCK_ID = "speaker-queue"
API_VERSION = 1


class SpeakerQueueBlock:
    id = BLOCK_ID
    api_version = API_VERSION

    def __init__(self) -> None:
        self._ctx = None
        self._queue = SpeakQueue()
        self._names: dict[str, str] = {}

    def start(self, ctx, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._queue = SpeakQueue(clock=ctx.clock)

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "action":
            return ()
        intent_id = ev.payload.get("intent_id")
        kind = ev.payload.get("kind")
        person = ev.payload.get("person")
        inner = ev.payload.get("payload") or {}
        if intent_id == "participant:name" and kind == "edit":
            self._names[person] = inner.get("name", "")
            self._emit_view()
            return ()
        if intent_id == "queue:room" and kind == "tap":
            return self._on_tap(person, inner.get("op"))
        return ()

    def stop(self) -> None:
        pass

    def _on_tap(self, person: str, op: Optional[str]) -> Sequence[CoreEvent]:
        before = self._queue.speaking
        if op == "request":
            self._queue.request(person)
        elif op == "done":
            self._queue.done(person)
        else:
            return ()
        after = self._queue.speaking
        self._emit_view()
        if before == after:
            return ()
        return (CoreEvent("block_output", {"holder": {
            "before": before, "after": after, "at": self._ctx.clock(),
        }}),)

    def _name_for(self, pid: str) -> str:
        return self._names.get(pid) or f"Guest {pid}"

    def _seat(self, pid: Optional[str]) -> Optional[dict]:
        if pid is None:
            return None
        return {"pid": pid, "name": self._name_for(pid)}

    def _emit_view(self) -> None:
        self._ctx.emit(ViewIntent(
            id="queue:room", block=self.id, kind="queue", audience="room",
            payload={
                "mode": "tap",
                "speaking": self._seat(self._queue.speaking),
                "waiting": [self._seat(pid) for pid in self._queue.queue],
                "round": None,
            },
        ))
