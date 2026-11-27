#!/usr/bin/env python3
# CoThinker - the stage state and the rules that decide it
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""What is on the stage, and what decides it. See docs/stage-methodology.md.

Pure data plus deterministic rules: no I/O, injected clocks. The agent
proposes typed entries; this module assigns the identities, applies the
caps and keeps the clocks. The consumer is the only writer of the
displayed file.

Two clocks on purpose: `now()` is monotonic and measures durations (it
cannot jump when the system clock is corrected), `clock()` is the wall
clock and only ever produces display labels.

THREADS. Four threads of the consumer touch this state: the agent thread
(after a turn), the ticker (twice a second), the SSE reader and the
watchdog. `tick()` removes settled entries from lists, so two threads
sweeping the same entry used to leave the loser with a `ValueError` --
in the agent thread that turned a SUCCESSFUL run into a failed one, and
its block was sent a second time. Every entry point that mutates or
reads the lists therefore takes `self._lock`.

LOCK ORDER, the only one there is: the consumer's `StageWriter._lock`
first, then `StageState._lock` (that is what `write()` does when it
calls `content()`). This state never calls back into the writer, so the
reverse order does not exist and the two cannot deadlock.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.text import MAX_PENDING_LABEL, derived_title, one_line  # noqa: E402

# STILL 2, although the history is new. `content()` only GAINED a key:
# `main` is unchanged and still carries the displayed surface, so an old
# stage page and an old consumer read a new file exactly as before, and
# `restore()` takes an old file (no `history`, only `main`) back as a
# history of one. A bump would buy nothing and cost everything: `restore()`
# refuses a differing version with a ValueError, so the first restart in
# the middle of a meeting would drop the whole surface the round is
# reading -- for a file this version can read either way.
SCHEMA_VERSION = 2

MAX_NOTICES = 2          # "two message slots and one question spot"
MAX_DECISIONS = 3        # header line stays one line
# A3 (operator, 2026-09-21): the line-count cap on the main area was removed,
# ersatzlos -- not raised. A line count is the wrong measure: 15 bullet
# points and 15 full sentences need different height, and the server
# does not know the reader's browser font size. Scrolling is the accepted
# fallback, not a failure. `collapsed_lines`/`collapsed_markdown` on
# `Main` stay in the schema -- the fold mechanism itself remains for A4
# (there it becomes "collapsed = provenance only") -- but `_surface()`
# below no longer fills them from a line count.
AGE_VISIBLE_S = 20.0
FOLLOW_UP_S = 120.0
QUESTION_EXPIRY_S = 600.0
RECEIPT_LINGER_S = 6.0
# How many not-yet-recorded entries ride along in the prompt at most. They
# leave as soon as one run comes back, so this only fills up while runs
# keep failing -- and an unbounded list would then grow the prompt for the
# rest of the meeting. The newest are kept: the oldest have had the most
# chances already.
MAX_UNRECORDED = 20

# --- the automatic topic change and its brakes -----------------------------
# Ground: docs/kurator-kernmotivation.md (normative) N1 -- following the
# conversation is the normal case, not the exception -- together with N5:
# brakes, not prohibitions. The numbers are docs/entwurf-buehne-verlauf.md
# section 3.1.
#
# SET, NOT MEASURED, and therefore withdrawable (methodology, *Reversible
# settings*). Section 5 names the withdrawal signal, and both halves are
# readable off `stage-events-*.jsonl`, which carries no speech:
#
#   * TOO SMALL -- topic changes follow each other closely in the trace and
#     the round reports the surface is restless;
#   * TOO LARGE -- he looks at the OLD surface while a new topic runs and
#     has to reach for a call-out. A call-out that orders nothing and only
#     fetches up what should have happened by itself is a measurement
#     against this setting, not a use of it.
TOPIC_MIN_HOLD_S = 45.0      # a surface stands at least this long ...
TOPIC_WINDOW_S = 60.0        # ... and inside one window at most
TOPIC_MAX_PER_WINDOW = 1     #     this many automatic changes happen
MAX_HISTORY = 20             # how many surfaces stay reachable

NOTICE_KINDS = {"value_conflict", "decision_breach", "speaker_conflict"}

# --- the announcement: "gleich kommt was" ------------------------------------
# The consumer sometimes KNOWS that something is about to reach the stage
# before the surface or the entry exists -- a call-out was heard, and the
# agent needs 40-180 s to answer it. Saying so at once is what keeps the
# round from asking "hat er mich gehoert?". The kinds are the things that
# can arrive; the label is set by the calling code, never by a model.
PENDING_KINDS = {"topic", "main", "research", "document", "question",
                 "decision", "entry"}
# An announcement that has not been kept after a minute is a promise the
# room stopped waiting for. It leaves as "discarded" (verworfen) instead of
# standing on the stage for the rest of the meeting.
PENDING_TIMEOUT_S = 60.0
# An image shown on request (`BILD:` from the call lane) closes on the
# next mirror line or topic change, or after this long -- it is an answer
# to one call, not a new wallpaper for the rest of the meeting.
IMAGE_TTL_S = 300.0
# The page shows at most three; the state keeps no more than it shows, so
# nothing is announced that nobody can see. The OLDEST gives way.
MAX_PENDING = 3
# Which accepted action keeps which announcement. A graph is the same
# movement as a topic change (see `_brake()`), so it keeps a topic
# announcement. `research` and `document` have no action today: they end
# through `settle()` or the timeout. `entry` has no action either: level 2
# settles it by id (blocks/decisions_tasks_questions/policy_runner.py).
#
# `main` is NOT here, on purpose. A `main` announcement is a call-out, and
# the run that answers it takes 40-180 s -- longer than the timeout. By
# the time its surface arrives, its own announcement may be long gone and
# a NEWER call-out's may stand; "the oldest main" would then end the
# wrong one, still waiting, and pair the outcome with the wrong id in the
# trace. Only the consumer knows which announcement belongs to which
# call-out block, so it settles that one by id after the block's run
# (`CallOutAnnouncements` in gateway/consumer.py).
_ARRIVES_AS = {"topic": "topic", "graph": "topic",
               "decision": "decision", "question": "question"}

# The two rejection reasons of the automatic path, named for the same
# reason as `PARKED`: the consumer writes them into the trace as tokens
# (the space becomes an underscore), and a bare string in two places
# drifts. Neither is an error -- a refused topic change means the brake
# worked.
TOO_SOON = "too soon"
TOO_MANY = "too many"

# "parked" is a rejection reason AND a visible change -- see `_park()` and
# `apply()`. Named so the consumer can tell it from the other reasons
# without matching a bare string in two places.
PARKED = "parked"

_WHITESPACE = re.compile(r"\s+")
# The characters an agent wraps a quotation in. Stripping them is not
# tolerance for invention: they delimit the quote, they are not part of
# what was said.
_QUOTE_MARKS = "\"'«»›‹„“”‚‘’ "


def scrub(text: str) -> str:
    """Foreign text with everything UTF-8 cannot encode removed.

    `json.loads` accepts lone surrogates -- `"Robo \\ud83d hat verstanden"`
    is a routine artefact of a truncated emoji from a language model --
    and every writer in this project ends in `encode("utf-8")`. One such
    value reaching the state raised `UnicodeEncodeError` in the stage
    writer, in the ticker twice a second, and in the SSE reader, where it
    looked like a dropped stream: the reconnect backoff then cost the raw
    transcript everything spoken during it.

    Dropped, not replaced: half an emoji carries no meaning, and a `?` in
    the middle of a German sentence on the stage is noise the round has to
    read past.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    return text


def _text(value: object) -> str | None:
    """A field from foreign JSON, or None when it is not usable text.

    The single funnel for every string an agent proposes -- mirror,
    headline, markdown, quote, decision, notice, question, link, receipt.
    The scrub sits HERE and not in the proposal reader because this is
    what every field of every action passes through, including the ones
    that never came out of a file: `resolve()` from the consumer, the
    startup mirror line, and whatever field is added next. A value that
    cannot be encoded must never reach the state, and this is the only
    place that sees all of them.
    """
    if not isinstance(value, str):
        return None
    cleaned = scrub(value).strip()
    return cleaned or None


def _normalised(text: str) -> str:
    """Whitespace-collapsed, case-folded text for the quote comparison."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


