#!/usr/bin/env python3
# CoThinker — transcript stage: speaker label from who held the mic
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Labels every recognised utterance with whoever held the microphone when
its audio started -- the predecessor's one-day application's last-state
rule (`docs/architecture/target.md` §3, ADR 0004): the turn holder during
an interview or introduction turn, the speaker-queue holder during
discussion and feedback, nobody otherwise. No voice enrollment, no
diarization-label matching -- `MicHolderStage` never decides who holds the
microphone, its caller does, through `set_holder()`.

Looked up at the row's own audio start (`audio_epoch + t_start`), not at
the time its text arrives: a hand-over may already have happened by then.
"""
from __future__ import annotations

import time
from bisect import bisect_right
from typing import Iterable

from shared.stt import TranscriptEvent

_TRANSCRIPT_TYPES = ("partial", "final")


class MicHolderStage:
    """`shared.stt.TranscriptStage`: `speaker` becomes the mic holder at the
    event's own audio start, the engine's diarization label moves to
    `extra["diarization_label"]`."""

    name = "speaker-labels"

    def __init__(self) -> None:
        self._times: list[float] = []
        self._holders: list[str] = []

    def set_holder(self, person: str, at: float | None = None) -> None:
        """Records a hand-over. A no-op if `person` already holds the mic."""
        if self._holders and self._holders[-1] == person:
            return
        self._times.append(time.time() if at is None else at)
        self._holders.append(person)

    def holder_at(self, t: float | None = None) -> str | None:
        """The holder at audio time `t`. No time, or a time before the
        first recorded change, gives the current (most recent) holder. A
        time exactly at a change belongs to the new holder."""
        if not self._holders:
            return None
        if t is None:
            return self._holders[-1]
        i = bisect_right(self._times, t)
        if i == 0:
            return self._holders[-1]
        return self._holders[i - 1]

    def feed(self, ev: TranscriptEvent) -> Iterable[TranscriptEvent]:
        if ev.type not in _TRANSCRIPT_TYPES:
            return (ev,)
        t = (ev.audio_epoch + ev.t_start
            if ev.audio_epoch is not None and ev.t_start is not None else None)
        extra = dict(ev.extra)
        extra["diarization_label"] = ev.speaker
        return (ev.replace(speaker=self.holder_at(t), extra=extra),)
