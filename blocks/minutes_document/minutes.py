# CoThinker — blocks/minutes_document/minutes.py: the minutes document renderer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Renders the sections of the minutes document (moved from the consumer's
`Protocol`, issue #44).

This class only reads the document and hands the rendered head (everything
in front of the raw section) to `write_head`. It never writes the file:
the session host splices the head in under its own lock and is the only
writer. The raw section is carried through by the host byte-identically.
"""
from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from shared.minutes_format import (DEFAULT_MODE, MODES, TAGS, Entry, Topic,
                                   clean_line, clean_wikilinks, entry_line,
                                   parse_topics, read_mode, render_view,
                                   split_raw, splice, topic_line)
from shared.minutes_format import is_v2 as is_v2_text


def log(*a) -> None:
    print("[minutes-document]", *a, file=sys.stderr, flush=True)


@dataclass
class EntryResult:
    """What `MinutesDocument.add_entry()` did. `reason` is "" when it landed.

    Reasons are fixed English words (the trace turns the space into an
    underscore): "unknown tag", "no text", "not v2", "not written", and
    "duplicate" for an entry identical to the last one of the current
    block or a decision whose text is already filed as a decision anywhere
    in the document. The link counts come from the wikilink check and are numbers
    only, so they can go into the trace as they are.
    """
    reason: str = ""
    links_seen: int = 0
    links_dead: int = 0


_HHMM_RE = re.compile(r"\A([01]\d|2[0-3]):[0-5]\d\Z")


def _decision_key(text: str) -> str:
    """A decision's text for comparison: lower case, whitespace collapsed."""
    return " ".join(text.split()).casefold()


def _stamp_or_now(stamp) -> str:
    """`stamp` if it is a proper HH:MM, otherwise the current minute."""
    if isinstance(stamp, str) and _HHMM_RE.match(stamp.strip()):
        return stamp.strip()
    return f"{datetime.now():%H:%M}"


