# CoThinker — blocks/live_transcript: the stage's live transcript side panel feed
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `live-transcript` (issue #181): feeds the stage's live
transcript side panel -- the last finished lines plus the one utterance
currently in progress, building up live from STT partials.

Consumes `utterance` events (`gateway/block_host_wiring.py::feed_utterance`,
the same per-line attribution the transcript already uses, payload keys
`text`, `speaker`, `turn_id`, `at`) for finished lines, and the new `partial`
host event (dispatched by `gateway/consumer.py` per STT partial event,
payload keys `turn_id`, `speaker`, `text`, `at`) for the tentative tail of
the utterance in progress.

Emits two view intents:
- `transcript:recent` (kind `transcript`): the last 10 finished lines,
  oldest first, `{speaker, started_at, text}` each -- the same shape as
  `StageStateSnapshot.transcript_recent`.
- `transcript:partial` (kind `transcript_partial`): the utterance in
  progress, `{turn_id, speaker, text, started_at}`; cleared (empty `text`)
  the moment its final arrives.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional, Sequence

from shared.blocks import BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

from .transcript import LiveTranscript

BLOCK_ID = "live-transcript"
API_VERSION = 1


def _wall_clock_label(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")


class LiveTranscriptBlock:
    id = BLOCK_ID
    api_version = API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._state = LiveTranscript()

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._state = LiveTranscript()

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "utterance":
            self._on_utterance(ev)
        elif ev.kind == "partial":
            self._on_partial(ev)
        return ()

    def stop(self) -> None:
        pass

    def _on_utterance(self, ev: CoreEvent) -> None:
        at = ev.payload.get("at")
        if at is None:
            at = self._ctx.clock()
        changed, cleared = self._state.on_final(
            ev.payload.get("speaker"), ev.payload.get("text"),
            ev.payload.get("turn_id"), at,
        )
        if changed:
            self._emit_recent()
        if cleared is not None:
            self._emit_partial(
                cleared["turn_id"], cleared["speaker"], "", cleared["started_at"])

    def _on_partial(self, ev: CoreEvent) -> None:
        at = ev.payload.get("at")
        if at is None:
            at = self._ctx.clock()
        now = self._ctx.clock()
        if self._state.on_partial(
            ev.payload.get("turn_id"), ev.payload.get("speaker"),
            ev.payload.get("text"), at, now,
        ):
            self._emit_partial(
                self._state.partial_turn_id, self._state.partial_speaker,
                self._state.partial_text, self._state.partial_started_at)

    def _recent_payload(self) -> list:
        return [
            {"speaker": speaker, "started_at": _wall_clock_label(at), "text": text}
            for speaker, at, text in self._state.recent
        ]

    def _emit_recent(self) -> None:
        self._ctx.emit(ViewIntent(
            id="transcript:recent", block=self.id, kind="transcript",
            audience="room", payload=self._recent_payload(),
        ))

    def _emit_partial(self, turn_id: Optional[str], speaker: str, text: str,
                      started_at: Optional[float]) -> None:
        self._ctx.emit(ViewIntent(
            id="transcript:partial", block=self.id, kind="transcript_partial",
            audience="room",
            payload={
                "turn_id": turn_id or "",
                "speaker": speaker,
                "text": text,
                "started_at": _wall_clock_label(started_at) if started_at is not None else "",
            },
        ))
