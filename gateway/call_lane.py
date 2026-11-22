#!/usr/bin/env python3
# CoThinker — gateway/call_lane.py: the call lane of the session host
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The call lane: answers when the room addresses the assistant by its wake
word (core:agent, session host). `CallLane` is the one object
`gateway/consumer.py --lanes` adds.

It owns one `gateway.lanes.LaneQueue` wrapping its own
`gateway.worker.WorkerClient` process and one
`blocks.mirroring_coach.context.LaneContext` window. The coach lane, its
rhythm and its Nachtrag logic are the `mirroring-coach` block
(`blocks/mirroring_coach/coach.py`); the two meet only through the block
host:

- Robo's own output (the call's acknowledgement, the call's result) is put
  into the coach's window as an ordinary speaker line -- a `block_output`
  event with `robo_line` -- never agent to agent. The coach's contribution
  comes back as a `mirror:` view intent (`observe_intent`) and is appended
  to the call window the same way.
- Confirmed topic changes arrive from the `topics` block through the block
  host's `on_output` listener (`CallLane.on_topic`); titling is that
  block's job. The call lane gives a new topic its history entry on the
  stage (point 6a) and announces it to the blocks as a `block_output`
  with `topic` (`from` = `session-host`) carrying the stage entry's id.

Kept out of `gateway/consumer.py` on purpose: that file is huge and runs
live meetings. Writing to the stage reuses the SAME `state`/`writer` the
rest of the consumer owns -- never a second writer, per AGENTS.md's
single-writer rule for `buehne.json`.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.wake_word import call_ack, call_intent  # noqa: E402
from correction_prefilter import prefilter  # noqa: E402
from blocks.mirroring_coach.context import ROLL_EVERY, WINDOW_SIZE, LaneContext  # noqa: E402
from lanes import TIMEOUT_MARKER, Job, LaneQueue  # noqa: E402
import stage_image  # noqa: E402
from worker import WorkerClient  # noqa: E402

# Point 2 (2026-10-20): the call lane gets read-only file tools -- this
# is a PROMPT instruction, not a Hermes-config limit: the call lane
# reuses ~/.hermes/profiles/cothinker, which the live serial (variant C)
# path may ALSO use tonight if --lanes does not pass acceptance; a
# config-level tool-call cap there would silently change variant C's
# behaviour too. The hard backstop against a stuck turn is therefore the
# TIMEOUT + active worker kill below, not this instruction alone.
# Per the operator, 2026-10-20: the call lane may WRITE on an explicit call. Prompt-level
# rules only (no technical lock): project folder freely, following its
# AGENTS.md filing rules, never the protocol (the consumer owns it); outside
# the project folder only after a warning and an explicit confirmation call.
CALL_TOOL_BUDGET_NOTE = (
    "Hoechstens 6 Werkzeugaufrufe in diesem Zug. Schreiben nur auf "
    "ausdruecklichen Zuruf (\"Robo, trag ... ein\"): im Projektordner frei, "
    "nach dessen AGENTS.md-Ablageregeln (sitzungen/ mit Datum, status/ als "
    "lebende Dateien ohne Datum, keine neuen Dateien im Wurzelordner); die "
    "Protokolldatei nie (die fuehrt der Konsument). Ausserhalb des "
    "Projektordners (auch Zusatzquellen) NICHT schreiben, sondern antworten: "
    "\"Achtung: Datei liegt außerhalb des Projekts (<Pfad>) — soll ich "
    "wirklich ändern?\" und erst nach einem ausdruecklichen "
    "Bestaetigungs-Zuruf schreiben.")

# Live test 2026-10-20 17:14: "Robo, strukturier das" came back as a wall
# of text about what Robo had read and not written (buehne.json,
# vorschlag.jsonl -- leftovers of the variant-C role in the profile's
# AGENTS.md). The answer IS the stage content: result only, no report.
CALL_ANSWER_RULE = (
    "Deine Antwort steht woertlich auf der Buehne. Schreib NUR das "
    "Ergebnis fuer die Runde, nie, was du gelesen, geprueft oder nicht "
    "getan hast. buehne.json und vorschlag.jsonl brauchst du nicht. "
    "Kurz: hoechstens 600 Zeichen, ausser die Runde will ausdruecklich mehr.")

