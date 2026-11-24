# CoThinker — blocks/voice_attribution: who is talking right now, by voice/track attribution
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `voice-attribution` (issue #164): tells the stage who is
talking right now, from the `speaker` label every `utterance` CoreEvent
already carries -- the same attribution `blocks/speaking_share/` and
`speaker_label.py` already consume. This is separate from the speaker
queue (`blocks/speaker_queue/`): the queue tracks who holds the floor by
tapping to join it, this block tracks who is actually talking, whether or
not anyone uses the queue at all.

Consumes `utterance` events (`gateway/block_host_wiring.py::feed_utterance`,
payload keys `text`, `speaker`, `turn_id`, `t_start`, `t_end`, `at`) and
`tick` events (payload `{"now": ...}`, dispatched periodically by the
session host) to detect ~2 s of silence, so a short pause does not flicker
the display.

Emits the view intent `voice:room` (`kind` `voice`, audience `room`) only
when the payload actually changed, rate-limited to a minimum
`MIN_EMIT_GAP_S` between consecutive emits (max 2 events/s) -- a `tick`
alone, with nothing new to show, never re-emits an unchanged payload.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional, Sequence

from shared.blocks import BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

from .voice import DEFAULT_HOLD_S, VoiceAttribution

BLOCK_ID = "voice-attribution"
API_VERSION = 1
MIN_EMIT_GAP_S = 0.5  # max 2 view-intent emits per second


def _wall_clock_label(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")


class VoiceAttributionBlock:
    id = BLOCK_ID
    api_version = API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._voice = VoiceAttribution()
        self._last_emit_at: Optional[float] = None
        self._last_payload: dict = self._payload()

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._voice = VoiceAttribution(hold_s=config.get("hold_s", DEFAULT_HOLD_S))
        self._last_emit_at = None
        self._last_payload = self._payload()

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "utterance":
            self._on_utterance(ev)
        elif ev.kind == "tick":
            self._on_tick(ev)
        return ()

    def stop(self) -> None:
        pass

    def _on_utterance(self, ev: CoreEvent) -> None:
        at = ev.payload.get("at")
        if at is None:
            at = self._ctx.clock()
        self._voice.on_utterance(ev.payload.get("speaker"), at)
        self._maybe_emit(at)

    def _on_tick(self, ev: CoreEvent) -> None:
        now = ev.payload.get("now")
        if now is None:
            now = self._ctx.clock()
        self._voice.on_tick(now)
        self._maybe_emit(now)

    def _payload(self) -> dict:
        return {
            "speaking": self._voice.speaking,
            "since": _wall_clock_label(self._voice.since) if self._voice.since is not None else None,
            "recent": [{"name": name, "last_at": _wall_clock_label(at)} for name, at in self._voice.recent],
        }

    def _maybe_emit(self, now: float) -> None:
        payload = self._payload()
        if payload == self._last_payload:
            return
        if self._last_emit_at is not None and now - self._last_emit_at < MIN_EMIT_GAP_S:
            return
        self._last_emit_at = now
        self._last_payload = payload
        self._ctx.emit(ViewIntent(
            id="voice:room", block=self.id, kind="voice", audience="room",
            payload=payload,
        ))