def quote_is_spoken(quote: str, spoken: str) -> bool:
    """Did `quote` really occur in what was said in this run?

    The mechanical substitute for judging language (section 5.2): the
    consumer does not decide whether the round gave an order, it demands
    evidence. Tolerant of exactly two things, because neither can invent
    content: line breaks and spacing (the block joins sentences, the agent
    re-wraps), and case (speech recognition capitalises differently than
    the agent quotes). Not tolerant of anything else -- a paraphrase, a
    "fixed" number or a plausible-sounding instruction nobody gave fails.
    """
    needle = _normalised(quote.strip(_QUOTE_MARKS))
    return bool(needle) and needle in _normalised(spoken)


@dataclass
class Main:
    """One surface -- and, since the history exists, one entry in it.

    `title` is the label the history is navigated by. A `topic` brings its
    own (it IS the new topic); a call-out has none and gets one derived
    from the markdown, see `derived_title()`.
    """
    id: str = ""
    title: str = ""
    markdown: str = ""
    quote: str = ""
    set_at: float = 0.0
    set_at_label: str = ""
    collapsed_lines: int = 0
    collapsed_markdown: str = ""
    # The surface KIND. Until the graph there was exactly one, and
    # `history_parts()` rendered every position with `md(...)`. A graph
    # position carries its nodes and edges instead of markdown: paging
    # back has to show the graph of THEN, not today's (entwurf-graph-
    # schirm section 4). "markdown" stays the default, so every existing
    # call site and every old file means what it always meant.
    kind: str = "markdown"
    graph: dict | None = None


def _history_row(m: Main, *, ist_neuste: bool) -> dict:
    """One `history` entry for `content()` -- the full row for markdown
    or the NEWEST graph position, a size-bounded stub for an older one.

    Operator decision, 2026-09-16: Variante 1 of the three proposed in the
    C2 review. A graph payload alone measures ~160 kB
    (47 nodes / 304 edges, vault-realistic) -- MAX_HISTORY (20) full graph
    positions pushed `content()` to 1 916 705 bytes measured, 2.7x the
    700 000-byte bound that measurement was checked against at the time.
    Nodes and edges are the expensive part,
    not the position count, and only the NEWEST graph position (the one
    `main` shows) is ever drawn with its full drawing anyway -- an older
    one is reached only by paging back. So only the newest keeps
    nodes/edges; an older one keeps its seed (small, still says what the
    position was about) so a reader paging back sees which note it was,
    not just a blank void. `stage/stage.py:graph_part_stub()` renders a
    notice for it instead of the drawing.
    """
    row = {"id": m.id, "title": m.title, "markdown": m.markdown,
           "set_at": m.set_at_label,
           "collapsed_lines": m.collapsed_lines,
           "collapsed_markdown": m.collapsed_markdown}
    if m.kind == "graph" and m.graph:
        row["kind"] = m.kind
        row["graph"] = (m.graph if ist_neuste
                         else {"seed": m.graph.get("seed", "")})
    return row


@dataclass
class Decision:
    id: str
    time: str
    text: str
    added_at_label: str


@dataclass
class Notice:
    id: str
    kind: str
    text: str
    raised_at: float
    raised_at_label: str
    state: str = "open"           # open | resolved | expired
    resolution: str = ""
    settled_at: float = 0.0
    follow_up: bool = False


@dataclass
class Question:
    id: str
    text: str
    raised_at: float
    raised_at_label: str
    state: str = "open"           # open | resolved | expired
    resolution: str = ""
    settled_at: float = 0.0
    follow_up: bool = False


@dataclass
class Parked:
    """A notice or question that found no free slot.

    It carries no identity: nothing can be resolved on it and nothing may
    reference it -- its content goes to `## Offene Fragen`, the stage only
    says briefly that it happened. The text is kept anyway: the protocol
    side has to know WHAT was parked to put it under `## Offene Fragen`.
    """
    text: str
    parked_at: float
    parked_at_label: str


@dataclass
class Pending:
    """An announcement: something of `kind` is on its way to the stage.

    It carries an identity so the trace can pair its start with its end,
    and so the page can tell a new announcement from one it already
    animates. It never carries what is coming -- only a short label the
    calling code chose.
    """
    id: str
    kind: str
    label: str
    since: float
    since_label: str


