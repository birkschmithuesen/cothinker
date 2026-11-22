#!/usr/bin/env python3
# CoThinker - the structural event trace
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""What the stage did, as structure -- never as speech.

The analysis of the real meeting reached its findings by measuring, not
by asking: 36 compactions, median one minute, and the single 16-minute
outlier coinciding exactly with the lost block. What had to be
reconstructed from commit timestamps back then is written down on
purpose here. The five quantities it has to make computable are listed
in docs/stage-methodology.md section 7; no thresholds are set, the file
only records observations.

Type, identity, timestamps, duration, outcome. NO conversation text --
that keeps the file compatible with the leak guard, comparable across
meetings, and free of confidential speech. A call-out quote is hashed so
fulfilment can be counted without storing what was said.

WHAT MAY BE WRITTEN -- the promise of this module, enforced here and
never at the call sites:

  * the NUMERIC fields (ages, durations, counts, sizes, flags) take a
    finite number or a boolean, nothing else;
  * the event name and every TOKEN field (`id`, `kind`, `state`,
    `reason`, `quote_hash`, `outcome`) take a token: 1 to 32 characters out of
    ASCII letters, digits, `_` and `-`. No spaces, no punctuation, no
    non-ASCII -- a spoken sentence cannot be one.

Anything else is DROPPED, never coerced. An allow-list of keys alone was
not enough: `json.dumps(..., default=str)` turned whatever object a
caller handed to `reason=` into its text, so a call site one character
away from a debug log could have written speech into the file. An event
whose NAME is not a token is not written at all. A call site that wants
a value in the trace has to shape it into a token first.

THREADS. All four threads of the consumer record into the same file.
There is no lock here on purpose -- this is a leaf: one short line is
written with a single `write` into a file opened for appending, which the
platform serialises against the other appenders, and the consumer's two
locks (`StageWriter._lock`, then `StageState._lock`) keep their
documented order because nothing here ever takes one.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Allow-lists of keys AND of value shapes. A key alone guards nothing:
# `reason` used to accept any object and stringify it.
NUMERIC_FIELDS = frozenset({
    "age_s", "latency_s", "duration_s", "count", "chars", "collapsed_lines",
    "ok", "discarded", "changed",
    # The level-1 decider (gateway/decider.py): which question set ran.
    "questions_version",
    # Level 2 (gateway/policy_runner.py): whether an action fired without
    # a question, and how many blocks the ask lifecycle waited through.
    "autonomous", "blocks",
    # The wiki layer (blocks/knowledge_link/wiki.py): how many wikilinks a new topic
    # block got -- the count, never the note names.
    "links",
})
# `outcome` is how an announcement ended (`arrived`, `discarded`,
# `displaced`, `unanswered`). It takes a token like every other key here --
# a new key under the same shape rule, not a looser rule.
TOKEN_FIELDS = frozenset({"id", "kind", "state", "reason", "quote_hash",
                          "outcome",
                          # gateway/decider.py: the 16-hex question-set
                          # digest and the backend name ("fake", "jev").
                          "questions_hash", "backend",
                          # gateway/policy_runner.py: which level-1 signal
                          # fired the action, and which meeting mode was
                          # set at the time.
                          "signal", "mode"})

# A token: short, ASCII, no spaces and no punctuation. Every id ("N1"),
# every notice kind ("value_conflict"), every state ("resolved"), every
# rejection reason ("no_text") and the 16-hex quote hash fit; a sentence
# from the meeting does not, and neither does a truncated one.
TOKEN_RE = re.compile(r"\A[A-Za-z0-9_-]{1,32}\Z")


def is_token(value: object) -> bool:
    """True for a value that may be written as text. See the docstring."""
    return isinstance(value, str) and TOKEN_RE.match(value) is not None


def is_number(value: object) -> bool:
    """True for a finite number or a boolean.

    `float('nan')` is excluded on purpose: `json.dumps` writes it as
    `NaN`, which no JSON reader of this file would accept -- one such
    line would cost the whole evening's measurement.
    """
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


class EventLog:
    """Append-only structural trace, one JSON object per line."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def hash_quote(quote: str) -> str:
        return hashlib.sha256(quote.strip().encode("utf-8")).hexdigest()[:16]

    def record(self, event: str, **fields) -> None:
        """Write one event. Never raises -- a broken trace must never cost
        a meeting.

        Not only the write is guarded: a caller can hand in a value that
        `json.dumps` refuses, and a TypeError out of the ticker would cost
        the round its clocks for the rest of the evening.

        A field whose key is unknown, or whose VALUE does not have the
        shape that key promises, is dropped. Nothing is stringified: a
        dropped measurement is a gap in the statistics, a stringified
        object can be a sentence somebody said.
        """
        try:
            if not is_token(event):
                return                    # a name we cannot vouch for
            # WITH the UTC offset, like every other timestamp this project
            # writes: the trace is read after the meeting, next to a
            # protocol that carries one, and a naive stamp cannot be
            # compared across two machines.
            row = {"at": datetime.now().astimezone().isoformat(
                timespec="seconds"), "event": event}
            quote = fields.pop("quote", None)
            if isinstance(quote, str):
                # The one value allowed to be speech -- and it never
                # arrives anywhere: only its digest is kept.
                fields["quote_hash"] = self.hash_quote(quote)
            for key, value in fields.items():
                if key in NUMERIC_FIELDS and is_number(value):
                    row[key] = value
                elif key in TOKEN_FIELDS and is_token(value):
                    row[key] = value
            # No `default=`: after the filter every value is a token or a
            # number, so nothing is left for it to stringify.
            line = json.dumps(row, ensure_ascii=False) + "\n"
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:                 # noqa: BLE001 -- see the docstring
            pass

    def record_all(self, events: Iterable[dict]) -> None:
        """Write what `StageState.tick()` reported.

        It already returns this shape -- name under `event`, the rest as
        fields -- so the state stays the one source of truth for what
        changed, and this side only writes it down.
        """
        try:
            for event in events or ():
                if not isinstance(event, dict):
                    continue
                fields = dict(event)
                # Not stringified either: `record()` writes nothing for a
                # name that is not a token.
                name = fields.pop("event", "")
                if name:
                    self.record(name, **fields)
        except Exception:                 # noqa: BLE001
            pass
