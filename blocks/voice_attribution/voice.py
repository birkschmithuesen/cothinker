#!/usr/bin/env python3
# CoThinker — blocks/voice_attribution/voice.py: the voice-attribution pure state machine
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`VoiceAttribution`, the pure state machine behind the `voice-attribution`
building block (issue #164). Pure, no I/O: no direct `time.time()` call --
every timestamp is an explicit argument, so tests stay deterministic.

Tracks who is talking right now, from the `speaker` label already carried
on every `utterance` CoreEvent (the same attribution `speaking-share` and
`speaker_label.py` already consume) -- independent of the speaker queue
(`blocks/speaker_queue/queue.py`'s `SpeakQueue`), which tracks who holds
the floor by tapping to join it, not who is actually talking.

Rules:
- A new speaker on an `utterance` becomes `speaking` right away; the
  speaker it replaces (if any) is pushed onto `recent`.
- `speaking` clears to `None` after `hold_s` seconds without an utterance
  from anyone (reported by a `tick`), so a short pause does not flicker
  the display; the clearing speaker is pushed onto `recent` too.
- `recent` keeps the last 5 distinct speakers, newest first; a name
  already in it moves to the front instead of duplicating.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

DEFAULT_HOLD_S = 2.0
MAX_RECENT = 5


class VoiceAttribution:
    def __init__(self, hold_s: float = DEFAULT_HOLD_S) -> None:
        self.hold_s = hold_s
        self.speaking: Optional[str] = None
        self.since: Optional[float] = None
        self.recent: List[Tuple[str, float]] = []
        self._last_activity: Optional[float] = None

    def on_utterance(self, speaker: Optional[str], at: float) -> bool:
        """One `utterance` from `speaker` at time `at`. Returns True if
        `speaking` changed because of it."""
        if not speaker:
            return False
        self._last_activity = at
        if speaker == self.speaking:
            return False
        if self.speaking is not None:
            self._push_recent(self.speaking, at)
        self.recent = [(n, t) for n, t in self.recent if n != speaker]
        self.speaking = speaker
        self.since = at
        return True

    def on_tick(self, now: float) -> bool:
        """`hold_s` seconds without activity clears `speaking`. Returns
        True if this call changed the state."""
        if self.speaking is None or self._last_activity is None:
            return False
        if now - self._last_activity < self.hold_s:
            return False
        self._push_recent(self.speaking, self._last_activity)
        self.speaking = None
        self.since = None
        return True

    def _push_recent(self, name: str, at: float) -> None:
        self.recent = [(n, t) for n, t in self.recent if n != name]
        self.recent.insert(0, (name, at))
        del self.recent[MAX_RECENT:]
