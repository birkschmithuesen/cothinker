#!/usr/bin/env python3
# CoThinker — per-utterance timing trace
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Timing of the raw STT stream, one line per utterance.

Deliberately separate from `shared/events.py` / `EventLog`: that module's
allow-list and hashing exist to keep conversation TEXT out of the
structural trace. This file never sees any text either -- it only ever
writes a character COUNT, never the characters themselves -- but its
purpose is different (latency of the raw stream, not the five
quantities of docs/stage-methodology.md section 7), so it stays its own
small class rather than inheriting or importing from `EventLog`.

Line shape, four fields -- plus two more, and only when the STT server
sent a usable audio-clock span for that utterance:

    {"at": "2026-09-20T10:04:20.317+02:00", "recognizer_id": "0",
     "chars": 87, "cut": null}
    {"at": "2026-09-20T10:04:20.317+02:00", "recognizer_id": "0",
     "chars": 87, "cut": null, "t_start": 1.013, "t_end": 2.487}

  * `t_start` / `t_end` -- seconds on the STT server's audio clock (a
    sample counter over the audio that really passed through it, so a
    position here is also a sample position in its debug WAV), rounded
    to milliseconds. Present only when BOTH are valid; `valid_span()`
    below is the rule and `gateway/consumer.py` uses the same function,
    so there is one definition of "usable" and not two. A server older
    than 2026-09-24 sends neither, and that stays the normal case. This
    is what earlier pause statistics were missing: without it,
    `timing.jsonl` measures when text ARRIVED, not when it was said.
  * `at` -- local time with UTC offset, MILLISECOND resolution (unlike
    `EventLog`, which stays at whole seconds on purpose). Captured by the
    CALLER at the point the utterance is handed to the block builder --
    not by this class at write time. For an utterance whose processing
    cuts a block (wake/length), `BlockBuilder.sentence()`'s synchronous
    call chain runs `_emit()` -> `output(b)` -> `block_done(b)` ->
    `publish_status()` -> a real, possibly disk-writing `writer.write()`
    before control returns -- capturing "now" only after that chain
    completed would measurably delay `at` for exactly the utterances
    whose timing is most interesting. So the timestamp is passed in as
    an explicit `datetime`, not computed here.
  * `recognizer_id` -- the raw value the STT server sent for this
    utterance, as a string, or `null` if it sent none. This is NOT the
    same value the rest of the consumer uses for speaker attribution
    (that one folds `"default"`/`"mic_gate"` to `None` for a different
    purpose) -- here the raw channel id passes through unchanged.
  * `chars` -- `len(text)` of the utterance, already stripped. Never the
    text itself.
  * `cut` -- `null`, `"pause"`, `"length"` or `"wake"`: whether this
    utterance's processing closed a block, and why. See
    `record_pause_marker` for the one case that is not a real utterance.

    ACCEPTED LIMITATION -- `length` is under-counted, often to zero:
    `BlockBuilder` can cut a block for `length` from two places
    (`gateway/segmenter.py`), and only ONE of them is ever attributed here.
    `sentence()` CAN cut it synchronously, inline with the utterance
    that triggered it -- and when it does, that case IS captured (see
    `tests/test_timing.py`'s synchronous-length-cut test for a
    deterministic case that pins exactly this). `tick()` (the
    consumer's 0.5s-interval thread, `gateway/consumer.py`) cuts it
    independently, whenever a block has been open too long with no new
    sentence arriving -- and that cut is invisible to this trace: the
    block's last sentence keeps `cut: null` forever. In real operation
    `tick()` is the common path, not the rare one, because a block
    usually sits open past `max_s` for a moment before the next
    utterance arrives. Measured (reviewer, 2026-09-21, real consumer
    against a fake STT, counted against `stage-events.jsonl` as ground
    truth): at a 2.0s utterance spacing, 0 of 4 `length`-cuts were
    captured as `cut`; at 1.0s spacing the count was nondeterministic
    across identical replays -- observed once as 2/3/3 of 3, a race
    between the two threads whose split can range anywhere down to 0
    depending on machine load. Do not read a low or zero `length` count
    in `timing.jsonl` as evidence that `max_block` rarely triggers --
    check `stage-events.jsonl` instead.

    This is a DELIBERATE, ACCEPTED scope decision, not a bug to fix
    here: the SDD plan that introduced this trace explicitly carved the
    `tick()` case out of scope and said so in writing ("tick() CAN emit
    'length' on its own [...] that case is explicitly OUT OF SCOPE for
    this card [...] Document this as an accepted limitation, not a
    bug."). That plan document lived only in a local, gitignored
    `.superpowers/` working directory and was never committed to this
    repository, so it is not a reachable reference for anyone who
    clones the repo -- this paragraph, not a pointer to that file, is
    the record of the decision.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path


def valid_span(t_start, t_end) -> bool:
    """Are these two values a usable audio-clock span?

    Deliberately strict, and shared with `gateway/consumer.py`: both
    numbers come off the wire from another process on another machine.

      * `True` is an `int` in Python and would otherwise sail through as
        a zero-length utterance at position 1.0.
      * `NaN` compares false against everything, so it survives every
        naive range check and then poisons whatever is computed from the
        file later.
      * A negative position cannot exist on a clock that starts at zero.
      * `t_end < t_start` is not a span.
      * An `int` with no `float` equivalent (`json.loads` of a huge
        integer literal off the wire can produce one) must not raise --
        `math.isfinite()` itself raises `OverflowError` converting it,
        and this function must never do that to its caller.

    Anything that fails is treated as ABSENT, never as an error: an STT
    server older than 2026-09-24 sends neither field, and that case has
    to stay completely normal (AGENTS.md: the wire contract is additive
    only). Both values are written or neither is -- half a span is worse
    than none, because it looks like a measurement.
    """
    for value in (t_start, t_end):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            if not math.isfinite(value) or value < 0:
                return False
        except (OverflowError, ValueError):
            # An int too large (or otherwise unable) to convert to a
            # float -- not finite in any useful sense, so: absent. (Both
            # values already passed the finite check above by the time
            # this comparison runs, so it cannot raise the same way.)
            return False
    return t_end >= t_start


class TimingLog:
    """Append-only per-utterance timing trace, one JSON object per line.

    Unlike `EventLog`, this class does not swallow write failures itself
    -- `record_utterance`/`record_pause_marker` may raise (`OSError`,
    `UnicodeError`, ...). The call site in `gateway/consumer.py` is
    responsible for catching and logging, the same way it already
    handles `Protocol.append_raw` and `bb.sentence`. That keeps the
    OBSERVABLE contract in one place: exactly one `log(...)` call on
    failure, and the SSE loop keeps running regardless.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        # The recognizer_id written on the most recently logged
        # UTTERANCE line (not a marker line). `Block` carries no
        # recognizer_id and `BlockBuilder` has a single, global pause
        # timer rather than one per recognizer -- so a pause marker
        # cannot read "the" recognizer_id off the block it closes. This
        # is a deliberate simplification: the marker gets the id of
        # whichever utterance was logged last, not necessarily the one
        # that belongs to the closed block.
        self._last_recognizer_id: str | None = None

    def record_utterance(self, recognizer_id: str | None, chars: int,
                         cut: str | None, at: datetime,
                         t_start=None, t_end=None) -> None:
        """One real utterance line. May raise -- see the class docstring.

        `at` is captured by the CALLER, at the point the utterance was
        handed to the block builder -- see the class docstring for why
        this class must not stamp "now" itself.

        `t_start`/`t_end` are the audio-clock span of the SPEECH, in
        seconds, as the STT server measured it. Optional and validated
        here rather than trusted: see `valid_span`.
        """
        self._last_recognizer_id = recognizer_id
        self._write(recognizer_id, chars, cut, at, t_start, t_end)

    def record_pause_marker(self, at: datetime) -> None:
        """Marker line: the previously-open block was just closed by a
        pause. NOT a real utterance -- `chars=0` is a pure placeholder,
        so a later reader of the file does not mistake this for an
        87-character silence. May raise -- see the class docstring.

        `at` is captured by the CALLER, at the moment it noticed the
        pause closed the block (i.e. right where `record_pause_marker`
        is called) -- same reasoning as `record_utterance`.
        """
        # No span: a marker is not an utterance and has no speech in it.
        self._write(self._last_recognizer_id, 0, "pause", at, None, None)

    def _write(self, recognizer_id: str | None, chars: int,
              cut: str | None, at: datetime,
              t_start=None, t_end=None) -> None:
        row = {
            "at": at.isoformat(timespec="milliseconds"),
            "recognizer_id": recognizer_id,
            "chars": chars,
            "cut": cut,
        }
        if valid_span(t_start, t_end):
            # Milliseconds: the promise is 50 ms, and sixteen digits of
            # float would claim a precision nobody measured.
            row["t_start"] = round(float(t_start), 3)
            row["t_end"] = round(float(t_end), 3)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
