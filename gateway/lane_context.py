#!/usr/bin/env python3
# CoThinker — per-lane context window, stable -> volatile
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Builds each lane's prompt from stable to volatile, for cache hits.

konzept-spuren-architektur.md, §"Kontext pro Agent-Spur":
    1. profile + tools        -- the whole meeting, unchanged (lives in
                                  the Hermes profile itself, HERMES_HOME;
                                  not built here)
    2. project + agenda       -- fixed per meeting
    3. "Stand" summary        -- everything older than the window
    4. last X utterances      -- verbatim, append-only
    5. the new request

The window does NOT slide by one utterance per request -- it advances in
BLOCKS (`roll_every`), so most turns only ever APPEND to an unchanged
prefix (profile/project/stand/window-so-far), which is exactly what lets
prompt caching hit. Only every `roll_every`-th utterance actually changes
the prefix: the oldest `roll_every` utterances are folded into `stand`
and the window drops back near `window_size`.

A roll was originally meant to also be the moment a
lane starts a FRESH worker session (a new session id), with this class
only tracking WHEN that moment is (`add_utterance()`'s return value).
That path was rejected -- restarting a worker costs ~19s
(`gateway/lane_integration.py`'s point 3), and a roll can fire every
`roll_every` utterances, far too often for that cost. Instead
`gateway/worker.py` is told (`--no-history`) to never replay its
per-session store at all, so the window THIS class builds is the
lane agent's entire input regardless of whether a roll just happened.
`add_utterance()`'s boolean return is kept (it is independently tested,
see tests/test_lane_context.py) and the lane wiring now uses it only as
a diagnostic signal -- logging the event and, via `last_cache_read_ratio`
below, expecting a visibly lower ratio on that turn.

`summarize` is pluggable and defaults to a cheap, deterministic
placeholder (no model call): it keeps a truncated excerpt of each folded
utterance rather than a real condensation. That is the smaller,
reversible choice for today's build -- a caller can pass a real
summarizer (e.g. one more Jev-style call) without changing this class.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

WINDOW_SIZE = 40
ROLL_EVERY = 20
EXCERPT_CHARS = 80


@dataclass
class Utterance:
    speaker: str
    text: str


def _fallback_summarize(old_stand: str, folded: list) -> str:
    excerpts = "; ".join(
        f"{u.speaker}: {u.text[:EXCERPT_CHARS]}" for u in folded)
    addition = f"[{len(folded)} Aeusserungen] {excerpts}"
    return f"{old_stand} {addition}".strip() if old_stand else addition


@dataclass
class LaneContext:
    """One instance per lane per meeting. `project_agenda` is fixed text
    (parts 1-2 of the layout live outside this class, in the profile and
    in this fixed string)."""
    project_agenda: str
    window_size: int = WINDOW_SIZE
    roll_every: int = ROLL_EVERY
    summarize: Callable[[str, list], str] = field(
        default=_fallback_summarize, repr=False)
    stand: str = ""
    _utterances: list = field(default_factory=list, repr=False)
    _folded_upto: int = 0
    # "Report cache read ratio per lane" (the card's point, carried over
    # from konzept-spuren-architektur.md): a literal common-prefix
    # measurement against the PREVIOUS build_prompt() call, not a real
    # token-level cache-hit count -- gateway/worker.py's protocol
    # (turn/result/log/ready) never surfaces one from Hermes/the
    # provider, so this is a deliberate, documented stand-in. It still
    # answers the operational question ("is this lane's prefix actually
    # staying stable turn to turn, or did something just break it") --
    # a roll is expected to drop it sharply (the Stand text itself just
    # changed), an ordinary append-only turn is expected to keep it high.
    last_cache_read_ratio: float = 0.0
    _last_prompt: str = field(default="", repr=False)

    def add_utterance(self, speaker: str, text: str) -> bool:
        """Appends one utterance. Returns True if the window just rolled
        -- the caller logs this (gateway/lane_integration.py) rather than
        acting on it, see the module docstring above."""
        self._utterances.append(Utterance(speaker, text))
        overflow_end = len(self._utterances) - self.window_size
        if overflow_end - self._folded_upto >= self.roll_every:
            folded = self._utterances[self._folded_upto:overflow_end]
            self.stand = self.summarize(self.stand, folded)
            self._folded_upto = overflow_end
            return True
        return False

    def window(self) -> list:
        """The utterances currently shown verbatim -- `window_size` to
        `window_size + roll_every - 1` of them, since the window only
        shrinks back down at a roll."""
        return self._utterances[self._folded_upto:]

    def build_prompt(self, request: str) -> str:
        parts = [self.project_agenda]
        if self.stand:
            parts.append("## Stand\n" + self.stand)
        window = self.window()
        if window:
            parts.append("## Verlauf\n" + "\n".join(
                f"{u.speaker}: {u.text}" for u in window))
        parts.append("## Auftrag\n" + request)
        full = "\n\n".join(parts)
        previous = self._last_prompt
        self.last_cache_read_ratio = (
            len(os.path.commonprefix([previous, full])) / len(full)
            if previous and full else 0.0)
        self._last_prompt = full
        return full
