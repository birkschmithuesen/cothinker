#!/usr/bin/env python3
# CoThinker — shared Speechmatics utterance collector
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Merges Speechmatics per-message `AddTranscript` finals into one final per
utterance.

Speechmatics sends one `AddTranscript` message per few words, not per
utterance. A consumer that treated every message as one row would get word
fragments instead of sentences. This collector buffers finals and flushes
one merged utterance on `EndOfUtterance`, on a speaker change, or after
`max_utt_s` -- the same rule for every consumer of the Speechmatics client
(`shared/speechmatics.py`): the meeting STT backend
(`stt/fundusapps/stt_server/backends/speechmatics_rt_backend.py`, which
subclasses this to publish onto its own `EventBus`) and the table hub
(`workshop/hub.py`, which uses it directly via the `on_flush` callback).

Pure Python, no I/O, synchronous: fed with (text, label, start, end) tuples
in session seconds via `on_final()`, flushed via `on_eou()` or on its own
(speaker change, `max_utt_s`).

A flushed utterance's fragments are joined with a plain space, not
`shared.speechmatics._join_transcript`: that function joins raw
word/punctuation results within ONE message, respecting `attaches_to` (no
space before a trailing comma or period). By the time a message's final
text reaches here it is already-joined plain text -- the `attaches_to`
information no longer exists to re-apply across message boundaries, so
re-running that function here would not be the same operation it is in
`_join_transcript` itself. The plain join is the correct, simpler choice,
not a shortcut.
"""
from __future__ import annotations

from typing import Callable, List, Optional

DEFAULT_MAX_UTT_S = 20.0


class UtteranceCollector:
    """Accumulates per-message finals into one utterance; flushes via
    `_emit()`, which a subclass overrides, or via the `on_flush` callback
    given to the constructor.

    A message's own `volume` (mean over its words, as
    `shared/speechmatics.py`'s `SttEvent.volume` already computes it)
    is carried through the merge as the word-count-weighted mean over the
    messages that had one -- the same weighting `SttEvent.volume` itself
    uses over words, one level up. A message without a volume contributes
    nothing to the weighted mean (not a 0.0); a flush where no message
    carried one emits `None`, never coerced to "silent".

    `on_flush_words` receives the per-word items as a sixth
    argument, and the closing `utt_id` as a seventh; `_emit`
    keeps its signature for subclasses.

    `utt_id` identifies the utterance in progress -- the
    live transcript strip groups a confirmed fragment under the same id as
    the partial tail it replaces. Assigned lazily (`_on_before_append`, the
    existing turn-id hook) on the first word after a reset, and cleared again
    by `_reset()`; `on_fragment`, if given, fires once per `on_final()` call,
    right after the fragment is appended, with the text/label/utt_id of that
    one fragment -- before any flush this same call may still go on to
    trigger (speaker change already happened above; max_utt_s check is still
    below)."""

    def __init__(self, max_utt_s: float = DEFAULT_MAX_UTT_S,
                 on_flush: Optional[Callable[
                     [str, Optional[str], Optional[float], Optional[float], Optional[float]], None]] = None,
                 on_flush_words: Optional[Callable[..., None]] = None,
                 on_fragment: Optional[Callable[[str, Optional[str], str], None]] = None):
        self.max_utt_s = max_utt_s
        self._on_flush = on_flush
        self._on_flush_words = on_flush_words
        self._on_fragment = on_fragment
        self._word_items: List[dict] = []
        self._words_complete = True
        self._words: List[str] = []
        self._label: Optional[str] = None
        self._start: Optional[float] = None
        self._end: Optional[float] = None
        self._volume_sum = 0.0
        self._volume_weight = 0.0
        self.utt_id: Optional[str] = None
        self._utt_seq = 0

    def on_final(self, text: str, label: Optional[str],
                 start: Optional[float], end: Optional[float],
                 volume: Optional[float] = None,
                 words: Optional[list] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._words and label and self._label and label != self._label:
            self.flush()
        self._on_before_append()
        self._words.append(text)
        if label and not self._label:
            self._label = label
        if start is not None and self._start is None:
            self._start = start
        if end is not None:
            self._end = end
        if volume is not None:
            weight = len(text.split())
            self._volume_sum += volume * weight
            self._volume_weight += weight
        if words:
            self._word_items.extend(w for w in words if isinstance(w, dict))
        elif any(ch.isalnum() for ch in text):
            self._words_complete = False     # one message without words: no partial per-word view
        # else: a punctuation-only AddTranscript (".", "?") carries no word results by
        # design -- live finding 31.10. Tisch 01: it wiped the per-word view of the whole
        # utterance (54 % of rows without words), so no voiceprint clip was ever cut.
        if self._on_fragment is not None:
            self._on_fragment(text, self._label, self.utt_id)
        if (self._start is not None and self._end is not None
                and self._end - self._start >= self.max_utt_s):
            self.flush()

    def on_eou(self) -> None:
        self.flush()

    def current_utt_id(self, label: Optional[str] = None) -> str:
        """A live partial bypasses `on_final()` entirely, so it needs its own
        way to read (or start) the id of the utterance in progress -- the
        same speaker-change rule `on_final()` applies to a confirmed
        fragment, applied here to a partial instead (a
        partial for a new speaker is itself the earliest signal of a turn
        change, no need to wait for its confirmation)."""
        if self._words and label and self._label and label != self._label:
            self.flush()
        return self._ensure_utt_id()

    def _ensure_utt_id(self) -> str:
        if self.utt_id is None:
            self._utt_seq += 1
            self.utt_id = str(self._utt_seq)
        return self.utt_id

    def flush(self) -> None:
        if self._words:
            volume = self._volume_sum / self._volume_weight if self._volume_weight else None
            text = " ".join(self._words)
            if self._on_flush_words is not None:
                words = list(self._word_items) if self._words_complete and self._word_items else None
                self._on_flush_words(text, self._label, self._start, self._end, volume, words, self.utt_id)
            else:
                self._emit(text, self._label, self._start, self._end, volume)
        self._reset()

    def _on_before_append(self) -> None:
        """Hook for a subclass that needs to act once per utterance, right
        after a pending flush and before the first word lands (e.g. assign
        a turn id). The base class uses it itself for `utt_id`."""
        self._ensure_utt_id()

    def _reset(self) -> None:
        self._words = []
        self._label = None
        self._start = self._end = None
        self._volume_sum = 0.0
        self._volume_weight = 0.0
        self._word_items = []
        self._words_complete = True
        self.utt_id = None

    def _emit(self, text: str, label: Optional[str],
              start: Optional[float], end: Optional[float],
              volume: Optional[float] = None) -> None:
        if self._on_flush is None:
            raise NotImplementedError
        self._on_flush(text, label, start, end, volume)