class StageState:
    """The stage as data. `apply()` is the only way in."""

    def __init__(self,
                 now: Callable[[], float] = time.monotonic,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self._now = now
        self._clock = clock
        # Reentrant: `apply()` reaches `resolve()` through
        # `_resolve_action()`, and both are public entry points that take
        # the lock themselves.
        self._lock = threading.RLock()
        self.mirror = ""
        # A substantive call-out answer, kept visible independent of
        # `mirror` (60s, see call_lane.MIRROR_EXPIRY_S) and of
        # `main`/`topic` (replaced the moment the room moves on, by
        # design -- see `_topic()`'s docstring). Live incident,
        # 27.10.2026: a funding overview answered three wake blocks and
        # was gone from the room's view within seconds of landing, because
        # nothing it reached survives a topic change on its own.
        self.call_answer = ""
        self.headline = ""
        # The main area is a HISTORY, and what is on display is its last
        # entry (see the `main` property). Nothing here points at a
        # position: the reader's position is client state in
        # `stage/stage.py`, so two viewers cannot page each other's view
        # away and the consumer's state does not hang off a browser.
        self.history: list[Main] = []
        # The two brakes of N5, and nothing else reads them: when the
        # surface last changed, and when the automatic changes of the
        # current window happened.
        self._surface_at = 0.0
        self._topic_at: list[float] = []
        self.decisions: list[Decision] = []
        self.notices: list[Notice] = []
        self.question: Question | None = None
        self.links: list[dict] = []
        # The counter is the running total over the whole meeting (it goes
        # into the event trace); `parked_entries` is only what is on
        # display right now, so the page can show the dimmed line at all.
        self.parked = 0
        self.parked_entries: list[Parked] = []
        self._counters: dict[str, int] = {"M": 0, "D": 0, "N": 0, "Q": 0,
                                          "P": 0}
        # What is announced and has not arrived yet, oldest first. NOT to be
        # confused with `_pending_events` below, which is an event buffer.
        self.pending: list[Pending] = []
        # Events for entries removed outside tick()'s own sweep -- a
        # settled question displaced by a new one, for instance. tick()
        # drains this before it looks at the clocks, so nothing that
        # disappears does so without a trace.
        self._pending_events: list[dict] = []
        # What left the stage without ever being written down: parked
        # notices and expired questions. "Nichts verschwindet ersatzlos"
        # only holds if somebody records them, and the agent writes the
        # protocol -- so the consumer hands these to the next prompt and
        # drops them once that run came back successfully.
        self._unrecorded: list[dict] = []
        # `{id, caption, set_at}` or None. Only the id (a hash) and the
        # caption go into buehne.json, never the path.
        self.image: dict | None = None
        # The agenda + voice-command zone (AgendaController in
        # gateway/consumer.py), or None when a session has no `agenda.md`.
        # Not restored from `buehne.json`: it is rebuilt from the `timer`
        # and `speaker-queue` blocks' view intents, which restore their
        # own state on start (see `AgendaController` there).
        self.agenda: dict | None = None
        # Optional stage regions rendered by `gateway/stage_surface.py`
        # from the `queue`, `voice`, `share` and `graph` view intents. None means
        # the key is left out of `content()` altogether, so a session
        # without those blocks writes the same file as before (the
        # version stays, see SCHEMA_VERSION). Absolute data only: no
        # ticking value may sit in here (the fingerprint).
        self.queue: dict | None = None
        self.voice: dict | None = None
        self.share: dict | None = None
        self.session_graph: dict | None = None
        # The latest knowledge hits for the current topic, from the
        # `knowledge-link` block's `knowledge:<topic_id>` view intent
        # (issue #148). None means the key is left out of `content()`
        # (key-absent-means-hidden), same as `queue`/`share`/`session_graph`.
        self.knowledge: dict | None = None
        # The call window's state, from the `call-window` block's
        # `listen:room` view intent (issue #180, ADR 0024). None means the
        # key is left out of `content()` (key-absent-means-hidden), same
        # as `queue`/`share`/`session_graph`/`knowledge`.
        self.listen: dict | None = None
        # The last 10 finished transcript lines, from the `live-transcript`
        # block's `transcript:recent` view intent (issue #181). Always a
        # list, never None: `StageStateSnapshot.transcript_recent` has no
        # Optional variant (unlike `queue`/`voice`/`share`/`session_graph`/
        # `knowledge`, it is not a region that is absent until a block
        # shows up -- an empty list already is "nothing said yet").
        self.transcript_recent: list[dict] = []
        # The utterance in progress, from the `live-transcript` block's
        # `transcript:partial` view intent (issue #181). None = nothing in
        # progress (key-absent-means-hidden); an intent with empty `text`
        # (its final landed) clears it.
        self.transcript_partial: dict | None = None
        # The tasks the meeting named, from the decisions-tasks-questions
        # block's `tasks:room` view intent (issue #129): a LIST, like the
        # contract's `StageStateSnapshot.tasks`. None until the first task
        # -- the key is left out of `content()` until then.
        self.tasks: list | None = None
        # The meeting chat thread, from the `meeting-chat` block's
        # `chat:room` view intent (issue #208). None = no chat yet
        # (key-absent-means-hidden).
        self.chat: dict | None = None

    # ------------------------------------------------------------ helpers
    @property
    def main(self) -> Main:
        """The surface on display: the last entry of the history.

        A read access, kept deliberately. The consumer reads `main.id`,
        `main.quote` and `main.collapsed_lines` in `applied_event()` and
        compares `main.id` before and after a turn to tell an answered
        call-out from an unanswered one. None of that is about a history,
        and rewriting those call sites for a history that has a name for
        its last entry would be churn.

        An empty `Main()` while the history is empty, so a reader sees the
        same empty-at-the-start surface it saw before the history existed.
        """
        return self.history[-1] if self.history else Main()

    def append_to_history(self, entry_id: str, addition: str) -> bool:
        """Appends `addition` to an EXISTING history entry's markdown, by id.

        For a lane result that arrives after its topic is no longer
        current (gateway/call_lane.py, konzept-spuren-architektur.
        md's "kein Stau" applied to the coach lane): the result belongs
        to that OLDER entry's own record, never to whatever the surface
        shows now -- `main` and today's entry are untouched regardless
        of what this call does. Returns False (and changes nothing) when
        no entry with this id exists any more, e.g. it aged out of
        `MAX_HISTORY`.
        """
        with self._lock:
            for entry in self.history:
                if entry.id == entry_id:
                    entry.markdown = f"{entry.markdown}\n\n{addition}".strip()
                    return True
            return False

    def history_title(self, entry_id: str) -> str:
        """The title of a history entry by id, or "" if it is gone."""
        with self._lock:
            for entry in self.history:
                if entry.id == entry_id:
                    return entry.title
            return ""

    def retitle(self, entry_id: str, old: str, new: str) -> bool:
        """Replaces a PROVISIONAL title in place (`titles` of the topics block).

        Only when the entry still carries `old` -- a title somebody else
        changed meanwhile is never overwritten. When that entry is the one
        on display, the headline follows. Returns whether it changed.
        """
        new_text = _text(new)
        if new_text is None:
            return False
        with self._lock:
            for entry in self.history:
                if entry.id == entry_id:
                    if entry.title != old:
                        return False
                    entry.title = one_line(new_text)
                    if self.history and self.history[-1] is entry:
                        self.headline = entry.title
                    return True
            return False

    def _push(self, entry: Main) -> None:
        """Put one surface on display and into the history.

        NOTHING IS EVER CUT FROM THE BACK. There is no pointer here to cut
        against -- it lives in the browser -- and that is not an omission
        but the point of N4: as long as every surface stays reachable,
        replacing one is reversible, and only then is the automatic change
        of N1 defensible at all. The cap takes the OLDEST entry, the one
        the round is furthest past.
        """
        self.history.append(entry)
        del self.history[:-MAX_HISTORY]
        self._surface_at = entry.set_at
        self.image = None

    def _surface(self, title: str, markdown: str, quote: str) -> Main:
        """One surface with its identity and its clock.

        A3 (operator, 2026-09-21): no line-count cap any more -- the whole
        markdown goes onto the surface, `collapsed_lines`/
        `collapsed_markdown` stay at their zero default. Scrolling is the
        accepted fallback for a long surface, not a failure to hide part
        of it.
        """
        return Main(
            id=self._next_id("M"),
            title=title,
            markdown=markdown,
            quote=quote,
            set_at=self._now(),
            set_at_label=self._label(),
        )

    def _next_id(self, prefix: str) -> str:
        """Identities are assigned here, never by the agent, and never
        reused -- a reused id would let a stale reference resolve the
        wrong entry."""
        self._counters[prefix] += 1
        return f"{prefix}{self._counters[prefix]}"

    def _label(self) -> str:
        """A display timestamp WITH its UTC offset.

        The browser counts the ages itself, via `Date.parse`. A naive
        `2026-09-08T19:09:00` is read there as the VIEWER's local time --
        and this round meets online. A reader one hour east would compute
        every notice as an hour old, i.e. instantly past the follow-up
        threshold: every line permanently in the warning colour with
        "steht seit N min, Robo prüft nach". The anti-stalling signal
        would become constant noise for exactly the remote audience.
        """
        stand = self._clock()
        if stand.tzinfo is None:
            # Naive means "local time on the meeting machine" -- astimezone()
            # attaches that offset without shifting the wall-clock reading.
            stand = stand.astimezone()
        return stand.isoformat(timespec="seconds")

    def _open_notices(self) -> list[Notice]:
        return [n for n in self.notices if n.state == "open"]

    def _park(self, text: str) -> str:
        """Park one entry: count it, keep it long enough to be shown, and
        hand it on to be written down.

        "Nichts verschwindet ersatzlos" also holds here: a parked entry is
        shown as a dimmed line for the receipt linger time, so the room
        sees that something was held back and where it went. Only the
        counter is kept forever; tick() drops the entry itself.

        The dimmed line claims the content is under `## Offene Fragen`.
        That claim is only true because the text goes into `_unrecorded`
        here: the consumer puts it into the next prompt and the agent, who
        owns the protocol, writes it there.
        """
        self.parked += 1
        self.parked_entries.append(Parked(text=text, parked_at=self._now(),
                                          parked_at_label=self._label()))
        self._remember_unrecorded("parked", text)
        return PARKED

    # ------------------------------------------------- what has to be filed
    def _remember_unrecorded(self, kind: str, text: str) -> None:
        """Note that this text still owes the protocol an appearance."""
        self._unrecorded.append({"kind": kind, "text": text})
        del self._unrecorded[:-MAX_UNRECORDED]
    def unrecorded(self) -> list[dict]:
        """A snapshot of what left the stage and is not in the protocol yet.

        A snapshot, not a drain: the prompt is built before the run, and a
        run can fail. Only `mark_recorded()`, after a run that came back,
        removes anything.
        """
        with self._lock:
            return list(self._unrecorded)

    def mark_recorded(self, entries: list[dict]) -> None:
        """Drop exactly the entries a successful run was told about.

        By identity, not by value: two identical notices can be parked
        minutes apart, and the second one still has to reach the protocol.
        """
        with self._lock:
            self._unrecorded = [e for e in self._unrecorded
                                if not any(e is done for done in entries)]

    # ------------------------------------------------------- announcements
    def announce(self, kind: str, label: str) -> str:
        """Announce that something of `kind` is about to reach the stage.

        Returns the new id, or "" when the announcement was refused. The
        label is a short German phrase: a prefix plus, by design (MM-L),
        up to three content words of what was said (`shared.minutes_format.pending_label`).
        It goes into buehne.json only, and nothing but the page ever reads
        it. The trace gets the id and the kind only, never the label.

        A fourth announcement displaces the OLDEST: the newest is what the
        round is waiting for right now.
        """
        with self._lock:
            if kind not in PENDING_KINDS:
                return ""
            text = _text(label)
            # Stripped AFTER the cut: a cut that lands on a space would
            # otherwise leave one hanging at the end of the line.
            text = one_line(text)[:MAX_PENDING_LABEL].rstrip() if text else ""
            if not text:
                return ""
            entry = Pending(id=self._next_id("P"), kind=kind, label=text,
                            since=self._now(), since_label=self._label())
            self.pending.append(entry)
            self._pending_events.append({"event": "pending_start",
                                         "id": entry.id, "kind": kind})
            while len(self.pending) > MAX_PENDING:
                self._end_pending(self.pending[0], "displaced")
            return entry.id

    def settle(self, pending_id: str, outcome: str = "arrived") -> bool:
        """End one announcement. False when there is no such one (any more).

        `outcome` is a token for the trace -- `arrived`, `unanswered`,
        whatever the caller names -- never text.
        """
        with self._lock:
            for entry in self.pending:
                if entry.id == pending_id:
                    self._end_pending(entry, outcome)
                    return True
            return False

    def _settle_oldest(self, kind: str, outcome: str) -> bool:
        for entry in self.pending:
            if entry.kind == kind:
                self._end_pending(entry, outcome)
                return True
        return False

    def _end_pending(self, entry: Pending, outcome: str) -> None:
        """Remove one announcement and queue its `pending_end`. No label."""
        self.pending.remove(entry)
        self._pending_events.append({
            "event": "pending_end", "id": entry.id, "kind": entry.kind,
            "outcome": outcome,
            "duration_s": round(self._now() - entry.since, 1)})

    # -------------------------------------------------------------- apply
    def apply(self, action: dict, spoken: str = "") -> str:
        """Apply one proposal action. `spoken` is the text of this run.

        Returns "" when accepted, otherwise a short English reason.

        A rejected action changes nothing the surface already shows -- a
        malformed proposal must not damage what the round is reading.
        There is ONE exception, and it is a rejection only from the
        agent's side: `PARKED`. The notice found no free slot, so it does
        not become a notice; it is counted, shown briefly as a dimmed
        line, and handed to `unrecorded()` for the protocol. Callers that
        count surface changes have to count that one (see
        `StageWriter.ingest()`).
        """
        if not isinstance(action, dict):
            return "unknown action"
        kind = action.get("action")
        handler = {
            "none": self._none,
            "mirror": self._mirror,
            "call_answer": self._call_answer,
            "headline": self._headline,
            "main": self._main,
            "topic": self._topic,
            "graph": self._graph,
            "decision": self._decision,
            "notice": self._notice,
            "question": self._question,
            "links": self._links,
            "resolve": self._resolve_action,
            "image": self._image,
        }.get(kind if isinstance(kind, str) else "")
        if handler is None:
            return "unknown action"
        with self._lock:
            reason = handler(action, spoken)
            # Only an ACCEPTED action is an arrival. A parked question
            # comes back as `PARKED`, so it keeps its announcement: what
            # the room was told is coming did not come.
            if not reason and kind in _ARRIVES_AS:
                self._settle_oldest(_ARRIVES_AS[kind], "arrived")
            return reason

    def _none(self, action: dict, spoken: str) -> str:
        return ""

    def _mirror(self, action: dict, spoken: str) -> str:
        # An explicit "" clears the mirror -- the lane's own expiry (live
        # finding 4) writes exactly that. Every other value still goes
        # through _text()'s scrub; only the literal empty string skips it,
        # since clearing is the one case where "no usable text" IS the
        # intended value, not a malformed proposal.
        if action.get("text") == "":
            self.mirror = ""
            self.image = None
            return ""
        value = _text(action.get("text"))
        if value is None:
            return "no text"
        self.mirror = value
        self.image = None
        return ""

    def _call_answer(self, action: dict, spoken: str) -> str:
        # Same "explicit empty string clears it" exception as `_mirror`,
        # for the same reason: the lane's own expiry
        # (call_lane.CALL_ANSWER_VISIBLE_S) writes exactly that.
        if action.get("text") == "":
            self.call_answer = ""
            return ""
        value = _text(action.get("text"))
        if value is None:
            return "no text"
        self.call_answer = value
        return ""

    def _image(self, action: dict, spoken: str) -> str:
        """Show one image large. The caller (lane integration) validated
        the path; here only the id shape and the caption are checked."""
        image_id = _text(action.get("id"))
        if image_id is None or not re.fullmatch(r"[0-9a-f]{16}", image_id):
            return "no image id"
        caption = one_line(_text(action.get("caption")) or "")[:160]
        self.image = {"id": image_id, "caption": caption,
                      "set_at": self._now(),
                      "set_at_label": self._label()}
        return ""

    def _headline(self, action: dict, spoken: str) -> str:
        value = _text(action.get("text"))
        if value is None:
            return "no text"
        self.headline = value
        return ""

    def _main(self, action: dict, spoken: str) -> str:
        """Replace the main area. Only on a call-out, evidenced by `quote`.

        Only the agent can recognise a call-out (a form of address OR a
        second-person imperative). The consumer does not judge language --
        it demands the instruction verbatim. Without it the action is
        discarded and the main area stays exactly as it was, which is the
        whole point: on a surface, moving is the interruption.

        And the evidence is CHECKED against what was actually said in this
        run. A quote nobody asked for is not evidence, it is a plausible
        sentence: any non-empty string used to buy the whole main area,
        every run, which is exactly the disruption the design exists to
        prevent. No spoken text, no replacement -- an unverifiable claim
        is not a call-out.

        The rule is untouched by the automatic topic change beside it. An
        ORDER waits for nothing: this path passes both brakes of `_topic()`
        and restarts the minimum hold time for the next one (`_push()`),
        because the surface it just set is the one that now has to stand.
        """
        markdown = _text(action.get("markdown"))
        if markdown is None:
            return "no markdown"
        quote = _text(action.get("quote"))
        if quote is None:
            return "no quote"
        if not quote_is_spoken(quote, spoken):
            return "quote not spoken"
        # A call-out carries no title -- and none is invented here. The
        # label the history needs is READ OFF the surface itself.
        self._push(self._surface(derived_title(markdown), markdown, quote))
        return ""

    def _brake(self, record: bool = True) -> str:
        """The two brakes of N5, for every AUTOMATIC way onto the surface.

        Shared by `_topic()` and `_graph()` on purpose: a graph that
        replaces the surface is the same movement as a topic change, and
        two copies of a pace rule drift apart. A call-out passes both --
        an order waits for nothing. `record=False` only answers -- it
        leaves the window as it is (`brake_check()`).
        """
        now = self._now()
        # The minimum hold time protects what is UP. With nothing up there
        # is nothing to protect.
        if self.history and now - self._surface_at < TOPIC_MIN_HOLD_S:
            return TOO_SOON
        recent = [t for t in self._topic_at if now - t < TOPIC_WINDOW_S]
        self._topic_at = recent
        if len(recent) >= TOPIC_MAX_PER_WINDOW:
            return TOO_MANY
        if record:
            self._topic_at = [*recent, now]
        return ""

    def brake_check(self) -> str:
        """Would an automatic surface change be braked right now?

        Level 2 (blocks/decisions_tasks_questions/policy.py) asks BEFORE it orders a topic block,
        so it does not announce a change the brake would refuse anyway.
        The same rule, not a copy: `_brake(record=False)`.
        """
        with self._lock:
            return self._brake(record=False)

    def _topic(self, action: dict, spoken: str) -> str:
        """Replace the main area because the TOPIC changed. No call-out.

        The second way onto the main area, and the one N1 asks for: a
        surface stuck on topic 1 while the room discusses topic 10 is
        itself a disruption, and taking the mouse to fix it costs the
        flow. Recognising the change is the agent's job, exactly as with a
        call-out -- nothing else in this process can.

        WHAT REPLACES THE QUOTE. Nothing, and that is deliberate: nobody
        gave an order, so there is no order to quote, and demanding one
        would make the agent invent it. What takes its place is not proof
        but PACE (N5) -- a title, which is the history entry, plus two
        brakes. Without them a flighty agent is a flickering screen, which
        is the disruption section 4.1 measured and this change does not
        get to reintroduce.

        Refusal is not failure. Both reasons say the surface is still
        holding something the round has not had long enough.
        """
        title = _text(action.get("title"))
        if title is None:
            return "no title"
        markdown = _text(action.get("markdown"))
        if markdown is None:
            return "no markdown"
        gebremst = self._brake()
        if gebremst:
            return gebremst
        self._push(self._surface(one_line(title), markdown, quote=""))
        return ""

    def confirmed_topic(self, title: str, markdown: str) -> str:
        """A topic change CONFIRMED by the lane hysteresis
        (blocks/topics/hysteresis.py: p >= threshold AND a minimum topic
        age) -- always pushed, the N5 brakes are not asked again.

        Not reachable through `apply()` on purpose: an agent proposal is
        an unconfirmed claim and keeps both brakes. This path is already
        paced by the hysteresis (>= 90 s between changes live); braking it
        a second time lost both confirmed topics of the 2026-10-20 replay
        ("too soon" behind a call's own entry). The change is still
        recorded in the brake's window, so automatic proposals right after
        it stay braked.
        """
        title_text = _text(title)
        if title_text is None:
            return "no title"
        body = _text(markdown) or title_text
        with self._lock:
            self._push(self._surface(one_line(title_text), body, quote=""))
            now = self._now()
            self._topic_at = [t for t in self._topic_at
                              if now - t < TOPIC_WINDOW_S] + [now]
            self._settle_oldest("topic", "arrived")
        return ""

    def _graph(self, action: dict, spoken: str) -> str:
        """Put the vault graph up. The THIRD way onto the main area.

        The payload arrives FINISHED, from the consumer: this module is
        pure data plus deterministic rules, it opens no file and scans no
        vault. What it does own is the pace -- the same two brakes as a
        topic change -- and the history, because paging back has to show
        the graph of then (entwurf-graph-schirm section 4).
        """
        title = _text(action.get("title"))
        if title is None:
            return "no title"
        graph = action.get("graph")
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"),
                                                         list):
            return "no graph"
        gebremst = self._brake()
        if gebremst:
            return gebremst
        entry = self._surface(one_line(title), markdown="", quote="")
        self._push(Main(id=entry.id, title=entry.title, markdown="",
                        quote="", set_at=entry.set_at,
                        set_at_label=entry.set_at_label,
                        kind="graph", graph=graph))
        return ""

    def _decision(self, action: dict, spoken: str) -> str:
        value = _text(action.get("text"))
        if value is None:
            return "no text"
        when = _text(action.get("time")) or self._clock().strftime("%H:%M")
        self.decisions.append(Decision(id=self._next_id("D"), time=when,
                                       text=value, added_at_label=self._label()))
        # Oldest drops out. It is not lost: the full history stays in the
        # protocol under `## Entscheidungen`. The stage is the excerpt.
        while len(self.decisions) > MAX_DECISIONS:
            self.decisions.pop(0)
        return ""

    def _notice(self, action: dict, spoken: str) -> str:
        value = _text(action.get("text"))
        if value is None:
            return "no text"
        kind = _text(action.get("kind"))
        if kind not in NOTICE_KINDS:
            return "unknown kind"
        if len(self._open_notices()) >= MAX_NOTICES:
            # "An agent that flags three things at once has stopped being
            # a tool." The content goes to `## Offene Fragen` instead.
            return self._park(value)
        self.notices.append(Notice(id=self._next_id("N"), kind=kind, text=value,
                                   raised_at=self._now(),
                                   raised_at_label=self._label()))
        return ""

    def _question(self, action: dict, spoken: str) -> str:
        value = _text(action.get("text"))
        if value is None:
            return "no text"
        if self.question is not None and self.question.state == "open":
            return self._park(value)
        now = self._now()
        if self.question is not None:
            # A settled question (resolved or expired) is still a receipt
            # on display. Overwriting it here would make it vanish
            # without the event tick() would otherwise have emitted for
            # it -- so emit that event now, and let tick() carry it out.
            # The new question is accepted regardless: a six-second
            # display artefact must not cost the round a real question.
            self._pending_events.append({
                "event": "question_cleared",
                "id": self.question.id,
                "state": self.question.state,
                "age_s": round(now - self.question.raised_at, 1),
            })
        self.question = Question(id=self._next_id("Q"), text=value,
                                 raised_at=now,
                                 raised_at_label=self._label())
        return ""

    def _links(self, action: dict, spoken: str) -> str:
        items = action.get("items")
        if not isinstance(items, list):
            return "no items"
        clean: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("file"))
            if name is None:
                continue
            clean.append({"file": name, "label": _text(item.get("label")) or name})
        self.links = clean
        return ""

    # -------------------------------------------------------- resolution
    def _resolve_action(self, action: dict, spoken: str) -> str:
        entry_id = _text(action.get("id"))
        if entry_id is None:
            return "no id"
        return self.resolve(entry_id, _text(action.get("text")) or "")

    def resolve(self, entry_id: str, text: str) -> str:
        """Turn an open notice or the question into a visible receipt.

        Not silent: a message that simply vanishes is indistinguishable
        from a swallowed run -- the exact cause the analysis names for
        "bist du fertig?". The receipt stays in the same slot, which also
        moves nothing: removing a line pulls the whole main area up.
        """
        with self._lock:
            for entry in [*self.notices, self.question]:
                if entry is None or entry.id != entry_id:
                    continue
                if entry.state != "open":
                    return "not open"
                entry.state = "resolved"
                entry.resolution = text
                entry.settled_at = self._now()
                entry.follow_up = False
                return ""
            return "unknown id"

    def open_entries(self) -> list:
        """A snapshot of everything still open: notices first, question last.

        A snapshot, and taken under the lock, because the caller (the
        prompt builder in the consumer) reads this from the agent thread
        while the ticker is sweeping the very same lists.
        """
        with self._lock:
            entries: list = [n for n in self.notices if n.state == "open"]
            if self.question is not None and self.question.state == "open":
                entries.append(self.question)
            return entries

    # ------------------------------------------------------------- clocks
    def tick(self) -> list[dict]:
        """Advance the clocks. Returns what changed, shaped for the trace.

        The consumer calls this regularly. It never talks to the agent --
        every decision here is a duration comparison.

        Called from the ticker AND from the agent thread, hence the lock:
        both sweep the same settled entry, and the loser of an unguarded
        race got a `ValueError` out of `list.remove()`. In the agent
        thread that error made a successful turn look failed.
        """
        with self._lock:
            # Announcements first: `_end_pending()` queues into the event
            # buffer, which is drained right below.
            now = self._now()
            for entry in list(self.pending):
                if now - entry.since >= PENDING_TIMEOUT_S:
                    self._end_pending(entry, "discarded")
            events: list[dict] = list(self._pending_events)
            self._pending_events.clear()
            if (self.image is not None
                    and now - self.image["set_at"] >= IMAGE_TTL_S):
                events.append({"event": "image_cleared",
                               "id": self.image["id"],
                               "age_s": round(now - self.image["set_at"], 1)})
                self.image = None

            for notice in list(self.notices):
                if (notice.state == "open" and not notice.follow_up
                        and now - notice.raised_at >= FOLLOW_UP_S):
                    notice.follow_up = True
                    events.append({"event": "notice_follow_up", "id": notice.id,
                                   "age_s": round(now - notice.raised_at, 1)})
                if (notice.state != "open"
                        and now - notice.settled_at >= RECEIPT_LINGER_S):
                    self.notices.remove(notice)
                    events.append({"event": "notice_cleared", "id": notice.id,
                                   "state": notice.state,
                                   "age_s": round(now - notice.raised_at, 1)})

            for entry in list(self.parked_entries):
                if now - entry.parked_at >= RECEIPT_LINGER_S:
                    # The parked line is a receipt like any other: shown
                    # briefly, then removed. It carries no id -- nothing can
                    # reference it -- so the event names none.
                    self.parked_entries.remove(entry)
                    events.append({"event": "parked_cleared",
                                   "age_s": round(now - entry.parked_at, 1)})

            q = self.question
            if q is not None:
                if (q.state == "open" and not q.follow_up
                        and now - q.raised_at >= FOLLOW_UP_S):
                    q.follow_up = True
                    events.append({"event": "question_follow_up", "id": q.id,
                                   "age_s": round(now - q.raised_at, 1)})
                if q.state == "open" and now - q.raised_at >= QUESTION_EXPIRY_S:
                    # An unanswered question loses its addressee: the round
                    # has moved on. It goes to `## Offene Fragen` and the
                    # stage is cleared. A NOTICE has no such timer -- a
                    # deadline conflict does not stop being one after ten
                    # minutes.
                    q.state = "expired"
                    q.settled_at = now
                    q.follow_up = False
                    # Same duty as a parked entry: the dimmed line says the
                    # question now lives under `## Offene Fragen`, and that
                    # is only true if somebody writes it there.
                    self._remember_unrecorded("expired", q.text)
                    events.append({"event": "question_expired", "id": q.id,
                                   "age_s": round(now - q.raised_at, 1)})
                elif (q.state != "open"
                      and now - q.settled_at >= RECEIPT_LINGER_S):
                    self.question = None
                    events.append({"event": "question_cleared", "id": q.id,
                                   "state": q.state,
                                   "age_s": round(now - q.raised_at, 1)})
            return events

    def follow_up_note(self) -> str:
        """The German prompt supplement for entries that got stuck.

        Appended to the NEXT run that is due anyway -- never an extra run,
        never a timer-triggered one. Evidence: F12, two spellings of one
        name still standing side by side unresolved.

        The German here is the prompt's ASCII transliteration ("Gespraech",
        "pruefe"), like the rest of the prompt: this text goes nowhere else
        -- `content()` exports only the `follow_up` flag, and the page
        writes its own wording.
        """
        with self._lock:
            stuck = [e for e in self.open_entries() if e.follow_up]
            now = self._now()
        if not stuck:
            return ""
        lines = ["", "🔁 NACHFASSEN:"]
        for entry in stuck:
            minutes = int((now - entry.raised_at) // 60)
            lines.append(
                f"Diese Meldung steht seit {minutes} Minuten: "
                f"{entry.id} — {entry.text}. Geh das bisherige Gespraech "
                f"durch und pruefe, ob sie laengst beantwortet ist. Ist sie "
                f"es, loese sie auf.")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------ content
    def content(self) -> dict:
        """The content fields of `buehne.json`. The consumer adds `status`.

        Ages are NOT baked in: the file would then have to be rewritten
        every second and the fingerprint would move on every poll. The
        page receives the raising timestamps plus the thresholds and does
        the counting itself.

        Under the lock: without it this read walks the same lists a
        concurrent `tick()` is removing from, and the writer would publish
        a stage assembled halfway through a sweep.
        """
        with self._lock:
            return self._content()

    def _content(self) -> dict:
        main = self.main
        content = self._base_content(main)
        # Only ADDED, and only when a surface rendered them: key absent
        # means "section hidden" for the page.
        for key, value in (("queue", self.queue), ("voice", self.voice),
                           ("share", self.share),
                           ("session_graph", self.session_graph),
                           ("knowledge", self.knowledge),
                           ("listen", self.listen),
                           ("transcript_partial", self.transcript_partial),
                           ("tasks", self.tasks),
                           ("chat", self.chat)):
            if value is not None:
                content[key] = value
        return content

    def _base_content(self, main: Main) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "updated": self._label(),
            "mirror": self.mirror,
            # Only ADDED (see SCHEMA_VERSION).
            "call_answer": self.call_answer,
            "headline": self.headline,
            # `main` STAYS, unchanged, and it is the last history entry.
            # The page and its tests hang off it, and a stage running from
            # before the history has to keep working against a file this
            # consumer writes -- which is also why the version did not move.
            "main": {
                "id": main.id,
                "markdown": main.markdown,
                "set_at": main.set_at_label,
                "collapsed_lines": main.collapsed_lines,
                "collapsed_markdown": main.collapsed_markdown,
            },
            # Every position in full, so the page can page through it
            # without asking again -- it has no second endpoint, and a
            # fetch per step would put the network between the reader and
            # a key press. No position travels with it: the pointer is the
            # browser's.
            "history": [
                _history_row(m, ist_neuste=(i == len(self.history) - 1))
                for i, m in enumerate(self.history)
            ],
            "decisions": [
                # `added_at` is what makes the "neu" mark possible: the
                # page compares it against its own clock and drops the
                # mark after a few seconds. Marking it here instead would
                # move the fingerprint on every poll.
                {"id": d.id, "time": d.time, "text": d.text,
                 "added_at": d.added_at_label}
                for d in self.decisions
            ],
            "notices": [
                {"id": n.id, "kind": n.kind, "text": n.text,
                 "raised_at": n.raised_at_label, "state": n.state,
                 "resolution": n.resolution, "follow_up": n.follow_up}
                for n in self.notices
            ],
            "question": (
                {"id": self.question.id, "text": self.question.text,
                 "raised_at": self.question.raised_at_label,
                 "state": self.question.state,
                 "resolution": self.question.resolution,
                 "follow_up": self.question.follow_up}
                if self.question is not None else None
            ),
            "links": list(self.links),
            "parked": self.parked,
            "parked_entries": [
                {"text": p.text, "parked_at": p.parked_at_label}
                for p in self.parked_entries
            ],
            # Only ADDED, so the version stays (see SCHEMA_VERSION). The
            # page animates on the id and shows the label; `since` is a
            # label, not an age, for the same reason as `raised_at`.
            "pending": [
                {"kind": p.kind, "label": p.label, "since": p.since_label,
                 "id": p.id}
                for p in self.pending
            ],
            "age_visible_after_s": int(AGE_VISIBLE_S),
            "follow_up_after_s": int(FOLLOW_UP_S),
            # Only ADDED (see SCHEMA_VERSION): an image on request, by id.
            "image": ({"id": self.image["id"],
                       "caption": self.image["caption"],
                       "set_at": self.image["set_at_label"]}
                      if self.image is not None else None),
            # None for every session without an `agenda.md` -- unchanged
            # from before this field existed, so an old stage page and a
            # session without the file see exactly the same payload shape.
            "agenda": self.agenda,
            # Always present (never key-absent-means-hidden, unlike the
            # region fields in `_content()` below): an empty list already
            # means "nothing finished yet", not "no live-transcript block".
            "transcript_recent": list(self.transcript_recent),
        }

    # ------------------------------------------------------------ restore
    def restore(self, data: dict) -> dict:
        """Take a previously written `buehne.json` content back in.

        After a crash the consumer starts again mid-meeting. Publishing a
        fresh state would blank the main area, the decisions, the notices
        and the question in front of the round -- while the worker
        re-attaches to the SAME agent session and still remembers the old
        identities. Two things therefore matter here, and the second one
        matters more:

        1. what can be restored is restored -- everything the round can
           see, plus the parked counter;
        2. no identity is ever handed out twice. The counters are lifted
           above every id that appears in the file, INCLUDING the ones
           whose entry is not restored: a stale `resolve` from the agent's
           memory must never settle a different entry than the one it
           means.

        Only OPEN entries come back. A receipt is a six-second display
        artefact; restoring one would put a resolution from before the
        crash back on the stage with a fresh linger.

        Ages survive: the entries carry a wall-clock label, so the age is
        recomputed against the monotonic clock of this process. Without
        that a notice raised twenty minutes ago would start its follow-up
        countdown again, and a question ten minutes overdue would sit in
        the single slot for another ten.

        Returns a small structural summary for the log and the trace.
        """
        with self._lock:
            return self._restore(data)

    def _restore(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("stage file is not an object")
        if data.get("version") != SCHEMA_VERSION:
            raise ValueError(f"stage file has version {data.get('version')!r}")
        now = self._now()

        def age_of(label: object) -> float:
            """Seconds since `label`, or 0.0 when it cannot be read."""
            try:
                stamp = datetime.fromisoformat(str(label))
            except (TypeError, ValueError):
                return 0.0
            reference = self._clock()
            if reference.tzinfo is None:
                reference = reference.astimezone()
            if stamp.tzinfo is None:
                stamp = stamp.astimezone()
            return max(0.0, (reference - stamp).total_seconds())

        def claim(entry_id: object) -> str:
            """Register an id so it can never be handed out again."""
            name = _text(entry_id) or ""
            match = re.fullmatch(r"([MDNQP])(\d+)", name)
            if match is None:
                return ""
            prefix, number = match.group(1), int(match.group(2))
            self._counters[prefix] = max(self._counters[prefix], number)
            return name

        summary = {"decisions": 0, "notices": 0, "question": 0}

        self.mirror = _text(data.get("mirror")) or ""
        self.call_answer = _text(data.get("call_answer")) or ""
        self.headline = _text(data.get("headline")) or ""

        def surface(row: object) -> Main | None:
            """One history entry out of the file, or None when unusable.

            A broken or partial entry is SKIPPED, never guessed at: an
            entry without an identity cannot be referenced, and one
            without markdown has nothing to show. Its id is claimed all
            the same -- the rule above holds for every id in the file, not
            only for the ones that come back.
            """
            if not isinstance(row, dict):
                return None
            entry_id = claim(row.get("id"))
            if row.get("kind") == "graph":
                # A graph position has no markdown -- and a graph row
                # without a payload is skipped like any other unusable
                # row. Its id was claimed above either way.
                graph = row.get("graph")
                if not entry_id or not isinstance(graph, dict):
                    return None
                return Main(
                    id=entry_id, title=one_line(_text(row.get("title")) or ""),
                    markdown="", quote="",
                    set_at=now - age_of(row.get("set_at")),
                    set_at_label=_text(row.get("set_at")) or self._label(),
                    kind="graph", graph=graph)
            text = _text(row.get("markdown"))
            if not entry_id or text is None:
                return None
            label = _text(row.get("title"))
            return Main(
                id=entry_id,
                # A file from before the titles has none. Deriving it is
                # the same operation `_main()` does, on the same content.
                title=one_line(label) if label else derived_title(text),
                markdown=text, quote="",
                set_at=now - age_of(row.get("set_at")),
                set_at_label=_text(row.get("set_at")) or self._label(),
                collapsed_lines=(row.get("collapsed_lines")
                                 if isinstance(row.get("collapsed_lines"), int)
                                 else 0),
                collapsed_markdown=_text(row.get("collapsed_markdown")) or "")

        main = data.get("main") if isinstance(data.get("main"), dict) else {}
        # Claimed before anything else is decided: `main` is the last
        # history entry in a file this version wrote, and the id of a
        # surface that does NOT come back must never be handed out again
        # either.
        claim(main.get("id"))
        rows = data.get("history")
        for row in (rows if isinstance(rows, list) else []):
            entry = surface(row)
            if entry is not None:
                self.history.append(entry)
        if not self.history:
            # No usable history: either a file from before it existed, or
            # one whose history did not survive. The displayed surface is
            # still under `main`, and it comes back as a history of one --
            # the round keeps looking at what it was looking at.
            entry = surface(main)
            if entry is not None:
                self.history.append(entry)
        del self.history[:-MAX_HISTORY]       # the cap holds after a restart
        if self.history:
            # The minimum hold time survives the restart, so a topic change
            # cannot walk straight through the restart into the surface the
            # round is reading. The per-window counter does NOT survive --
            # the file does not say which entry came from which path, and
            # inventing that here would be guessing. For the 45 s that the
            # hold time covers of the 60 s window the brake holds anyway.
            self._surface_at = self.history[-1].set_at

        for row in (data.get("decisions") or []):
            if not isinstance(row, dict):
                continue
            entry_id = claim(row.get("id"))
            text = _text(row.get("text"))
            if not entry_id or text is None:
                continue
            self.decisions.append(Decision(
                id=entry_id, time=_text(row.get("time")) or "",
                text=text,
                added_at_label=_text(row.get("added_at")) or self._label()))
            summary["decisions"] += 1
        del self.decisions[:-MAX_DECISIONS]     # the cap holds after a restart

        for row in (data.get("notices") or []):
            if not isinstance(row, dict):
                continue
            entry_id = claim(row.get("id"))
            text = _text(row.get("text"))
            kind = _text(row.get("kind"))
            if (not entry_id or text is None or kind not in NOTICE_KINDS
                    or row.get("state") != "open"
                    or len(self._open_notices()) >= MAX_NOTICES):
                continue
            self.notices.append(Notice(
                id=entry_id, kind=kind, text=text,
                raised_at=now - age_of(row.get("raised_at")),
                raised_at_label=_text(row.get("raised_at")) or self._label(),
                follow_up=bool(row.get("follow_up"))))
            summary["notices"] += 1

        question = data.get("question")
        if isinstance(question, dict):
            entry_id = claim(question.get("id"))
            text = _text(question.get("text"))
            if entry_id and text is not None and question.get("state") == "open":
                self.question = Question(
                    id=entry_id, text=text,
                    raised_at=now - age_of(question.get("raised_at")),
                    raised_at_label=(_text(question.get("raised_at"))
                                     or self._label()),
                    follow_up=bool(question.get("follow_up")))
                summary["question"] = 1

        links: list[dict] = []
        for item in (data.get("links") or []):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("file"))
            if name is None:
                continue
            links.append({"file": name,
                          "label": _text(item.get("label")) or name})
        self.links = links

        # Announcements are NOT restored: one from before the crash
        # promises a run that died with the old process. Their ids are
        # claimed all the same, so a page still animating `P3` never sees
        # a different announcement under that name.
        for row in (data.get("pending") or []):
            if isinstance(row, dict):
                claim(row.get("id"))

        # The running total of the meeting, not a display artefact: it is
        # what the event trace counts against. The parked LINES are not
        # restored -- they last six seconds and are long gone.
        if isinstance(data.get("parked"), int) and data["parked"] > 0:
            self.parked = data["parked"]

        return summary