class MinutesDocument:
    """The sections of a v2 minutes document.

    `add_entry()` / `open_topic()` read the document, render the new head
    and pass it to `write_head`. Callers that need the document not to
    change between the read and the host's splice (the session host's
    `MinutesPort` does) hold the file lock around the call.
    """

    def __init__(self, path: Path, *, mode: str = DEFAULT_MODE,
                 write_head: Callable[[str], None]):
        self.path = Path(path)
        self._write_head = write_head
        self._lock = threading.RLock()
        # The mode picks the sections of the derived view (render_view).
        self.mode = mode if mode in MODES else DEFAULT_MODE
        # Decided by the file as it is at start: a v1 file of the same day
        # continues as v1. Re-checked on every write.
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        self.is_v2 = is_v2_text(text)
        # An existing v2 file keeps the mode it was created with: the flag
        # only chooses the mode of a NEW file. A restart with another
        # `--mode` must not re-render this meeting's overview under other
        # section headings.
        if self.is_v2:
            recorded = read_mode(text)
            if recorded is not None and recorded != self.mode:
                log(f"mode flag {self.mode} ignored: the protocol was "
                    f"created as {recorded}")
                self.mode = recorded

    # ------------------------------------------------------------ writes
    def open_topic(self, title, stamp=None) -> str:
        """Append `### HH:MM Titel` -- a new topic block. v2 only.

        Returns "" on success, else the reason. Never raises.
        """
        clean = clean_line(title if isinstance(title, str) else "", 120)
        if not clean:
            return "no text"
        when = _stamp_or_now(stamp)

        def build(topics: list) -> str:
            topics.append(Topic(time=when, title=clean, entries=[]))
            return topic_line(when, clean)

        return self._rewrite(build)

    def add_entry(self, tag, text, *, stamp=None,
                  names: Iterable[str] | None = None) -> EntryResult:
        """Append `- HH:MM [tag] Text` to the current topic block. v2 only.

        An entry before any topic block opens `### HH:MM Beginn`. With
        `names` (the project's file stems) every `[[wikilink]]` is checked
        and a dead one is reduced to plain text BEFORE it is written --
        the protocol must not promise a file that is not there. An entry
        identical (tag and text) to the last one of the current block is
        not appended again, and neither is a `beschluss` whose text
        (case-insensitive, whitespace-normalised) equals ANY decision
        already in the document: the same decision can reach this method
        twice -- once via `decision`, once as a `tag_entry` -- and the
        overview must not list it twice. Never raises; see `EntryResult`.
        """
        if not isinstance(tag, str) or tag not in TAGS:
            return EntryResult("unknown tag")
        if not isinstance(text, str):
            return EntryResult("no text")
        seen = dead = 0
        # The pre-processing sits inside the same never-raise promise as
        # the write: `names` comes from the caller and a stray value in it
        # (not iterable, not a string) must cost this entry, not the turn.
        try:
            if names is not None:
                text, seen, dead = clean_wikilinks(
                    text, {n for n in names if isinstance(n, str)})
            clean = clean_line(text, 500)
            when = _stamp_or_now(stamp)
        except Exception as e:            # noqa: BLE001 -- see the docstring
            log("entry not prepared:", repr(e))
            return EntryResult("not written", seen, dead)
        if not clean:
            return EntryResult("no text", seen, dead)

        def build(topics: list) -> str | None:
            append = ""
            if not topics:
                topics.append(Topic(time=when, title="Beginn", entries=[]))
                append = topic_line(when, "Beginn")
            current = topics[-1]
            last = current.entries[-1] if current.entries else None
            if last is not None and last.tag == tag and last.text == clean:
                return None
            if tag == "beschluss":
                key = _decision_key(clean)
                if any(entry.tag == "beschluss"
                       and _decision_key(entry.text) == key
                       for topic in topics for entry in topic.entries):
                    return None
            current.entries.append(Entry(time=when, tag=tag, text=clean))
            return append + entry_line(when, tag, clean)

        return EntryResult(self._rewrite(build), seen, dead)

    # ------------------------------------------------------------- reads
    def current_topic(self) -> tuple[str, str]:
        """(key, title) of the LAST topic block; ("", "") when there is
        none (or the file cannot be read, or is not v2). The key names the
        block for this run: its position, time and title line -- two blocks
        with the same heading still differ by position. Never raises."""
        try:
            with self._lock:
                text = self._read()
            if not is_v2_text(text):
                return "", ""
            topics = parse_topics(text)
            if not topics:
                return "", ""
            t = topics[-1]
            return f"{len(topics)}|{t.time}|{t.title}", t.title
        except Exception as e:            # noqa: BLE001 -- see the docstring
            log("protocol not read:", repr(e))
            return "", ""

    def current_topic_title(self) -> str:
        """The title line of the LAST topic block, "" when there is none.
        Never raises."""
        return self.current_topic()[1]

    # ---------------------------------------------------------- internals
    def _read(self) -> str:
        # newline="" and surrogateescape keep what is read byte-faithful,
        # whatever line endings or stray bytes the document holds.
        with open(self.path, encoding="utf-8", newline="",
                  errors="surrogateescape") as f:
            return f.read()

    def _rewrite(self, build: Callable[[list], str | None]) -> str:
        """Read, render, hand the head over -- the one way a write happens.

        `build(topics)` gets the parsed topic blocks, adds what it appends
        to them (so the view includes it) and returns the lines to append,
        or None for "nothing to do" (a duplicate). Returns "" on success or
        the reason. Never raises: a failure is logged and reported to the
        caller as "not written".
        """
        with self._lock:
            try:
                text = self._read()
                if not is_v2_text(text):
                    self.is_v2 = False
                    return "not v2"
                topics = parse_topics(text)
                append = build(topics)
                if append is None:
                    return "duplicate"
                new = splice(text, view=render_view(topics, self.mode),
                             append=append)
                head, _ = split_raw(new)
                self._write_head(head)
                return ""
            except Exception as e:        # noqa: BLE001 -- see the docstring
                log("protocol not written:", repr(e))
                return "not written"
