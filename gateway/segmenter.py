#!/usr/bin/env python3
# CoThinker — bundling sentences into blocks
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Block building: sentence endings become meaningful sections.

The STT server delivers a `final` after every speaking pause — usually one
sentence. A single sentence is too little context to hand an agent as a task
(measured against a real meeting: median 1,161 characters per meaningful
block, so many sentences). This class bundles sentences into blocks.

Outside a call, three triggers, the first that matches cuts:

  PAUSE  Nothing spoken for longer than `pause_s` -> the thought is finished.
         The main case.
  WAKE   The sentence contains the wake word ("Robo") -> opens a CALL
         WINDOW instead of cutting immediately (see below).
  LENGTH After `max_s` of speaking time, cut even without a pause. Otherwise
         the agent hangs in the air for minutes during a long monologue.

The opposite case: utterances under `min_chars` are NOT sent alone, but
appended to the next block — otherwise a "Right." would chop up the
protocol. Exception: the LENGTH cut also emits a short block — otherwise a
"Yes." would stick to a completely different topic half an hour later.

CALL WINDOW (issue #180): the wake word used to cut and send the block
immediately, the instant it matched -- a live trial (2026-11-24) showed
"Hey Robo. Ich gebe dir." go out alone, with the content of the following
sentences never reaching the agent. The wake word now OPENS a window
instead: every sentence while it is open belongs to the call (pauses do
not cut it), until it is closed -- by the end phrase ("Robo, Ende",
`shared.wake_word.END_PHRASE_RE`, stripped from the order), by a tap
(`open_call`/`close_call`), by `call_silence_s` of silence, or by
`call_max_s` at the latest. Exactly one block, `wake_word=True` and
`reason` one of `end|tap|silence|max`, goes out on close. A call closed
with no content (a tap on an empty window) sends nothing; `state()`
reports that as `last_close={"reason": "cancel", ...}`.

Time is injected (`now`) so tests don't have to run in real time.

CONCURRENCY: `sentence()` comes from the SSE reader, `tick()` from its own
thread, `open_call`/`close_call` from a stage action. All three mutate the
same state, so the whole class is protected by a lock. Without it,
sentences get lost (measured: 10 of 3000) or appear twice (17 of 3000).
"""
from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.wake_word import (CLAUSE_LEAD, END_PHRASE_RE, WAKE_NOT,  # noqa: E402
                              wake_phrase_pattern)

# Trailing filler after the wake word that still means "heard, not told to
# do anything yet" -- live incident 2026-10-27: "Robo
# Aktiv." answered as if it were an order and produced a non-answer,
# wasting a call slot ahead of the real request that followed 11s later.
_BARE_WAKE_FILLER = re.compile(r"^(aktiv)$", re.IGNORECASE)

# Splits an utterance into sentences on a trailing . ! or ? -- used only to
# find the LAST one (see `_is_bare_wake_tail`), never to re-join them.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Block:
    """A coherent section of speech."""
    sentences: list[str] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    reason: str = ""              # pause | wake | length | final
    wake_word: bool = False       # contains a direct order

    @property
    def text(self) -> str:
        return " ".join(s.strip() for s in self.sentences if s.strip()).strip()

    @property
    def chars(self) -> int:
        return len(self.text)

    def __bool__(self) -> bool:
        return bool(self.text)


class BlockBuilder:
    """Collects sentences and emits finished blocks.

    Usage:
        bb = BlockBuilder(output=process)
        bb.sentence("So I'd say we get started.")
        bb.tick()          # call regularly, checks the pause
        bb.finish()        # at the end, emits the remainder
    """

    def __init__(
        self,
        output: Callable[[Block], None],
        pause_s: float = 2.5,
        max_s: float = 90.0,
        min_chars: int = 25,
        wake_word: str = "hey robo",
        call_silence_s: float = 8.0,
        call_max_s: float = 90.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.output = output
        self.pause_s = pause_s
        self.max_s = max_s
        self.min_chars = min_chars
        self.call_silence_s = call_silence_s
        self.call_max_s = call_max_s
        self.now = now
        # "Roboter" must not trigger -- but "Roboterzeuge" must. Speech
        # recognition welds the wake word onto the next one when it is
        # spoken without a pause: measured 2026-09-21 over 2485 log lines,
        # 91x "Robo" recognised and 2x "Roboterzeuge" for "Robo, erzeuge …".
        # That order was silently demoted to a normal utterance, because
        # `\brobo\b` does not match inside a longer word -- and a missed
        # wake word is INVISIBLE: the round gives an order, nothing happens,
        # and no trace of the failure exists anywhere.
        #
        # So the name matches as a PREFIX, minus the German words that
        # legitimately start the same way. Without that subtraction a
        # conversation ABOUT robots ("Robotik", "ein Roboter") would read as
        # a stream of orders -- a real risk here, where robots are a topic.
        #
        # Several wake phrases: comma-separated. An empty one would produce
        # `\b\b`, which matches EVERY sentence; better none at all.
        phrases = [p.strip() for p in (wake_word or "").split(",") if p.strip()]
        patterns = [wake_phrase_pattern(phrase.split(), f"n{i}")
                   for i, phrase in enumerate(phrases)]
        self.wake_word_re = (
            re.compile("|".join(patterns), re.IGNORECASE) if patterns else None)
        self._open = Block()
        self._last_sentence: float = 0.0
        self._lock = threading.Lock()
        # The call window (issue #180): "idle" or "listening". While
        # listening, `self._open` IS the call's block.
        self._window: str = "idle"
        self._call_opened_at: Optional[float] = None
        self._call_opened_by: Optional[str] = None
        self._last_close: Optional[dict] = None

    # ---------------------------------------------------------------- API
    @staticmethod
    def _wake_not(m: re.Match) -> bool:
        """Did `m` match one of the false-friend words (WAKE_NOT) under
        whichever named group of `wake_word_re` fired?"""
        name = next((v for v in m.groupdict().values() if v is not None),
                    None)
        return bool(name) and bool(WAKE_NOT.match(name))

    def _bare_wake_clause(self, clause: str) -> bool:
        """Is `clause` the wake phrase (plus pure activation filler, e.g.
        "Aktiv") and NOTHING else?

        Anchored at the very start on purpose: "Hey Robo, mach das" is the
        phrase PLUS an order in the same clause and must keep its order.
        """
        if self.wake_word_re is None:
            return False
        stripped = CLAUSE_LEAD.sub("", clause)
        m = self.wake_word_re.match(stripped)
        if not m or self._wake_not(m):
            return False
        rest = re.sub(r"[\s,.!?:;\-–„“\"'»«]+", " ",
                      stripped[m.end():]).strip()
        return not rest or bool(_BARE_WAKE_FILLER.match(rest))

    def _is_bare_wake(self, text: str) -> bool:
        """Is `text` the wake word and NOTHING else?

        People say the name, pause, then give the order as a separate
        utterance (live finding 2, 2026-10-26: "Robo" alone cut a block
        from the explanation that came BEFORE it, and the real order that
        followed was never taken as the call's content). A bare wake word
        must therefore not swallow whatever was already open -- it opens a
        fresh, already-flagged block for what follows instead.
        """
        return self._bare_wake_clause(text)

    def _is_bare_wake_tail(self, text: str) -> bool:
        """Is the LAST sentence of `text` a bare wake (see
        `_bare_wake_clause`), with nothing actionable in it?

        Covers the case `_is_bare_wake` cannot: the STT server welds an
        explanation and the wake word into ONE `final` when there is no
        detectable pause between them (live incident 2026-10-27:
        "Das heisst, wir koennten Robo dazu auch mal fragen.
        Robo Aktiv." arrived as a single final). Only the LAST sentence is
        checked -- a wake word addressed at the end of a sentence that
        OPENS with a real order ("Mach das bitte, Robo.") keeps that order,
        because that clause does not START with the wake word.
        """
        clauses = [c for c in _SENTENCE_SPLIT.split(text) if c.strip()]
        return bool(clauses) and self._bare_wake_clause(clauses[-1].strip())

    def _is_wake(self, text: str) -> bool:
        """Does `text` address the agent -- the phrase at the START of a
        clause (utterance start, or right after `.`/`!`/`?`), minus false
        friends.

        Clause-start on purpose: a bare name talked ABOUT the assistant
        ("Robo hat vorhin gesagt ...") or a real word that happens to
        start the same way ("Wobei ich finde ...") must not fire just
        because the phrase occurs somewhere in the text -- only an
        address, at the start of what someone is saying, counts.
        """
        if self.wake_word_re is None:
            return False
        for clause in _SENTENCE_SPLIT.split(text):
            clause = clause.strip()
            if not clause:
                continue
            stripped = CLAUSE_LEAD.sub("", clause)
            m = self.wake_word_re.match(stripped)
            if m and not self._wake_not(m):
                return True
        return False

    def sentence(self, text: str, allow_wake: bool = True) -> None:
        """A `final` from the STT server.

        `allow_wake=False`: the line is bundled normally but never cuts a
        wake block -- used for self-introductions during a running intro
        round ("Ich bin Max ... ob der Robo mich erkennt" became an ORDER,
        live 2026-10-20 and dress rehearsal).
        """
        # The STT server is foreign code: `text` is not guaranteed to be a
        # string. An AttributeError here would take down the SSE reader.
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        text = text.strip()
        if not text:
            return
        with self._lock:
            t = self.now()

            if self._window == "listening":
                # Every sentence while the call is open belongs to it
                # (issue #180) -- pauses do not cut it, only the end
                # phrase, a tap, silence or the call's own max.
                self._collect_call_locked(text, t)
                return

            if allow_wake and self.wake_word_re is not None and self._is_bare_wake(text):
                # Close whatever was open as an ordinary block -- it is
                # NOT part of the call -- then open the call window for
                # the order that follows.
                self._start_call("voice", t, keep_open=False)
                return

            # Pause BEFORE this sentence? Then it no longer belongs to the old block.
            if self._open and (t - self._last_sentence) >= self.pause_s:
                self._emit("pause")

            if not self._open.sentences:
                self._open.start = t
            self._open.sentences.append(text)
            self._open.end = t
            self._last_sentence = t

            if (allow_wake and self.wake_word_re is not None
                    and self._is_wake(text)):
                if self._is_bare_wake_tail(text):
                    # No order anywhere in this utterance either -- just
                    # the wake word, welded on at the end. Flush it as
                    # ordinary speech (nothing is silently dropped) and
                    # open a fresh, UNFLAGGED block for what follows.
                    #
                    # Unlike the standalone bare wake above, this clause
                    # was welded onto an utterance that already carries
                    # unrelated content -- live incident 2026-10-27:
                    # pre-flagging here let the NEXT speaker's
                    # filler ride out as a call twice in the same window,
                    # once after "... Robo dazu auch mal fragen. Robo
                    # Aktiv." and once after an incidental name-drop,
                    # "Ja, das passt schon. Erika. Robo."
                    # (Erika is a participant, not an order). A genuine
                    # follow-up order still fires: it either repeats the
                    # wake word itself (caught by `_is_wake` below on its
                    # own merit) or accumulates past `min_chars`/`max_s`
                    # like any other speech -- nothing is swallowed.
                    self._emit("pause", force=True)
                    self._open = Block(start=t, end=t)
                    self._last_sentence = t
                    return
                # The order is already accumulated in `self._open`
                # (e.g. "Hey Robo, fass zusammen") -- the call window
                # takes over THIS block rather than starting a fresh one,
                # so the content already said is not lost.
                self._start_call("voice", t, keep_open=True)
                # Wake and end phrase can arrive welded into one STT final
                # ("Hey Robo, fass zusammen. Robo, Ende." with no pause
                # the server could split on) -- check the end phrase on
                # the very same text that just opened the call.
                self._close_if_end_phrase_locked()
                return

            if (t - self._open.start) >= self.max_s:
                self._emit("length")

    def tick(self) -> None:
        """Call regularly — detects the pause AFTER the last sentence, or
        a call window's silence/max timeout."""
        with self._lock:
            t = self.now()
            if self._window == "listening":
                if (t - self._call_opened_at) >= self.call_max_s:
                    self._close_call_locked("max")
                elif (t - self._last_sentence) >= self.call_silence_s:
                    self._close_call_locked("silence")
                return
            if not self._open:
                return
            if (t - self._open.start) >= self.max_s:
                self._emit("length")           # takes priority: always cuts
            elif (t - self._last_sentence) >= self.pause_s:
                self._emit("pause")

    def finish(self) -> None:
        """At the end of the session: emit the remainder, however short."""
        with self._lock:
            if self._window == "listening":
                self._close_call_locked("final")
                return
            if self._open:
                self._emit("final", force=True)

    @property
    def open_chars(self) -> int:
        with self._lock:
            return self._open.chars

    # -------------------------------------------------------- call window
    def open_call(self, source: str) -> None:
        """Opens the call window -- a tap on the stage button (or any
        external trigger), same effect as the wake word firing."""
        with self._lock:
            if self._window == "listening":
                return
            self._start_call(source, self.now(), keep_open=False)

    def close_call(self, source: str) -> None:
        """Closes the call window -- a tap while listening. No-op while
        idle (nothing to close)."""
        with self._lock:
            self._close_call_locked(source)

    def state(self) -> dict:
        """The window's current state, for the `call_window` building
        block to turn into the `listen:room` view intent (ADR 0023)."""
        with self._lock:
            last_close = dict(self._last_close) if self._last_close else None
            if self._window != "listening":
                return {"state": "idle", "since": None, "opened_by": None,
                        "closes_in_s": None, "last_close": last_close}
            closes_in_s = max(
                0.0, self.call_silence_s - (self.now() - self._last_sentence))
            return {"state": "listening", "since": self._call_opened_at,
                    "opened_by": self._call_opened_by,
                    "closes_in_s": closes_in_s, "last_close": last_close}

    # ------------------------------------------------------------ internal
    def _start_call(self, source: str, t: float, *, keep_open: bool) -> None:
        """Only call while holding the lock. Flips the window to
        listening. `keep_open=True` reuses the already-accumulated
        `self._open` (the order text is already in it, see `sentence()`);
        otherwise whatever was open is flushed as an ordinary block first
        (it is NOT part of the call) and a fresh, empty one starts it."""
        if not keep_open:
            if self._open:
                self._emit("pause", force=True)
            self._open = Block(start=t, end=t)
        self._window = "listening"
        self._call_opened_at = t
        self._call_opened_by = source
        self._last_sentence = t

    def _collect_call_locked(self, text: str, t: float) -> None:
        """Only call while holding the lock, with the window listening.
        Appends `text` to the call, then closes it if that sentence
        carried the end phrase; otherwise checks the call's own max."""
        if not self._open.sentences:
            self._open.start = t
        self._open.sentences.append(text)
        self._open.end = t
        self._last_sentence = t
        if self._close_if_end_phrase_locked():
            return
        if (t - self._call_opened_at) >= self.call_max_s:
            self._close_call_locked("max")

    def _close_if_end_phrase_locked(self) -> bool:
        """Only call while holding the lock, with the window listening
        and the latest sentence already appended as
        `self._open.sentences[-1]`. If THAT sentence carries the end
        phrase (same clause-start, false-friend rules as the wake
        phrase), strips it, keeps any content before it in the same
        clause, closes the call and returns True; otherwise returns
        False, leaving the call open."""
        last = self._open.sentences[-1]
        clauses = [c for c in _SENTENCE_SPLIT.split(last) if c.strip()]
        end_at = None
        for i, clause in enumerate(clauses):
            stripped = CLAUSE_LEAD.sub("", clause.strip())
            m = END_PHRASE_RE.match(stripped)
            if m and not self._wake_not(m):
                end_at = i
                break
        if end_at is None:
            return False

        before = " ".join(c.strip() for c in clauses[:end_at])
        if before:
            self._open.sentences[-1] = before
        else:
            self._open.sentences.pop()
        self._close_call_locked("end")
        return True

    def _close_call_locked(self, reason: str) -> None:
        """Only call while holding the lock. Closes the call window;
        sends the accumulated block unless it is empty, in which case the
        close is recorded as a `cancel` and nothing goes out."""
        if self._window != "listening":
            return
        b = self._open
        self._open = Block()
        self._window = "idle"
        self._call_opened_at = None
        self._call_opened_by = None
        t = self.now()
        if not b:
            self._last_close = {"reason": "cancel", "at": t}
            return
        b.reason = reason
        b.wake_word = True
        self._last_close = {"reason": reason, "at": t}
        self.output(b)
    def _emit(self, reason: str, force: bool = False) -> None:
        """Only call while holding the lock.

        `output` deliberately runs INSIDE the lock: only that way is the
        order of blocks across thread boundaries guaranteed. The callback
        must therefore not block and must not call back in here (it should
        only put the block into a queue).
        """
        b = self._open
        if not b:
            self._open = Block()
            return
        # Too short and no order -> leave it, it keeps growing.
        # The length cut overrides this, otherwise a "Yes." would sit in the
        # open block until the end of the session.
        if (not force and reason != "length" and not b.wake_word
                and b.chars < self.min_chars):
            return
        b.reason = reason
        self._open = Block()
        self.output(b)