# Per-intent shape of the answer (wake_word.CALL_INTENTS decides the intent).
CALL_INTENT_SHAPES = {
    "structure": (
        "Form: die grossen Themen der Runde als fette Zeilen (**Thema**), "
        "darunter je 2-4 vorgeschlagene Unterschritte als '- ' Liste, in "
        "sinnvoller Reihenfolge. Nur Themen, die genannt wurden; Unklares "
        "als letzte Zeile 'Offen: ...'. Keine Einleitung, kein Schlusssatz. "
        "Vorher den Stand pruefen: je Thema hoechstens eine passende Datei "
        "im Projektordner lesen (status/offene-punkte.md oder der Ordner "
        "des Themas unter sitzungen/), und nur Schritte vorschlagen, die "
        "laut Stand noch offen sind -- nie etwas, das schon erledigt ist."),
    "summarize": (
        "Form: hoechstens 5 '- ' Punkte: was gesagt, entschieden, offen ist. "
        "Nur Gesagtes, keine Namen bei 'unbekannt'. Keine Einleitung."),
}

# Point 2: hard per-turn budgets. On expiry the lane does not just stop
# WAITING (gateway.lanes.LaneQueue does that on its own) -- it actively
# kills the worker process, so the NEXT job gets a fresh one instead of
# queueing behind a turn that might never return at all (live incident:
# a hung turn ran until the surrounding 900s timeout).
CALL_TURN_TIMEOUT_S = 120.0
# Call lane only (it has read tools; the coach lane has none): where the
# project's files and the protocol live, so "Robo, was steht in
# kosten.md?" can find them. Part of the STABLE prefix -- fixed per meeting.
CALL_PATHS_TEMPLATE = (
    "Projektordner: {folder}\n"
    "Protokoll: {protocol}\n"
    "Lies Dateien nur gezielt aus dem Projektordner.")
# One line per COTHINKER_EXTRA_DIRS entry (read-only reference folders),
# plus the rule once. Call lane only, stable prefix.
CALL_EXTRA_DIR_LINE = "Zusatzquelle (nur lesen): {path}"
CALL_EXTRA_DIRS_RULE = "Für Fragen zu CoThinker zuerst dort nachsehen."
# A reference folder may be a SYMLINK folder (links into the vault).
# search_files (ripgrep / find -H) does not descend into symlinked files,
# read_file does -- so the folder's files are named explicitly, once, in
# the stable prefix, and the agent reads them by path.
CALL_EXTRA_FILES_LINE = "  Dateien (direkt mit read_file lesen, search_files findet Links nicht): {names}"
CALL_EXTRA_MAX_FILES = 40
# Call lane only, stable prefix: the one structured marker the consumer
# understands in a call answer (gateway/stage_image.py).
CALL_IMAGE_RULE = ("Soll ein Bild gezeigt werden, schreib eine eigene Zeile "
                   "BILD: <absoluter Pfad>.")
CALL_EXTRA_IMAGES_LINE = ("  Bilder (mit BILD: zeigen, nicht lesen oder suchen, "
                          "der Pfad genügt): {paths}")
CALL_IMAGE_REFUSED = "(Bild nicht zeigbar: nur png/jpg/svg/webp aus dem Projektordner oder den Zusatzquellen)"

TIMEOUT_ABORT_TEXT = "Robo: dauert zu lange, abgebrochen — bitte wiederholen"

# 2026-10-20 replay: an ack whose job failed (or was never run) stayed
# on the stage forever ("Recherche läuft …" with nothing behind it).
# Every call job ends in a visible line -- the answer, or this.
CALL_FAILED_TEXT = "Robo: Zuruf nicht geschafft — bitte wiederholen"