# ========================================================= agenda + timer
# Ported from the live moderation ticker at
# ~/scratch/moderation.py (session 2026-10-20): it watched the
# protocol for "naechste Person"/"naechste Frage" by regex and served a
# tiny page with the current question, who is on, and a budget counter.
# The agenda clock and the round-robin list are now the `timer` and
# `speaker-queue` blocks (ADR 0010); what is left here is the parsing of
# `agenda.md` and the voice-command detection. Reading and ticking
# `agenda.md` and feeding the blocks is gateway/consumer.py's
# `AgendaController`.
#
# Voice commands are detected here by regex alone, in code, so they never
# reach the agent: a block that is never built is a turn that is never
# run (see the SSE loop in gateway/consumer.py).
AGENDA_RX_ITEM = re.compile(
    r"n(ä|ae|a)chste[rn]?\s+(punkt|frage)", re.I)
AGENDA_RX_PERSON = re.compile(
    r"n(ä|ae|a)chste[rn]?\s+(person|sprecher|runde)|du bist dran", re.I)
AGENDA_RX_BACK = re.compile(
    r"(eine?n?\s+punkt\s+)?zur(ü|ue|u)ck|vorherige[rn]?\s+(punkt|frage)",
    re.I)
AGENDA_RX_JUMP = re.compile(
    r"(?:gehe?\s+zu\s+|zur(?:ü|ue|u)ck\s+zu\s+|spring[et]?\s+zu\s+)?"
    r"punkt\s+(\d+)", re.I)

