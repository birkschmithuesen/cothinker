#!/usr/bin/env python3
# CoThinker — Assistant Q8: speaking time per person in the room-wide feedback round
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Speaking time per person for the room-wide feedback queue (Q8, feedback 265).

The phone's own-vs-average bars (`table-state.js feedbackTalkShareRows`) need two
numbers per seat: how long THIS person held the floor, and the room average. Source
of truth is the room speak queue itself (`workshop/speak_queue.py`): a person speaks
from the moment the queue makes them `speaking` until `done`, `takeover` or a
disconnect hands the floor on. No STT, no word count, no model -- holder time only,
so silence inside one's own turn counts as one's own time (that is the point of the
self-check: who takes up the room's airtime).

Keys are the room queue's own "<nn>:<person>" tokens, unique across tables.
Pure: no sockets, no files, no direct clock call -- the caller passes `now`
(seconds, monotonic) in, so tests are deterministic.

Average = total seconds of everyone / number of participants, where participants
are the seats present in the feedback round PLUS anyone who already spoke (a person
who spoke and then lost their connection still counts, a silent person present
counts with 0). With 40 people in the room the denominator is 40, not "the ones who
spoke" -- otherwise one long speaker would always look average.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional


class RoomTalkTime:
    def __init__(self) -> None:
        self._acc: Dict[str, float] = {}
        self._holder: Optional[str] = None
        self._since: Optional[float] = None

    # --- updates -----------------------------------------------------------------
    def on_change(self, before: Optional[str], after: Optional[str], now: float) -> None:
        """The room queue's speaker changed from `before` to `after` at `now`.
        Idempotent for `before == after` (a reconnect re-registering the speaker)."""
        if before == after and after == self._holder:
            return
        self._close(now)
        if after is not None:
            self._holder, self._since = after, now
            self._acc.setdefault(after, 0.0)

    def reset(self) -> None:
        self._acc.clear()
        self._holder = None
        self._since = None

    def _close(self, now: float) -> None:
        if self._holder is not None and self._since is not None:
            self._acc[self._holder] = self._acc.get(self._holder, 0.0) + max(0.0, now - self._since)
        self._holder, self._since = None, None

    # --- reads -------------------------------------------------------------------
    @property
    def holder(self) -> Optional[str]:
        return self._holder

    def seconds(self, key: str, now: float) -> float:
        s = self._acc.get(key, 0.0)
        if key == self._holder and self._since is not None:
            s += max(0.0, now - self._since)
        return s

    def spoken_keys(self) -> set:
        return {k for k, v in self._acc.items() if v > 0.0} | ({self._holder} if self._holder else set())

    def participants(self, present: Iterable[str]) -> set:
        return set(present) | self.spoken_keys()

    def average(self, present: Iterable[str], now: float) -> float:
        people = self.participants(present)
        if not people:
            return 0.0
        return sum(self.seconds(k, now) for k in people) / len(people)

    def payload_for(self, key: Optional[str], present: Iterable[str], now: float) -> dict:
        """The `feedback_talk_share` wire payload for one seat (`key` None = a phone
        whose person is not known yet: own 0)."""
        people = self.participants(present)
        avg = (sum(self.seconds(k, now) for k in people) / len(people)) if people else 0.0
        return {"own_seconds": round(self.seconds(key, now), 1) if key else 0.0,
                "average_seconds": round(avg, 1), "participants": len(people)}

    # --- persistence (run/redezeit.json) -----------------------------------------
    def to_json(self, now: float) -> dict:
        """Snapshot with the running turn folded in; the running turn itself is not
        persisted (the in-memory queue does not survive a restart either)."""
        out = dict(self._acc)
        if self._holder is not None:
            out[self._holder] = self.seconds(self._holder, now)
        return {"schema": 1, "seconds": {k: round(v, 3) for k, v in sorted(out.items())},
                "speaking": self._holder}

    @classmethod
    def from_json(cls, obj) -> "RoomTalkTime":
        rt = cls()
        if isinstance(obj, dict) and isinstance(obj.get("seconds"), dict):
            for k, v in obj["seconds"].items():
                if isinstance(k, str) and isinstance(v, (int, float)) and v >= 0:
                    rt._acc[k] = float(v)
        return rt