# Live finding 4, 2026-10-26 KI-Stammtisch: a call answer from 18:59 stood
# under the title for 40 minutes straight -- the mirror line has one
# writer (the call lane, via `_write()`) and had no expiry. The answer
# text already lives permanently in the topic's history entry (see
# `_on_call_result`), so letting the mirror line fade back to empty loses
# nothing; the page hides an empty mirror line on its own
# (`#mirror:empty{display:none}` in stage/stage.py).
MIRROR_EXPIRY_S = 60.0

# 27.10.2026: three wake blocks fired three calls in 74s,
# each logged "answered", and nobody in the room saw an answer. The funding
# overview DID persist (into the topic's history entry, via
# `append_to_history` below) -- but the topic moved on seconds later, and
# a history entry that is no longer `main` is a small, clickable item in
# the history bar, not something the room is looking at. `mirror` is the
# wrong place to carry it either way: it is deliberately short, quiet and
# capped at 300 chars on the page (stage/stage.py), and `main`/`topic` must
# stay free for the room's OWN topic changes at any moment (see
# `StageState._topic()`'s docstring -- braking that a second time already
# cost two confirmed topics in the 2026-10-20 replay). `call_answer` is a
# separate, additive field precisely so it can outlive both: five minutes,
# matching this card's acceptance bar ("visible on stage >= 5 min").
CALL_ANSWER_VISIBLE_S = 300.0

# Header of a merged call job's request: several calls arrived while
# one was already running, they are answered together, in order.
CALL_MERGED_HEADER = (
    "Mehrere Zurufe sind eingegangen, waehrend du beschaeftigt warst. "
    "Beantworte JEDEN davon, in dieser Reihenfolge, nummeriert:")

CALL_INTENT_LABELS = {
    "structure": "Strukturierung", "summarize": "Zusammenfassung",
    "research": "Recherche", "question": "Antwort", "generic": "Antwort",
}

# Live finding 3, 2026-10-26 KI-Stammtisch: the call lane answered the
# literal word "none" (18:44) and a status description about itself
# ("Thema: Wake-Word-Test ... keine Dateiänderung", 19:44) instead of
# answering the call or staying silent. CALL_ANSWER_RULE already tells
# the model never to report what it read/checked/did; this is the
# code-level backstop for when it does it anyway -- the output contract
# is a real answer or nothing, never a report about itself.
_META_ANSWER_NONE = re.compile(r"^none$", re.IGNORECASE)
_META_ANSWER_SELF_REPORT = re.compile(
    r"keine\s+datei\w*[äa]nderung", re.IGNORECASE)


def _is_meta_answer(text: str) -> bool:
    """Is `text` a non-answer the call lane must never publish?"""
    text = text.strip()
    return bool(_META_ANSWER_NONE.match(text)
               or _META_ANSWER_SELF_REPORT.search(text))


def _is_timeout(result: dict) -> bool:
    return TIMEOUT_MARKER in str(result.get("error") or "")

def call_paths_text(folder, protocol, extra_dirs=()) -> str:
    """The call lane's path lines (absolute), appended to its stable prefix.

    `extra_dirs`: additional read-only reference folders
    (COTHINKER_EXTRA_DIRS); empty keeps the text exactly as before."""
    lines = [CALL_PATHS_TEMPLATE.format(folder=Path(folder).resolve(),
                                        protocol=Path(protocol).resolve())]
    extras = [str(Path(d).expanduser().resolve()) for d in extra_dirs if d]
    if extras:
        for p in extras:
            lines.append(CALL_EXTRA_DIR_LINE.format(path=p))
            names = _extra_dir_files(Path(p))
            if names:
                lines.append(CALL_EXTRA_FILES_LINE.format(names=", ".join(names)))
            images = [n for n in names
                      if Path(n).suffix.lower() in stage_image.IMAGE_EXTS]
            if images:
                # 2026-10-20 real run: with the name alone the agent tried
                # search_files (blind to links) and read_file (no use on a
                # PNG) and answered "not found". The absolute path is all
                # the BILD: line needs.
                lines.append(CALL_EXTRA_IMAGES_LINE.format(
                    paths=", ".join(str(Path(p) / n) for n in images)))
        lines.append(CALL_EXTRA_DIRS_RULE)
    lines.append(CALL_IMAGE_RULE)
    return "\n".join(lines)