# Issue #201 (live trial): "naechster Punkt" fired mid-sentence ("der
# naechste Punkt ist dann ganz kurz") because the old code used
# `.search()`, matching the phrase anywhere. An explicit command is now
# only recognized when it IS the utterance -- a leading address (the wake
# name, with or without a greeting) or politeness word, and a trailing
# "bitte"/"danke", are stripped first; whatever survives must fullmatch
# one command, never just contain it.
_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:okay|ok|also|gut|hey|hallo|robo|bobo|hobo|probo|rubo|grobo)\b"
    r"[,.\-\s]*)+", re.I)
_TRAILING_FILLER_RE = re.compile(r"[,.\-\s]+(?:bitte|danke)\.?$", re.I)


def _explicit_command_text(text: str) -> str:
    """`text` with one leading address/politeness word and a trailing
    "bitte"/"danke" stripped -- everything else must be the command
    itself, see the module-level note on issue #201 above."""
    core = _LEADING_FILLER_RE.sub("", text)
    core = _TRAILING_FILLER_RE.sub("", core)
    return core.strip()


def detect_agenda_command(text: str) -> str | tuple[str, int] | None:
    """"item" for "naechster Punkt"/"naechste Frage", "person" for
    "naechste Person"/"naechster Sprecher"/"du bist dran", "back" for
    "zurueck"/"vorheriger Punkt", `("jump", n)` for "Punkt n"/"gehe zu
    Punkt n"/"zurueck zu Punkt n", else None.

    `fullmatch()`, not `.search()`: the command must BE the utterance
    (modulo a leading address/politeness word and a trailing "bitte"),
    never just a phrase that happens to occur somewhere inside a longer
    sentence -- see the module-level note on issue #201 above.
    """
    core = _explicit_command_text(text)
    if not core:
        return None
    m = AGENDA_RX_JUMP.fullmatch(core)
    if m:
        return ("jump", int(m.group(1)))
    if AGENDA_RX_ITEM.fullmatch(core):
        return "item"
    if AGENDA_RX_PERSON.fullmatch(core):
        return "person"
    if AGENDA_RX_BACK.fullmatch(core):
        return "back"
    return None


