#!/usr/bin/env python3
# CoThinker — wires the call lane and the coach lane into the consumer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`Lanes` is the one object `gateway/consumer.py --lanes` adds.

It owns two `gateway.lanes.LaneQueue`s (call, coach), each wrapping its
own `gateway.worker.WorkerClient` process and its own
`gateway.lane_context.LaneContext` window, plus one
`gateway.topic_hysteresis.TopicHysteresis` that turns the decider's
noisy per-block numbers into confirmed topic changes.

Kept out of `gateway/consumer.py` itself on purpose: that file is huge
and runs live meetings (AGENTS.md's "the service may be running right
now") -- isolating the new, less-proven code in its own module keeps
the serial `--policy`/variant-C path's diff to "construct this, call
these two hooks", and makes the lane logic unit-testable without a real
protocol/stage/vault around it.

INTER-LANE COMMUNICATION (operator's point 8, 2026-10-20): never agent to
agent. Every piece of Robo's own output -- the call's acknowledgement,
the call lane's result, the coach's contribution -- is appended by THIS
module, the consumer side, to BOTH lanes' rolling windows as an ordinary
speaker line ("Robo (Zuruf): ..." / "Robo (Coach): ..."), so the other
lane sees it on its own next turn. The only channel to either worker
process is the existing turn-JSON-in/result-JSON-out stdin/stdout
protocol (`gateway/worker.py`) through `gateway.lanes.LaneQueue` -- no
second channel, and neither lane ever writes a file itself.

Writing to the stage reuses the SAME `state`/`writer` the rest of the
consumer already owns (the `state.apply(...)` pattern `gateway/
consumer.py` itself uses for "Robo hoert mit.", plus the new
`state.append_to_history()`/`history_title()`) -- never a second
writer, per AGENTS.md's single-writer rule for `buehne.json`.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from shared.wake_word import call_ack, call_intent
from correction_prefilter import prefilter
from lane_context import ROLL_EVERY, WINDOW_SIZE, LaneContext
from lanes import TIMEOUT_MARKER, Job, LaneQueue
import stage_image
from stage_state import derived_title, one_line
from topic_hysteresis import MIN_TOPIC_AGE_S, THRESHOLD, TopicHysteresis
from worker import WorkerClient

# Operator's point 3 (2026-10-20): the coach's full reply is EITHER exactly
# this token OR starts with one of these labels, and is <= this many
# chars. Smoke-tested against gateway/worker.py at 90f40fb: a
# circular-argument case answered "Spiegel: ..." in 6.5s, an early-topic
# case answered "[SILENT]" in 2.3s.
COACH_SILENT = "[SILENT]"
COACH_LABELS = ("Spiegel:", "Struktur:", "Frage:")
COACH_MAX_CHARS = 600

COACH_REQUEST = (
    "Trage zum laufenden Thema bei, wenn es etwas gibt beizutragen -- "
    "sonst nicht. Antworte NUR mit genau \"[SILENT]\", oder beginne "
    "deine ganze Antwort mit genau einem der Label \"Spiegel:\", "
    "\"Struktur:\" oder \"Frage:\", gefolgt von deinem Beitrag. "
    "Hoechstens 600 Zeichen insgesamt.")

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
COACH_TURN_TIMEOUT_S = 60.0

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

COACH_MIN_DELAY_S = 45.0

# --coach-rhythm (operator, 2026-10-20, default off): the coach helps at the
# START of a topic (orient), at its END (structure the closed topic, into
# that topic's own history entry) and at most once more in a long pause --
# fostering the discussion, never interrupting. Coach eval on the
# 26.09./21.09. meetings: the one-shot-at-45s trigger spoke ~0.7/h and
# the best output came at topic end. Live fires (start, long pause) only
# happen in a pause with no utterance for COACH_PAUSE_S.
COACH_PAUSE_S = 15.0
COACH_LONG_TOPIC_S = 240.0          # long-pause fire only after 4 min
COACH_MIN_GAP_S = 480.0             # >= 8 min between SPOKEN live fires
COACH_MAX_PER_HOUR = 4              # rolling, spoken live fires only
COACH_RETRY_S = 90.0                # after a [SILENT], next try no sooner
COACH_MAX_TRIES = 3                 # per topic and phase (start / pause)
COACH_REQUEST_START = (
    "Thema beginnt: hilf beim Einstieg — eine Frage, die das Thema "
    "öffnet, oder ein Spiegel, worum es geht; sonst [SILENT].")
COACH_REQUEST_END = (
    "Thema abgeschlossen: Struktur der vier Pinnwände oder "
    "Offen/Aufgaben, nur Gesagtes; sonst [SILENT].")
COACH_REQUEST_PAUSE = (
    "Längere Pause im laufenden Thema: hilf weiter, nur wenn es etwas "
    "beizutragen gibt; sonst [SILENT].")
COACH_RHYTHM_REQUESTS = {"start": COACH_REQUEST_START,
                         "end": COACH_REQUEST_END,
                         "pause": COACH_REQUEST_PAUSE}

# Point C (6d, 2026-10-20): the Nachtrag notice reuses the EXISTING
# single-slot warning channel (`StageWriter.warn()`/`clear_warning()`)
# rather than a new field -- but that slot is shared with real
# operational warnings (conflicts, a failed protocol write), so this
# module NEVER overwrites a warning it did not itself write: every text
# it sets starts with this exact prefix, and it only ever replaces a
# warning that already starts with it.
NACHTRAG_PREFIX = "Nachtrag "

CALL_INTENT_LABELS = {
    "structure": "Strukturierung", "summarize": "Zusammenfassung",
    "research": "Recherche", "question": "Antwort", "generic": "Antwort",
}


def _valid_coach_reply(text: str) -> bool:
    """Operator's point 3 output contract, checked before anything of a
    coach reply is trusted enough to go anywhere near the stage."""
    text = text.strip()
    if text == COACH_SILENT:
        return True
    return (len(text) <= COACH_MAX_CHARS
            and any(text.startswith(label) for label in COACH_LABELS))


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


@dataclass
class LaneStats:
    call: dict
    coach: dict


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


class Lanes:
    """One instance per meeting, only constructed when `--lanes` is set.

    `coach_briefing` only matters to a `coach_lane_factory` built over the
    agent seam (`gateway/agent_lane.py`'s `api_lane_factory`): the worker
    path (default `coach_lane_factory=None`) gets its standing
    instructions from `--hermes-home`'s own `SOUL.md` and ignores it.
    """

    def __init__(self, *, call_command: list, coach_command: list,
                 call_session_id: str, coach_session_id: str,
                 project_agenda: str, state, writer,
                 call_paths: str = "",
                 image_dirs: Optional[list] = None,
                 history_path: Optional[Path] = None,
                 coach_min_delay_s: float = COACH_MIN_DELAY_S,
                 call_turn_timeout_s: float = CALL_TURN_TIMEOUT_S,
                 coach_turn_timeout_s: float = COACH_TURN_TIMEOUT_S,
                 coach_rhythm: bool = False,
                 topic_threshold: float = THRESHOLD,
                 topic_min_age_s: float = MIN_TOPIC_AGE_S,
                 titler_factory: Optional[Callable] = None,
                 call_lane_factory: Optional[Callable] = None,
                 coach_lane_factory: Optional[Callable] = None,
                 coach_briefing: str = "",
                 window_size: int = WINDOW_SIZE, roll_every: int = ROLL_EVERY,
                 now: Callable[[], float] = time.monotonic,
                 log: Callable[..., None] = print) -> None:
        self.log = log
        self.now = now
        self.state = state
        self.writer = writer
        self.history_path = history_path
        self.coach_min_delay_s = coach_min_delay_s
        self.call_turn_timeout_s = call_turn_timeout_s
        self.coach_turn_timeout_s = coach_turn_timeout_s
        self.hysteresis = TopicHysteresis(
            threshold=topic_threshold, min_topic_age_s=topic_min_age_s, now=now)
        self.call_paths = call_paths
        # Where a `BILD:` path may point (project folder + extra dirs);
        # None/empty = images off, the marker line is just dropped.
        self.image_dirs = list(image_dirs or [])
        self.call_ctx = LaneContext(
            project_agenda=self._with_call_paths(project_agenda),
            window_size=window_size, roll_every=roll_every)
        self.coach_ctx = LaneContext(project_agenda=project_agenda,
                                    window_size=window_size, roll_every=roll_every)
        self._lock = threading.Lock()
        self._coach_topic_started_at = self.hysteresis.topic_started_at
        self._coach_eligible = False
        self._coach_contributed = False
        self._current_topic_id = ""
        # --coach-rhythm state (untouched when the switch is off).
        self.coach_rhythm = coach_rhythm
        self._last_utterance_at = now()
        self._rhythm_inflight = False
        self._rhythm_spoken: list = []      # times of spoken live fires
        self._rhythm_tries = {"start": 0, "pause": 0}
        self._rhythm_done = {"start": False, "pause": False}
        self._rhythm_next_try_at = 0.0
        # The history entry of the RUNNING topic, only when it is known:
        # a confirmed return pushes no entry, so it stays "" then.
        self._rhythm_entry_id = ""
        self._pending_nachtraege: list = []
        self._last_nachtrag_text = ""
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
        # Coach lane: newest wins -- a superseded coach request is
        # simply obsolete, nobody waits for it.
        if coach_lane_factory is not None:
            self.coach_lane = coach_lane_factory(
                coach_session_id, on_result=self._on_coach_result,
                on_dropped=self._on_coach_dropped, briefing=coach_briefing)
        else:
            self.coach_lane = LaneQueue(
                WorkerClient(command=coach_command, session_id=coach_session_id,
                            log=log),
                on_result=self._on_coach_result,
                on_dropped=self._on_coach_dropped,
                log=log)
        # --topic-titles (default off): None keeps today's behaviour --
        # the provisional title from the block's first line stays.
        # The factory gets the two callbacks and returns an object with
        # `request(entry_id, provisional, block, previous)` and `stop()`
        # (gateway/topic_title.py's TopicTitler).
        self.titler = (titler_factory(self._on_title, self._on_title_failed)
                       if titler_factory is not None else None)

    # ---------------------------------------------------------- lifecycle
    def start(self) -> tuple[bool, bool]:
        """Starts BOTH worker processes now (operator's point 3: ~19s to
        become ready) -- never lazily on the first call or topic, which
        would put that latency in front of whoever is waiting."""
        return self.call_lane.start(), self.coach_lane.start()

    def stop(self) -> None:
        self.call_lane.stop()
        self.coach_lane.stop()
        if self.titler is not None:
            self.titler.stop()

    def stats(self) -> dict:
        return {
            "call": {**self.call_lane.stats(),
                     "cache_read_ratio": self.call_ctx.last_cache_read_ratio},
            "coach": {**self.coach_lane.stats(),
                     "cache_read_ratio": self.coach_ctx.last_cache_read_ratio},
        }

    def update_stable_prefix(self, project_agenda: str) -> None:
        """Operator's point 7: the stable prefix (profile/tools segment) may
        change exactly once, when the intro round ends and enrolled
        speaker names are known -- an accepted cache-hit break, never
        repeated. The caller builds the new text (participants +
        enrolled names); this only installs it on both lanes."""
        self.call_ctx.project_agenda = self._with_call_paths(project_agenda)
        self.coach_ctx.project_agenda = project_agenda

    def _with_call_paths(self, project_agenda: str) -> str:
        return (f"{project_agenda}\n{self.call_paths}" if self.call_paths
                else project_agenda)

    def _next_job_id(self, prefix: str) -> str:
        self._job_counter += 1
        return f"{prefix}-{self._job_counter}"

    # -------------------------------------------------------------- calls
    def handle_block(self, speaker: str, text: str, *,
                     wake_word: bool) -> Optional[str]:
        """One finished block (`gateway/segmenter.py`'s `block_done`).

        Point 5: the agent-facing text is PREFILTERED (fillers/repeats
        stripped) -- the raw text is what `protokoll.md` keeps; this
        window never sees it.

        Returns the acknowledgement text when this was a call -- the
        caller's cue that this block went to the call lane and must NOT
        also be enqueued on the default single-worker queue. Returns
        None for every other block.
        """
        filtered, _stats = prefilter(text)
        if wake_word:
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
        for line_speaker, line_text in self._speaker_lines(speaker, filtered):
            if self.coach_ctx.add_utterance(line_speaker, line_text):
                self.log("coach lane: context window rolled, Stand folded")
                self._rolled(self.coach_lane)
        if not self.coach_rhythm:
            self._maybe_fire_coach()
        return None

    @staticmethod
    def _speaker_lines(speaker: str, text: str) -> list:
        """--speaker-split: a block whose text carries several lines of
        `Name: ...` (one per speaker turn, gateway/speaker_split.py) goes
        into the window as one utterance PER LINE under its own name.
        Single-line text -- everything without the switch -- stays one
        utterance under `speaker`, exactly as before."""
        if "\n" not in text:
            return [(speaker, text)]
        out = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            name, sep, rest = line.partition(": ")
            if sep and name and len(name) <= 40 and rest.strip():
                out.append((name, rest.strip()))
            elif out:
                out[-1] = (out[-1][0], f"{out[-1][1]} {line}")
            else:
                out.append((speaker, line))
        return out or [(speaker, text)]

    def on_decider_result(self, result, snap, reason: str) -> None:
        """Chained onto `gateway/decider.py`'s `DeciderWorker.on_result`.

        Reads `neues_thema`/`rueckkehr_thema` off `result.by_id()` and
        feeds the hysteresis. A confirmed, genuinely new topic opens a
        45s-minimum coach-eligibility window AND gets exactly one new
        history entry (point 6a); a confirmed return does neither (per
        spec, a return never triggers a new coach contribution, and
        reactivating an old topic does not invent a new history row for
        it).
        """
        by_id = result.by_id()
        p_neu = (by_id.get("neues_thema") or {}).get("p")
        p_ruek = (by_id.get("rueckkehr_thema") or {}).get("p")
        decision = self.hysteresis.on_result(p_neu, p_ruek)
        if not decision.changed:
            return
        block_text = str(getattr(snap, "block", "") or "")
        title = derived_title(block_text) or one_line(block_text) or "Thema"
        if decision.returned:
            self.log(f"topic: return p={p_ruek}")
        else:
            self.log(f"topic: new {title} p={p_neu}")
        if self.coach_rhythm:
            # END: the topic that just closed gets its structure, filed
            # into ITS entry -- built before the new entry is pushed, so
            # the header still names the closed topic.
            self._fire_rhythm_end()
        with self._lock:
            self._coach_topic_started_at = self.now()
            self._coach_eligible = decision.coach_eligible
            self._coach_contributed = False
            self._rhythm_tries = {"start": 0, "pause": 0}
            self._rhythm_done = {"start": False, "pause": False}
            self._rhythm_next_try_at = 0.0
            self._rhythm_entry_id = ""
        if decision.returned:
            return
        # A CONFIRMED change (p >= threshold AND min topic age, the
        # hysteresis' own pacing) always gets its entry -- the stage's
        # automatic-change brake must not refuse it a second time (the
        # 2026-10-20 replay lost both confirmed topics to "too soon").
        reason_ok = self.state.confirmed_topic(title, block_text)
        if reason_ok:
            self.log("lane: topic history entry not pushed:", reason_ok)
            return
        with self._lock:
            self._current_topic_id = self.state.main.id
            self._rhythm_entry_id = self.state.main.id
        if self.titler is not None:
            # --topic-titles: the provisional title is up NOW; one model
            # call off this thread may replace it in place later.
            entry = self.state.main
            previous = list(getattr(snap, "previous", None) or [])
            self.titler.request(entry.id, entry.title, block_text,
                                str(previous[-1]) if previous else "")
        # Point 6d: a Nachtrag notice is about a topic that is no longer
        # current -- the moment a genuinely NEW topic lands, it no
        # longer applies (the round has moved on again).
        self._clear_nachtrag_notice()
        try:
            self.writer.write()
        except Exception as e:                # noqa: BLE001
            self.log("lane: topic push not written:", repr(e))

    def _on_title(self, entry_id: str, old: str, new: str, ms: int) -> None:
        """Titler callback (its own thread): swap the title in place."""
        changed = self.state.retitle(entry_id, old, new)
        self.log(f"topic: title {old!r} -> {new!r} {ms}ms"
                 + ("" if changed else " (not applied: entry changed)"))
        if changed:
            try:
                self.writer.write()
            except Exception as e:            # noqa: BLE001
                self.log("lane: retitle not written:", repr(e))

    def _on_title_failed(self, entry_id: str, token: str, ms: int) -> None:
        """The provisional title stays; TopicTitler already logged it."""

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
        CONSUMER appends it to BOTH lanes' windows as a normal speaker
        line, so the other lane sees it on its own next turn."""
        if self.call_ctx.add_utterance(label, text):
            self.log("call lane: context window rolled, Stand folded")
            self._rolled(self.call_lane)
        if self.coach_ctx.add_utterance(label, text):
            self.log("coach lane: context window rolled, Stand folded")
            self._rolled(self.coach_lane)

    @staticmethod
    def _rolled(lane) -> None:
        """A window roll: a seam lane opens a fresh agent session next;
        a worker lane has nothing to do (--no-history)."""
        on_roll = getattr(lane, "on_roll", None)
        if on_roll is not None:
            on_roll()

    def _maybe_fire_coach(self) -> None:
        with self._lock:
            due = (self._coach_eligible and not self._coach_contributed
                  and (self.now() - self._coach_topic_started_at)
                  >= self.coach_min_delay_s)
            if not due:
                return
            self._coach_contributed = True
            topic_started_at = self._coach_topic_started_at
            entry_id = self._current_topic_id
        self.log(f"coach lane: firing for topic {entry_id or '?'}")
        prompt = self.coach_ctx.build_prompt(
            f"{self._topic_header()}\n{COACH_REQUEST}")
        self.coach_lane.submit(Job(
            id=self._next_job_id("coach"), prompt=prompt,
            timeout=self.coach_turn_timeout_s,
            meta={"topic_started_at": topic_started_at,
                 "topic_entry_id": entry_id}))

    # ------------------------------------------------------ coach rhythm
    def note_utterance(self) -> None:
        """--coach-rhythm: one call per final utterance (the consumer's
        STT loop) -- the pause clock the live fires wait on."""
        with self._lock:
            self._last_utterance_at = self.now()

    def coach_tick(self) -> Optional[str]:
        """--coach-rhythm: called from the consumer's 0.5 s ticker.
        Fires START or the one LONG-PAUSE contribution when the room has
        been quiet for COACH_PAUSE_S and the live budget allows it.
        Returns the kind fired (tests/eval), else None."""
        if not self.coach_rhythm:
            return None
        with self._lock:
            kind = self._rhythm_due()
            if kind is None:
                return None
            self._rhythm_tries[kind] += 1
            self._rhythm_inflight = True
            topic_started_at = self._coach_topic_started_at
            entry_id = self._rhythm_entry_id
        self._submit_rhythm(kind, topic_started_at, entry_id,
                            self._topic_header())
        return kind

    def _rhythm_due(self) -> Optional[str]:
        """Caller holds the lock."""
        t = self.now()
        if self._rhythm_inflight or not self._rhythm_entry_id:
            return None
        if t - self._last_utterance_at < COACH_PAUSE_S:
            return None                       # never mid-exchange
        if t < self._rhythm_next_try_at:
            return None
        self._rhythm_spoken = [x for x in self._rhythm_spoken
                               if t - x < 3600.0]
        if len(self._rhythm_spoken) >= COACH_MAX_PER_HOUR:
            return None
        if self._rhythm_spoken and t - self._rhythm_spoken[-1] < COACH_MIN_GAP_S:
            return None
        age = t - self._coach_topic_started_at
        if age >= COACH_LONG_TOPIC_S:
            phase = "pause"
        elif self._coach_eligible and age >= self.coach_min_delay_s:
            phase = "start"
        else:
            return None
        if (phase == "pause" and self._coach_eligible
                and self._rhythm_tries["start"] == 0):
            # A new topic that never had its opening pause: its first
            # quiet moment is still an opening, not a "long pause".
            phase = "start"
        if self._rhythm_done[phase] or self._rhythm_tries[phase] >= COACH_MAX_TRIES:
            return None
        return phase

    def _fire_rhythm_end(self) -> None:
        with self._lock:
            entry_id = self._rhythm_entry_id
            topic_started_at = self._coach_topic_started_at
            age = self.now() - topic_started_at
        if not entry_id or age < self.coach_min_delay_s:
            return
        self._submit_rhythm("end", topic_started_at, entry_id,
                            self._topic_header())

    def _submit_rhythm(self, kind: str, topic_started_at: float,
                       entry_id: str, header: str) -> None:
        self.log(f"coach lane: rhythm {kind} for topic {entry_id or '?'}")
        prompt = self.coach_ctx.build_prompt(
            f"{header}\n{COACH_RHYTHM_REQUESTS[kind]}\n{COACH_REQUEST}")
        self.coach_lane.submit(Job(
            id=self._next_job_id(f"coach-{kind}"), prompt=prompt,
            timeout=self.coach_turn_timeout_s,
            meta={"topic_started_at": topic_started_at,
                 "topic_entry_id": entry_id, "rhythm": kind}))

    def _on_coach_dropped(self, job: Job) -> None:
        self.log(f"coach lane: {job.id} superseded")
        self._rhythm_settle(job, spoken=False)

    def _rhythm_settle(self, job: Job, *, spoken: bool) -> None:
        """A rhythm job ended. [SILENT]/failure consumes no slot: only a
        retry pause; a spoken live fire starts the min gap and counts
        toward the hourly cap. END never counts (it lands in the closed
        topic's history, it does not speak into the running round)."""
        kind = job.meta.get("rhythm")
        if not kind:
            return
        with self._lock:
            if kind == "end":
                return
            self._rhythm_inflight = False
            if job.meta.get("topic_started_at") != self._coach_topic_started_at:
                return                        # its topic is gone already
            if spoken:
                self._rhythm_done[kind] = True
                self._rhythm_spoken.append(self.now())
            else:
                self._rhythm_next_try_at = self.now() + COACH_RETRY_S

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

    def _on_coach_result(self, job: Job, result: dict, duration: float) -> None:
        """Files the coach's reply into ITS topic's history entry
        (point 6b) -- never a new entry, never the mirror. A topic
        change while the coach worked (point 6c) makes this a Nachtrag
        on that OLDER entry; the currently displayed entry (`main`) is
        untouched either way, since this never writes to `main` unless
        the topic in question still IS `main`."""
        if _is_timeout(result):
            # Not shown on the primary stage: nobody asked the coach
            # anything, so an "abgebrochen" line there would be a
            # surprise interruption of exactly the kind this stage's
            # design (N1-N5) exists to avoid. Logged, and filed as a
            # failed attempt for the record.
            self.log("coach lane timed out, worker killed")
            self._rhythm_settle(job, spoken=False)
            self._file_history(job, result, stale=False)
            return
        entry_id = job.meta.get("topic_entry_id") or ""
        text = (result.get("final") or "").strip()
        if not result.get("ok") or not _valid_coach_reply(text):
            self.log("coach lane: no usable reply:",
                     result.get("error") or text[:COACH_MAX_CHARS])
            self._rhythm_settle(job, spoken=False)
            self._file_history(job, result, stale=False)
            return
        if text == COACH_SILENT:
            # Silence is the normal case (the coach's own profile: "an
            # unrequested voice costs attention"), not logged as a
            # failure -- but logged, so a run's measurement can tell
            # "fired and chose silence" apart from "never fired at all"
            # (2026-10-20 real replay: both looked identical without
            # this line, decisions-*.jsonl had to be replayed offline
            # through TopicHysteresis to even confirm eligibility fired).
            self.log("coach lane: [SILENT]")
            self._rhythm_settle(job, spoken=False)
            return
        self._rhythm_settle(job, spoken=True)
        with self._lock:
            current_started_at = self._coach_topic_started_at
            current_id = self._current_topic_id
        stale = job.meta.get("topic_started_at") != current_started_at
        at = datetime.now().astimezone().strftime("%H:%M")
        addition = (f"**Nachtrag {at}:** {text}" if stale else text)
        if job.meta.get("rhythm") == "end":
            # The closing structure of THIS entry's own topic, not a late
            # answer -- labelled as what it is.
            addition = f"**Abschluss {at}:** {text}"
        if not entry_id or not self.state.append_to_history(entry_id, addition):
            # The entry aged out of MAX_HISTORY, or none was ever pushed
            # (the brake refused it) -- fall back to the JSONL record so
            # the contribution is not silently lost.
            self._file_history(job, result, stale=stale)
            return
        self._broadcast("Robo (Coach)", text)
        try:
            self.writer.write()
        except Exception as e:                # noqa: BLE001
            self.log("lane: coach result not written:", repr(e))
        if stale and entry_id != current_id and job.meta.get("rhythm") != "end":
            title = self.state.history_title(entry_id) or "?"
            self.log(f"coach contribution filed as a Nachtrag on an older "
                     f"topic ({title!r}), not shown as current")
            self._note_nachtrag(title)

    def _note_nachtrag(self, title: str) -> None:
        """Point 6d: a small, collapsible notice for a late result that
        landed on a topic no longer shown as current. Several pending
        Nachtraege collapse into one line; cleared by the next genuinely
        new topic (`on_decider_result`). Paging back to the entry itself
        cannot clear it -- the stage has no channel for "which entry is
        a viewer looking at right now" (plain HTTP polling, no session
        state), so that half of the spec is NOT implemented; documented,
        not guessed at.
        """
        with self._lock:
            self._pending_nachtraege.append(title)
            pending = list(self._pending_nachtraege)
        try:
            current = self.writer.warning()
        except Exception as e:                # noqa: BLE001
            self.log("lane: nachtrag notice skipped:", repr(e))
            return
        if current and not current.startswith(NACHTRAG_PREFIX):
            # Never clobber someone else's warning (conflicts, a failed
            # protocol write, ...) -- the notice simply waits; the next
            # successful write (this method runs again on the next
            # Nachtrag, or _clear_nachtrag_notice() on the next topic)
            # gets another chance once that warning clears on its own.
            return
        if len(pending) == 1:
            text = f'{NACHTRAG_PREFIX}zu „{pending[0]}" — ← zum Anzeigen'
        else:
            text = (f"{len(pending)} {NACHTRAG_PREFIX}e: "
                   + ", ".join(f'„{t}"' for t in pending))
        try:
            self.writer.warn(text)
            self.writer.write()
            self._last_nachtrag_text = text
        except Exception as e:                # noqa: BLE001
            self.log("lane: nachtrag notice not written:", repr(e))

    def _clear_nachtrag_notice(self) -> None:
        with self._lock:
            had_pending = bool(self._pending_nachtraege)
            self._pending_nachtraege = []
            last_text, self._last_nachtrag_text = self._last_nachtrag_text, ""
        if not had_pending or not last_text:
            return
        try:
            # `only=` (not an unconditional clear): if some OTHER warning
            # has since taken the slot, this must not clobber it either.
            self.writer.clear_warning(only=last_text)
            self.writer.write()
        except Exception as e:                # noqa: BLE001
            self.log("lane: nachtrag notice not cleared:", repr(e))

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
        consumer's ticker, same cadence as `coach_tick()`.

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

    def _file_history(self, job: Job, result: dict, *, stale: bool) -> None:
        if not self.history_path:
            return
        try:
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "at": datetime.now().astimezone().isoformat(),
                    "stale": stale, "ok": bool(result.get("ok")),
                    "final": result.get("final") or "",
                    "topic_started_at": job.meta.get("topic_started_at"),
                }, ensure_ascii=False) + "\n")
        except OSError as e:                  # noqa: BLE001
            self.log("coach history not written:", repr(e))
