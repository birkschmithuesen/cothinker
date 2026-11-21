#!/usr/bin/env python3
# CoThinker — Assistant K2: per-role share of spoken words
# Copyright (C) 2026 Birk Schmithuesen

# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A gentle balance hint for the timer phone (K7 card shows
it -- this module does not touch the hub, the UI, views.js or table.js).

Counts EVERY word every person says, including an asker's own question
words -- deliberately NOT scoped to who is currently asking or to any
"interview phase" filter. This differs on purpose from
`workshop.bullets.BulletsHook.on_stt`, which skips the round's asker
because that hook extracts the interviewee's *content*; a talk-share
balance hint is about who is taking up airtime, asker included. `window`
in the emitted event is a fixed label for the consumer (currently always
"interview" -- there is only one window so far), not a filter on which
finals count.

No model calls, no names reaching a model: a "person" here is a
placeholder (`workshop.persons.PERSONS`, "P1".."Pn"), never a real name.

`TalkShareTracker.add_final` is the incremental step -- O(the words in
that one new final), never a re-scan of earlier finals. `event()` /
`build_event()` are pure functions from the current per-person word
counts to the C2 `talk_share` event (binding contract below).

    {"type": "talk_share",
     "shares": [{"person": "P1", "role_id": "...", "words": 123, "share": 0.31}],
     "total_words": 400, "window": "interview"}

- A known person (one the speaker-label -> person mapping points at) who
  has not spoken yet still appears in `shares`, as `words: 0, share: 0.0`,
  as long as `total_words > 0`.
- An unmapped speaker label is counted under its own `Anonymous` row.
- `share` values sum to 1.0 when `total_words > 0`; `shares` is `[]` when
  `total_words == 0` (no row ever divides by zero).
- A row carries an optional `given` field (the talk-share
  panel's "<given> · <role>" display) only when `given_of` maps that
  person -- never `given: None`.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

ANONYMOUS = "Anonymous"
DEFAULT_WINDOW = "interview"
DEFAULT_THROTTLE_S = 3.0  # the operator 2026-10-30 20:40: bars moved too slowly at 10 s
FIRST_WORDS_MIN_S = 2.0   # a person's first words: at most this long after the previous event


def _word_count(text: str) -> int:
    return len((text or "").split())


def build_event(words: Dict[str, int], persons: Iterable[str] = (),
                role_of: Optional[Dict[str, str]] = None, given_of: Optional[Dict[str, str]] = None,
                *, window: str = DEFAULT_WINDOW) -> dict:
    """Pure: a per-person word-count snapshot -> the `talk_share` event.

    `persons` is the known roster (e.g. the speaker-label -> person
    mapping's values) so a person who has not spoken yet still gets a
    `words: 0, share: 0.0` row once anyone has spoken at all.

    `given_of`: person -> the bound role's given name,
    for the talk-share panel's display only. Optional and additive -- a
    person missing from it gets no `given` key at all (never `given:
    None`), so existing rows and existing callers are unaffected. Never
    the source of `role_id`: that still comes from `role_of` alone, the
    same plain role short name hub.py's matching/ring logic reads from
    _names/personen.json.
    """
    role_of = role_of or {}
    given_of = given_of or {}
    total = sum(words.values())
    shares = []
    if total:
        roster = set(persons) | set(words)
        for person in sorted(roster):
            w = words.get(person, 0)
            row = {"person": person, "role_id": role_of.get(person), "words": w,
                   "share": w / total}
            given = given_of.get(person)
            if given:
                row["given"] = given
            shares.append(row)
    return {"type": "talk_share", "shares": shares, "total_words": total, "window": window}


class TalkShareTracker:
    """Incremental per-table state.

    `speaker_to_person`: the hub's own speaker-label -> person mapping
    (`workshop.persons.PERSONS`-style placeholders); a label absent from
    it is counted under `ANONYMOUS`. `role_of`: person -> role_id, same
    shape as `workshop.prologue_input._read_role_of`'s `rollen.json`.
    """

    def __init__(self, speaker_to_person: Dict[str, Optional[str]], *,
                 role_of: Optional[Dict[str, str]] = None,
                 given_of: Optional[Dict[str, str]] = None,
                 throttle_s: float = DEFAULT_THROTTLE_S, window: str = DEFAULT_WINDOW) -> None:
        self.speaker_to_person = dict(speaker_to_person)
        self.role_of = dict(role_of or {})
        self.given_of = dict(given_of or {})
        self.throttle_s = throttle_s
        self.window = window
        self._words: Dict[str, int] = {}
        self._last_emit: Optional[float] = None
        self._announced: Optional[set] = None   # persons already shown in an emitted event

    def add_final(self, speaker_label: Optional[str], text: str) -> None:
        """One new final utterance -- O(its own word count), never a
        re-scan of earlier finals."""
        n = _word_count(text)
        if n == 0:
            return
        person = self.speaker_to_person.get(speaker_label, ANONYMOUS) if speaker_label is not None else ANONYMOUS
        self._words[person] = self._words.get(person, 0) + n

    def event(self) -> dict:
        return build_event(self._words, self.speaker_to_person.values(), self.role_of, self.given_of,
                           window=self.window)

    def should_emit(self, now: float) -> bool:
        # Live finding 31.10. Tisch 01: a person's first words sat unseen for up to a
        # full throttle_s window because an empty tick (nothing new to show) still used
        # up the slot. An empty snapshot never consumes it, and first words go out
        # after FIRST_WORDS_MIN_S at the latest instead of waiting for the full throttle.
        if not self._words:
            return False
        if self._last_emit is None:
            return True
        if self._announced is None or set(self._words) - self._announced:
            return (now - self._last_emit) >= FIRST_WORDS_MIN_S
        return (now - self._last_emit) >= self.throttle_s

    def maybe_emit(self, now: float) -> Optional[dict]:
        """`None` before `throttle_s` has elapsed since the last emission;
        otherwise the current `event()`, and records `now` as the new
        last-emit time. `now` is caller-supplied -- no sleep, no real
        clock dependency baked in."""
        if not self.should_emit(now):
            return None
        self._last_emit = now
        self._announced = set(self._words)
        return self.event()