AGENDA_CHECKBOX_RE = re.compile(r"^-\s*\[([ xX])\]\s*(.+)$")
AGENDA_BUDGET_RE = re.compile(r"\((\d+)\s*min\)\s*$")
AGENDA_DAUER_RE = re.compile(r"^Dauer:\s*(\d+)\s*min", re.I)
AGENDA_ENDE_RE = re.compile(r"^Ende:\s*(\d{1,2}):(\d{2})", re.I)
AGENDA_START_RE = re.compile(r"^Start:\s*(\d{1,2}):(\d{2})", re.I)


@dataclass
class AgendaItem:
    text: str
    budget_s: int | None
    done: bool


@dataclass
class AgendaDoc:
    items: list[AgendaItem]
    duration_min: int | None = None
    end_time: tuple[int, int] | None = None
    # The meeting's PLANNED start (issue #203), parallel to `end_time`:
    # the operator's `Start: HH:MM`, so the timer can anchor on the plan
    # instead of the moment the service happens to come up.
    start_time: tuple[int, int] | None = None


def parse_agenda(markdown: str) -> AgendaDoc:
    """`agenda.md`: a checkbox list, an optional `(N min)` budget per item
    and optional `Dauer: N min` / `Ende: HH:MM` / `Start: HH:MM` headers for
    the whole meeting. Never raises -- an unreadable line is simply not an
    item."""
    items: list[AgendaItem] = []
    duration_min: int | None = None
    end_time: tuple[int, int] | None = None
    start_time: tuple[int, int] | None = None
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = AGENDA_CHECKBOX_RE.match(line)
        if m:
            done = m.group(1).lower() == "x"
            body = m.group(2).strip()
            budget_s = None
            bm = AGENDA_BUDGET_RE.search(body)
            if bm:
                budget_s = int(bm.group(1)) * 60
                body = AGENDA_BUDGET_RE.sub("", body).strip()
            items.append(AgendaItem(text=body, budget_s=budget_s, done=done))
            continue
        dm = AGENDA_DAUER_RE.match(line)
        if dm:
            duration_min = int(dm.group(1))
            continue
        em = AGENDA_ENDE_RE.match(line)
        if em:
            end_time = (int(em.group(1)), int(em.group(2)))
            continue
        sm = AGENDA_START_RE.match(line)
        if sm:
            start_time = (int(sm.group(1)), int(sm.group(2)))
    return AgendaDoc(items=items, duration_min=duration_min, end_time=end_time,
                     start_time=start_time)


def meeting_deadline(doc: AgendaDoc, started_at: float) -> float | None:
    """The epoch second the meeting is due to end, or None.

    `Dauer` wins over `Ende` when both are given. Pure given `started_at`
    -- the caller decides what "now" means (the moment `agenda.md` was
    first read), consistent with the rest of this module's injected
    clocks.
    """
    if doc.duration_min is not None:
        return started_at + doc.duration_min * 60
    if doc.end_time is not None:
        hour, minute = doc.end_time
        base = datetime.fromtimestamp(started_at).astimezone()
        return base.replace(hour=hour, minute=minute, second=0,
                            microsecond=0).timestamp()
    return None


def planned_start(doc: AgendaDoc, fallback: float) -> float:
    """The epoch second the meeting is PLANNED to start (issue #203), or
    `fallback` when `agenda.md` carries no `Start:` header.

    `fallback` is also the date `Start`'s HH:MM is read against -- the
    caller decides what "now" means, same as `meeting_deadline()`.
    """
    if doc.start_time is None:
        return fallback
    hour, minute = doc.start_time
    base = datetime.fromtimestamp(fallback).astimezone()
    return base.replace(hour=hour, minute=minute, second=0,
                        microsecond=0).timestamp()
