#!/usr/bin/env python3
# CoThinker — blocks/live_transcript/transcript.py: the live-transcript pure state machine
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`LiveTranscript`, the pure state machine behind the `live-transcript`
building block (issue #181). Pure, no I/O: every timestamp is an explicit
argument, so tests stay deterministic.

Tracks two things:
- `recent`: the last `MAX_RECENT` FINISHED lines (oldest first), one per
  `utterance` CoreEvent (an STT final).
- the ONE utterance currently in progress: its tentative text, replaced on
  every `partial` CoreEvent for the same `turn_id` and cleared (moved into
  `recent`, text emptied) when the matching final arrives. Re-emission of
  the in-progress partial is throttled to at most one every
  `MIN_PARTIAL_EMIT_GAP_S` seconds per turn (<=4/s), per issue #181 --
  the FIRST partial of a new turn and the clearing final are never
  throttled away.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

MAX_RECENT = 10
MIN_PARTIAL_EMIT_GAP_S = 0.25  # <=4 emits/s per turn


class LiveTranscript:
    def __init__(self) -> None:
        self.recent: List[Tuple[str, float, str]] = []  # (speaker, at, text)
        self.partial_turn_id: Optional[str] = None
        self.partial_speaker: str = ""
        self.partial_text: str = ""
        self.partial_started_at: Optional[float] = None
        self._last_partial_emit: Dict[str, float] = {}

    def on_final(
        self, speaker: Optional[str], text: Optional[str],
        turn_id: Optional[str], at: float,
    ) -> Tuple[bool, Optional[dict]]:
        """One finished segment (an STT final). Returns
        `(recent_changed, cleared_partial)`: `cleared_partial` is
        `{"turn_id", "speaker", "started_at"}` when this final closed the
        turn whose partial was in progress, else `None`."""
        text = (text or "").strip()
        changed = False
        if text:
            self.recent.append((speaker or "", at, text))
            del self.recent[: -MAX_RECENT]
            changed = True
        cleared = None
        if turn_id is not None and turn_id == self.partial_turn_id:
            cleared = {
                "turn_id": self.partial_turn_id,
                "speaker": self.partial_speaker,
                "started_at": self.partial_started_at,
            }
            self.partial_turn_id = None
            self.partial_speaker = ""
            self.partial_text = ""
            self.partial_started_at = None
            self._last_partial_emit.pop(turn_id, None)
        return changed, cleared

    def on_partial(
        self, turn_id: Optional[str], speaker: Optional[str],
        text: Optional[str], at: float, now: float,
    ) -> bool:
        """One partial update. Returns True if the in-progress view should
        be re-emitted now (the caller reads the updated state back off
        `partial_turn_id`/`partial_speaker`/`partial_text`/
        `partial_started_at`); False if this update is throttled away."""
        if turn_id is None:
            return False
        is_new_turn = turn_id != self.partial_turn_id
        self.partial_turn_id = turn_id
        self.partial_speaker = speaker or ""
        self.partial_text = (text or "").strip()
        if is_new_turn:
            self.partial_started_at = at
        last = self._last_partial_emit.get(turn_id)
        if not is_new_turn and last is not None and now - last < MIN_PARTIAL_EMIT_GAP_S:
            return False
        self._last_partial_emit[turn_id] = now
        return True
