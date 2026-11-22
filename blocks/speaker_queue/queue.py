#!/usr/bin/env python3
# CoThinker — blocks/speaker_queue/queue.py: the speak queue's pure state machine
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`SpeakQueue`, the pure state machine behind the `speaker-queue` building
block (ADR 0010). Pure, no I/O: no sockets, no files, no direct
`time.time()`/`time.monotonic()` call. Whoever builds a `SpeakQueue` passes
the clock in, so tests stay deterministic with a fake one.

Rules:
- A seat pressing "Speak" while nobody speaks becomes the speaker right away.
- A seat pressing "Speak" while someone else speaks joins the end of the
  queue instead.
- The seat at the head of the queue can "take over": the current speaker's
  contribution ends and the seat taking over speaks immediately.
- A speaker's contribution only ends by `done` (their own) or `takeover`
  (the next in line) -- NEVER automatically on silence or by the clock.
  That decision is deliberately left to the people in the room.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional


class SpeakQueue:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.speaking: Optional[str] = None
        self.speaking_since: Optional[float] = None
        self.queue: List[str] = []

    def request(self, seat: str) -> None:
        if seat == self.speaking or seat in self.queue:
            return
        if self.speaking is None:
            self._set_speaking(seat)
        else:
            self.queue.append(seat)

    def done(self, seat: str) -> None:
        if seat != self.speaking:
            return
        self._advance()

    def disconnect(self, seat: str) -> None:
        """A seat's socket dropped out. If it was speaking, hand off exactly
        like `done()`; if it was only waiting, drop it from the queue."""
        if seat == self.speaking:
            self._advance()
        elif seat in self.queue:
            self.queue.remove(seat)

    def takeover(self, seat: str) -> None:
        if not self.queue or self.queue[0] != seat:
            return
        self.queue.pop(0)
        self._set_speaking(seat)

    def position(self, seat: str) -> Optional[int]:
        """1-based position in the queue, or None if `seat` isn't waiting."""
        if seat not in self.queue:
            return None
        return self.queue.index(seat) + 1

    def event(self) -> dict:
        """The wire payload: `{speaking: seat|null, queue: [seat, ...]}`."""
        return {"speaking": self.speaking, "queue": list(self.queue)}

    def _advance(self) -> None:
        if self.queue:
            self._set_speaking(self.queue.pop(0))
        else:
            self.speaking = None
            self.speaking_since = None

    def _set_speaking(self, seat: str) -> None:
        self.speaking = seat
        self.speaking_since = self._clock()
