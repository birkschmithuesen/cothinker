# CoThinker — speaking-share block: per-person word share and room talk time
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The speaking-share building block (block contract, `shared/blocks.py`).

Feeds `utterance` events (speaker label + text) into `TalkShareTracker`
(`talk_share.py`) and a `block_output` event's own `holder` change (as
emitted by the speaker-queue block) into `RoomTalkTime`
(`room_talk_time.py`). On `tick` events it calls the tracker's own
throttled `maybe_emit` with the tick's own time field and, when it returns
an event, emits it as the `share:room` view intent (Surface API,
`shared/surface.py`).

A speaker label seen for the first time in an `utterance` event becomes
its own person id (no separate roster to resolve it against exists in
this repository yet); a missing speaker label falls back to the tracker's
own `ANONYMOUS` bucket, same as any other unmapped label.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from blocks.speaking_share.room_talk_time import RoomTalkTime
from blocks.speaking_share.talk_share import TalkShareTracker
from shared.blocks import BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

BLOCK_ID = "speaking-share"
API_VERSION = 1
DEFAULT_THROTTLE_S = 5.0


class SpeakingShareBlock:
    id = BLOCK_ID
    api_version = API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._tracker: Optional[TalkShareTracker] = None
        self._room_talk = RoomTalkTime()

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        throttle_s = config.get("throttle_s", DEFAULT_THROTTLE_S)
        self._tracker = TalkShareTracker({}, throttle_s=throttle_s)

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "utterance":
            self._on_utterance(ev)
        elif ev.kind == "block_output":
            self._on_block_output(ev)
        elif ev.kind == "tick":
            self._on_tick(ev)
        return ()

    def stop(self) -> None:
        pass

    def _on_utterance(self, ev: CoreEvent) -> None:
        speaker = ev.payload.get("speaker")
        text = ev.payload.get("text", "")
        if speaker is not None and speaker not in self._tracker.speaker_to_person:
            self._tracker.speaker_to_person[speaker] = speaker
        self._tracker.add_final(speaker, text)

    def _on_block_output(self, ev: CoreEvent) -> None:
        holder = ev.payload.get("holder")
        if not isinstance(holder, Mapping):
            return
        self._room_talk.on_change(holder.get("before"), holder.get("after"), holder.get("at"))

    def _on_tick(self, ev: CoreEvent) -> None:
        event = self._tracker.maybe_emit(ev.payload.get("now"))
        if event is None:
            return
        self._ctx.emit(ViewIntent(
            id="share:room",
            block=self.id,
            kind="share",
            audience="room",
            payload={"shares": event["shares"], "total_words": event["total_words"]},
        ))


def make_block() -> SpeakingShareBlock:
    return SpeakingShareBlock()
