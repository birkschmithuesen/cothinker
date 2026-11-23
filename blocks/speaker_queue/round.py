# CoThinker — blocks/speaker_queue/round.py: round-robin speaker list (mode "round")
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The speaker-queue block's round mode: a fixed list of people that takes
turns in order, in contrast to the tap mode's first-come queue.

Pure data, no clock and no I/O; the block persists `to_state()` itself.
"""
from __future__ import annotations

from typing import Optional, Sequence


class RoundRobin:
    def __init__(self, people: Sequence[str], index: int = 0) -> None:
        self.people = [str(p) for p in people]
        self.index = index % len(self.people) if self.people else 0

    @property
    def speaker(self) -> Optional[str]:
        return self.people[self.index] if self.people else None

    @property
    def next_speaker(self) -> Optional[str]:
        if len(self.people) < 2:
            return None
        return self.people[(self.index + 1) % len(self.people)]

    def advance(self) -> bool:
        """Moves to the next person; True when that wrapped past the last
        one back to the first. A no-op (False) without people."""
        if not self.people:
            return False
        self.index += 1
        if self.index >= len(self.people):
            self.index = 0
            return True
        return False

    def view(self) -> dict:
        return {"people": list(self.people), "speaker": self.speaker,
                "next_speaker": self.next_speaker}

    def to_state(self) -> dict:
        return {"people": list(self.people), "index": self.index}

    @classmethod
    def from_state(cls, data: dict) -> Optional["RoundRobin"]:
        people = data.get("people")
        if not isinstance(people, list) or not people:
            return None
        index = data.get("index")
        return cls(people, index if isinstance(index, int) else 0)