def _extra_dir_files(folder: Path) -> list:
    """Top-level file names of a reference folder (README first), only when
    it holds symlinks -- a plain folder is searchable as it is. Sorted, so
    the prefix stays byte-stable for the whole meeting."""
    try:
        entries = sorted(folder.iterdir(), key=lambda e: e.name)
    except OSError:
        return []
    if not any(e.is_symlink() for e in entries):
        return []
    names = [e.name for e in entries if e.is_file()]   # follows links
    names.sort(key=lambda n: (n.lower() != "readme.md", n))
    return names[:CALL_EXTRA_MAX_FILES]


def extra_dirs_from_env(value: str) -> list:
    """COTHINKER_EXTRA_DIRS: colon-separated absolute paths, blanks dropped."""
    return [p.strip() for p in (value or "").split(":") if p.strip()]



class CallLane:
    """One instance per meeting, only constructed when `--lanes` is set.

    `dispatch` (set by the constructor or `attach()`) is the block host's
    `SessionBlocks.dispatch`: Robo's own lines and the confirmed topic go to
    the blocks through it. Without it the lane runs alone (tests, no
    blocks).
    """

    def __init__(self, *, call_command: list, call_session_id: str,
                 project_agenda: str, state, writer,
                 call_paths: str = "",
                 image_dirs: Optional[list] = None,
                 call_turn_timeout_s: float = CALL_TURN_TIMEOUT_S,
                 call_lane_factory: Optional[Callable] = None,
                 dispatch: Optional[Callable[[str, Mapping], int]] = None,
                 window_size: int = WINDOW_SIZE, roll_every: int = ROLL_EVERY,
                 now: Callable[[], float] = time.monotonic,
                 log: Callable[..., None] = print) -> None:
        self.log = log
        self.now = now
        self.state = state
        self.writer = writer
        self.call_turn_timeout_s = call_turn_timeout_s
        self.call_paths = call_paths
        # Where a `BILD:` path may point (project folder + extra dirs);
        # None/empty = images off, the marker line is just dropped.
        self.image_dirs = list(image_dirs or [])
        self.call_ctx = LaneContext(
            project_agenda=self._with_call_paths(project_agenda),
            window_size=window_size, roll_every=roll_every)
        self._project_agenda = project_agenda
        self._dispatch = dispatch
        self._lock = threading.Lock()
        self._current_topic_id = ""
        # The block text of the decider result being processed; the
        # `topics` block's `topic` output carries no text (see on_topic).
        self._decider_block_text = ""
        self._job_counter = 0
        # The mirror line's own expiry (live finding 4) -- see
        # MIRROR_EXPIRY_S and mirror_tick().
        self._mirror_text = ""
        self._mirror_set_at = 0.0
        # The call_answer panel's own, longer expiry --
        # see CALL_ANSWER_VISIBLE_S and mirror_tick().
        self._call_answer_text = ""
        self._call_answer_set_at = 0.0
        # Call lane: MERGE, never drop (operator, 2026-10-20: every call gets
        # answered, nothing ever stalls). The waiting slot stays one job.
        # COTHINKER_AGENT=api (gateway/agent_lane.py): a lane factory
        # replaces the worker process with the agent seam; same lane API.
        if call_lane_factory is not None:
            self.call_lane = call_lane_factory(
                call_session_id, on_result=self._on_call_result,
                on_dropped=self._on_call_dropped, merge=self._merge_calls)
        else:
            self.call_lane = LaneQueue(
                WorkerClient(command=call_command, session_id=call_session_id,
                            log=log),
                on_result=self._on_call_result,
                on_dropped=self._on_call_dropped,
                merge=self._merge_calls, log=log)

    # ---------------------------------------------------------- lifecycle
    def attach(self, dispatch: Callable[[str, Mapping], int]) -> None:
        """Connects the block host and tells it the current stable prefix."""
        self._dispatch = dispatch
        self._publish({"stable_prefix": self._project_agenda})

    def start(self) -> bool:
        """Starts the worker process now (operator's point 3: ~19s to become
        ready) -- never lazily on the first call, which would put that
        latency in front of whoever is waiting."""
        return self.call_lane.start()

    def stop(self) -> None:
        self.call_lane.stop()

    def stats(self) -> dict:
        return {**self.call_lane.stats(),
                "cache_read_ratio": self.call_ctx.last_cache_read_ratio}

    def update_stable_prefix(self, project_agenda: str) -> None:
        """Operator's point 7: the stable prefix (profile/tools segment) may
        change exactly once, when the intro round ends and enrolled
        speaker names are known -- an accepted cache-hit break, never
        repeated. The caller builds the new text (participants +
        enrolled names); this installs it on the call lane and hands it to
        the blocks."""
        self._project_agenda = project_agenda
        self.call_ctx.project_agenda = self._with_call_paths(project_agenda)
        self._publish({"stable_prefix": project_agenda})

    def _with_call_paths(self, project_agenda: str) -> str:
        return (f"{project_agenda}\n{self.call_paths}" if self.call_paths
                else project_agenda)

    def _next_job_id(self, prefix: str) -> str:
        self._job_counter += 1
        return f"{prefix}-{self._job_counter}"

    def _publish(self, output: Mapping[str, object]) -> None:
        """One `block_output` event from the session host to the blocks."""
        if self._dispatch is None:
            return
        try:
            self._dispatch("block_output", {"from": "session-host", **output})
        except Exception as e:                # noqa: BLE001 -- a lane must
            # never take the consumer down with a broken block.
            self.log("call lane: block dispatch failed:", repr(e))

    # -------------------------------------------------------------- calls
    @staticmethod
    def agent_text(text: str) -> str:
        """Point 5: the agent-facing text is PREFILTERED (fillers/repeats
        stripped) -- the raw text is what `protokoll.md` keeps; the lane
        windows never see it."""
        return prefilter(text)[0]

    def handle_block(self, speaker: str, text: str, *,
                     wake_word: bool) -> Optional[str]:
        """One finished block (`gateway/segmenter.py`'s `block_done`).

        Returns the acknowledgement text when this was a call -- the
        caller's cue that this block went to the call lane and must NOT
        also be enqueued on the default single-worker queue. Returns None
        for every other block: ordinary speech reaches the coach through
        the block host's `segment` event, not through this lane.
        """
        if not wake_word:
            return None
        filtered = self.agent_text(text)
        ack = call_ack(text)
        self._write(ack)
        self._broadcast("Robo (Zuruf)", ack)
        if self.call_ctx.add_utterance(speaker, filtered):
            self.log("call lane: context window rolled, Stand folded")
            self._rolled(self.call_lane)
        calls = [{"text": filtered, "intent": call_intent(text),
                  "ack": ack, "at": self.now()}]
        self.call_lane.submit(Job(id=self._next_job_id("call"),
                                  prompt=self._call_prompt(calls),
                                  timeout=self.call_turn_timeout_s,
                                  meta={"intent": calls[0]["intent"],
                                        "calls": calls}))
        return ack

    def observe_intent(self, intent) -> None:
        """The stage sink's tee: a coach contribution (`mirror:` intent of
        the mirroring-coach block) goes into the call window as an
        ordinary speaker line, so the call lane sees it on its next turn.
        A final topic title (`topics` block) is passed on to the blocks."""
        if intent.block == "topics":
            self._on_topic_intent(intent)
            return
        if intent.block != "mirroring-coach" or not intent.id.startswith("mirror:"):
            return
        payload = intent.payload if isinstance(intent.payload, Mapping) else {}
        line = str(payload.get("line") or "")
        if not line:
            return
        if self.call_ctx.add_utterance(str(payload.get("speaker") or "Robo (Coach)"),
                                       line):
            self.log("call lane: context window rolled, Stand folded")
            self._rolled(self.call_lane)

    def _on_topic_intent(self, intent) -> None:
        """A final title from the `topics` block (`provisional` false): the
        stage sink has already swapped it in place; the blocks are told the
        stage entry's new title (`retitled`), as the titler callback did."""
        payload = intent.payload if isinstance(intent.payload, Mapping) else {}
        if payload.get("provisional", True):
            return
        title = str(payload.get("title") or "")
        entry_id = next((e.id for e in reversed(list(self.state.history))
                         if e.title == title), "")
        if entry_id:
            self._publish({"topic": {"id": entry_id, "title": title,
                                     "retitled": True}})

    def note_decider_block(self, block_text: str) -> None:
        """The consumer's decider callback calls this just before it hands
        the result to the block host: the `topics` block's `topic` output
        carries no block text, and `on_topic` needs it for the history
        entry. The host dispatches synchronously on the same thread."""
        self._decider_block_text = str(block_text or "")

    def on_topic(self, topic) -> None:
        """The block host's `on_output` listener for `{"topic": ...}`.

        The `topics` block (blocks/topics) confirms a genuinely NEW topic
        (or a RETURN, `topic["return"]`: no entry) and emits it. A new topic
        gets exactly one new history entry (point 6a); a return gets none
        (reactivating an old topic does not invent a new history row for
        it). Either way the blocks are told through a `block_output` event
        with `topic` (`id` is empty when no entry was pushed). This is the
        ONLY writer of topic history entries: the legacy stage sink only
        rewrites the stage file and swaps a final title in place.
        """
        returned = bool(topic.get("return"))
        title = str(topic.get("title") or "Thema")
        block_text = self._decider_block_text
        if returned:
            self.log("topic: return")
        else:
            self.log(f"topic: new {title}")
        announced = {"id": "", "title": title, "returned": returned,
                     "coach_eligible": not returned}
        if returned:
            self._publish({"topic": announced})
            return
        # A CONFIRMED change (the topics block's own pacing) always gets its
        # entry -- the stage's automatic-change brake must not refuse it a
        # second time (the 2026-10-20 replay lost both confirmed topics to
        # "too soon").
        reason_ok = self.state.confirmed_topic(title, block_text)
        if reason_ok:
            self.log("lane: topic history entry not pushed:", reason_ok)
            self._publish({"topic": announced})
            return
        with self._lock:
            self._current_topic_id = self.state.main.id
        announced["id"] = self._current_topic_id
        announced["title"] = self.state.main.title    # as the stage normalised it
        self._publish({"topic": announced})
        try:
            self.writer.write()
        except Exception as e:                # noqa: BLE001
            self.log("lane: topic push not written:", repr(e))

    # ----------------------------------------------------------- internal
    def _topic_header(self) -> str:
        """Point 8: the decider's current topic id/title, as a small
        header on every lane turn -- read straight off the stage state
        the consumer already owns, never re-derived."""
        m = self.state.main
        if not m.id:
            return ""
        return f"[Thema {m.id}: {m.title}]" if m.title else f"[Thema {m.id}]"

    def _broadcast(self, label: str, text: str) -> None:
        """Point 8: Robo's own output is never agent-to-agent -- the
        CONSUMER appends it to the call window AND hands it to the blocks
        as a normal speaker line, so the coach sees it on its own next
        turn."""
        if self.call_ctx.add_utterance(label, text):
            self.log("call lane: context window rolled, Stand folded")
            self._rolled(self.call_lane)
        self._publish({"robo_line": {"speaker": label, "text": text}})

    @staticmethod
    def _rolled(lane) -> None:
        """A window roll: a seam lane opens a fresh agent session next;
        a worker lane has nothing to do (--no-history)."""
        on_roll = getattr(lane, "on_roll", None)
        if on_roll is not None:
            on_roll()

    def _call_prompt(self, calls: list) -> str:
        if len(calls) == 1:
            request = calls[0]["text"]
        else:
            request = CALL_MERGED_HEADER + "\n" + "\n".join(
                f"{i}. {c['text']}" for i, c in enumerate(calls, 1))
        shape = ""
        if len(calls) == 1:
            shape = CALL_INTENT_SHAPES.get(calls[0].get("intent") or "", "")
        rules = CALL_ANSWER_RULE + (f"\n{shape}" if shape else "")
        return self.call_ctx.build_prompt(
            f"{self._topic_header()}\n{CALL_TOOL_BUDGET_NOTE}\n{rules}\n"
            f"{request}")

    def _merge_calls(self, waiting: Job, new: Job) -> Job:
        """LaneQueue's `merge`: the waiting call job absorbs the new one.
        One combined prompt listing every pending call in order; the
        acks of all of them are resolved by this one job's result."""
        calls = list(waiting.meta.get("calls") or []) + list(
            new.meta.get("calls") or [])
        self.log(f"call lane: {new.id} merged into waiting job "
                 f"({len(calls)} calls)")
        return Job(id=f"{waiting.id}+{new.id}", prompt=self._call_prompt(calls),
                   timeout=max(waiting.timeout, new.timeout),
                   meta={"intent": waiting.meta.get("intent", "generic"),
                         "calls": calls})

    def _log_call_outcome(self, job: Job, outcome: str) -> None:
        now = self.now()
        lat = ", ".join(f"{now - c.get('at', now):.1f}s"
                        for c in job.meta.get("calls") or [])
        self.log(f"call lane: {job.id} {outcome}, "
                 f"{len(job.meta.get('calls') or [])} call(s), "
                 f"ack->answer {lat}")

    def _on_call_dropped(self, job: Job) -> None:
        """Only reached when the lane stops with a job still waiting --
        its ack must not outlive the meeting as a forever-running line."""
        self._log_call_outcome(job, "dropped")
        self._write(CALL_FAILED_TEXT)

    def _on_call_result(self, job: Job, result: dict, duration: float) -> None:
        if _is_timeout(result):
            self._log_call_outcome(job, "timed out")
            self._write(TIMEOUT_ABORT_TEXT)
            self._broadcast("Robo (Zuruf)", TIMEOUT_ABORT_TEXT)
            return
        text = (result.get("final") or "").strip()
        if not result.get("ok") or not text:
            self.log("call lane failed:", result.get("error"))
            self._log_call_outcome(job, "failed")
            self._write(CALL_FAILED_TEXT)
            self._broadcast("Robo (Zuruf)", CALL_FAILED_TEXT)
            return
        if _is_meta_answer(text):
            # Reject both patterns: keep the ack line on stage instead of
            # publishing a non-answer as Robo's answer.
            self.log(f"call lane: {job.id} meta answer rejected: {text!r}")
            self._log_call_outcome(job, "rejected (meta answer)")
            return
        self._log_call_outcome(job, "answered")
        text, image = self._take_image(text)
        reply = f"Robo: {text}"
        if self.call_lane.waiting_count():
            # The mirror holds one line: this answer just replaced a later
            # call's ack, so say that one is still on its way.
            reply = f"{reply} · nächster Zuruf läuft …"
        self._write(reply)
        self._set_call_answer(reply)
        if image is not None:
            # AFTER the mirror line: a new mirror line closes an image.
            self._show_image(*image)
        self._broadcast("Robo (Zuruf)", reply)
        # A call result is NOT a topic: it goes INTO the current topic's
        # entry (2026-10-20 replay: own "Robo: Antwort" entries pushed the
        # real topics out and the coach attached to a call result).
        label = CALL_INTENT_LABELS.get(job.meta.get("intent", "generic"),
                                       "Antwort")
        with self._lock:
            entry_id = self._current_topic_id
        at = datetime.now().astimezone().strftime("%H:%M")
        if not entry_id or not self.state.append_to_history(
                entry_id, f"**Robo ({label}, {at}):** {text}"):
            # No confirmed topic yet: the mirror line is the call area.
            self.log("lane: call result shown in the mirror only (no topic "
                     "entry yet)")
            return
        try:
            self.writer.write()
        except Exception as e:                # noqa: BLE001
            self.log("lane: call result history push not written:", repr(e))

    def _take_image(self, text: str):
        """Split a `BILD:` line off a call answer. Returns the text to show
        and `(id, caption)` for a valid image, or None."""
        rest, path = stage_image.extract_marker(text)
        if path is None:
            return text, None
        ok = stage_image.validate(path, self.image_dirs) if self.image_dirs else None
        if ok is None:
            # The path is not logged: it came from a model.
            self.log("call lane: image refused (path not allowed)")
            return (f"{rest} {CALL_IMAGE_REFUSED}".strip(), None)
        caption = stage_image.caption_for(ok, rest)
        return (rest or caption), (stage_image.image_id(ok), caption)

    def _show_image(self, image_id: str, caption: str) -> None:
        try:
            reason = self.state.apply({"action": "image", "id": image_id,
                                       "caption": caption})
            if reason:
                self.log("call lane: image not applied:", reason)
                return
            self.writer.write()
            self.log(f"call lane: image {image_id} on stage")
        except Exception as e:                # noqa: BLE001
            self.log("lane: image not written:", repr(e))

    def _write(self, text: str) -> None:
        try:
            self.state.apply({"action": "mirror", "text": text})
            self.writer.write()
        except Exception as e:                # noqa: BLE001 -- a lane must
            # never take the consumer down with it.
            self.log("lane: result not written:", repr(e))
            return
        with self._lock:
            self._mirror_text = text
            self._mirror_set_at = self.now()

    def _set_call_answer(self, text: str) -> None:
        """Keep the full answer visible on its own,
        outliving `mirror` (60s) and surviving a topic change (`main`'s
        is the room's to move at any time). Expired the same way, by
        `mirror_tick()` below, after the much longer CALL_ANSWER_VISIBLE_S."""
        try:
            self.state.apply({"action": "call_answer", "text": text})
            self.writer.write()
        except Exception as e:                # noqa: BLE001
            self.log("lane: call answer panel not written:", repr(e))
            return
        with self._lock:
            self._call_answer_text = text
            self._call_answer_set_at = self.now()

    def mirror_tick(self) -> None:
        """Fade a call-lane mirror line back to empty after MIRROR_EXPIRY_S
        (live finding 4), and the call_answer panel after the longer
        CALL_ANSWER_VISIBLE_S -- called from the
        consumer's ticker, same cadence as the other ticker work.

        Only clears OUR OWN last-written text, and only while it is still
        on stage unchanged: if something else (the agent's own mirror
        proposal, a newer call) wrote over it meanwhile, this is not ours
        to touch any more.
        """
        with self._lock:
            if not self._mirror_text or self.now() - self._mirror_set_at < MIRROR_EXPIRY_S:
                mirror_text = None
            else:
                mirror_text, self._mirror_text = self._mirror_text, ""
            if (not self._call_answer_text
                    or self.now() - self._call_answer_set_at < CALL_ANSWER_VISIBLE_S):
                answer_text = None
            else:
                answer_text, self._call_answer_text = self._call_answer_text, ""
        if mirror_text is not None and self.state.mirror == mirror_text:
            try:
                self.state.apply({"action": "mirror", "text": ""})
                self.writer.write()
            except Exception as e:                # noqa: BLE001
                self.log("lane: mirror expiry not written:", repr(e))
        if answer_text is not None and self.state.call_answer == answer_text:
            try:
                self.state.apply({"action": "call_answer", "text": ""})
                self.writer.write()
            except Exception as e:                # noqa: BLE001
                self.log("lane: call answer panel expiry not written:", repr(e))
