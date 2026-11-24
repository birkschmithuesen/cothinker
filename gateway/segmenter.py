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

Three triggers, the first that matches cuts:

  PAUSE  Nothing spoken for longer than `pause_s` -> the thought is finished.
         The main case.
  WAKE   The sentence contains the wake word ("Robo") -> cut immediately and
         send with priority. This is an order, not a transcript.
  LENGTH After `max_s` of speaking time, cut even without a pause. Otherwise
         the agent hangs in the air for minutes during a long monologue.

The opposite case: utterances under `min_chars` are NOT sent alone, but
appended to the next block — otherwise a "Right." would chop up the
protocol. Exception: the LENGTH cut also emits a short block — otherwise a
"Yes." would stick to a completely different topic half an hour later.

Time is injected (`now`) so tests don't have to run in real time.

CONCURRENCY: `sentence()` comes from the SSE reader, `tick()` from its own
thread. Both mutate the same open block, so the whole class is protected by
a lock. Without it, sentences get lost (measured: 10 of 3000) or appear
twice (17 of 3000).
"""
from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.wake_word import CLAUSE_LEAD, WAKE_NOT, wake_phrase_pattern  # noqa: E402

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
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.output = output
        self.pause_s = pause_s
        self.max_s = max_s
        self.min_chars = min_chars
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

            if allow_wake and self.wake_word_re is not None and self._is_bare_wake(text):
                # Close whatever was open as an ordinary block -- it is
                # NOT part of the call -- then open a fresh block already
                # flagged as a call for the order that follows.
                if self._open:
                    self._emit("pause", force=True)
                self._open = Block(start=t, end=t, wake_word=True)
                self._last_sentence = t
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
                self._open.wake_word = True
                self._emit("wake")             # order: send right away
                return

            if (t - self._open.start) >= self.max_s:
                self._emit("length")

    def tick(self) -> None:
        """Call regularly — detects the pause AFTER the last sentence."""
        with self._lock:
            if not self._open:
                return
            t = self.now()
            if (t - self._open.start) >= self.max_s:
                self._emit("length")           # takes priority: always cuts
            elif (t - self._last_sentence) >= self.pause_s:
                self._emit("pause")

    def finish(self) -> None:
        """At the end of the session: emit the remainder, however short."""
        with self._lock:
            if self._open:
                self._emit("final", force=True)

    @property
    def open_chars(self) -> int:
        with self._lock:
            return self._open.chars

    # ------------------------------------------------------------ internal
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
