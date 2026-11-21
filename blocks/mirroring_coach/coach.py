# CoThinker — blocks/mirroring_coach/coach.py: the mirroring coach block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The mirroring coach (ADR 0010, one of Reflect's twelve blocks).

A coach that listens to the room and, at most once per topic (or with
`coach_rhythm` at a topic's start, its end and in one long pause), offers a
mirror, a structure or a question -- or stays silent. It was the coach
lane of `gateway/lane_integration.py`; the call lane (answers when the room
addresses the assistant by its wake word) stayed in the session host
(`gateway/call_lane.py`).

Events in (`BlockHost.dispatch`):

- `segment` -- one finished block of speech: `text`, `speaker`,
  `wake_word`, and optionally `agent_text` (the prefiltered text the agent
  should see; falls back to `text`). Wake-word segments belong to the call
  lane and are ignored.
- `utterance` -- one STT final: the pause clock of the rhythm.
- `block_output` -- from another block or the host: `topic` (a confirmed
  topic: `id`, `title`, `returned`, `coach_eligible`; `retitled` renames a
  known one), `robo_line` (`speaker`, `text`: the assistant's own output,
  appended to the window as an ordinary speaker line, never agent to
  agent) and `stable_prefix` (the project/agenda text; may change when the
  intro round ends).
- `tick` -- the host's clock: `coach_tick` (rhythm).

Events out: `ViewIntent("mirror:<n>", kind="prompt", audience="room")` per
contribution. The payload carries what the host's stage sink needs to file
it where it always went (the topic's history entry): `contribution`
("reply" | "Abschluss" | "Nachtrag" | "failure" | "clear"), `topic_id`,
`topic_title`, `text` (the display line), `line` (the plain reply),
`addition` (the entry's markdown), `notice` (a Nachtrag on an older topic)
and, for a failed attempt, `record`. The agent is reached only through `ctx.agent`, a `LanePort`
(`shared/lane_port.py`); with `ctx.agent` None (no lanes) the block is
inert. The block never writes the stage itself: the host's stage sink maps
the intent onto the stage.

The template's `grounding` rule text is appended to the coach briefing (the
stable prefix of every prompt, `docs/architecture/target.md` §6).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Mapping, Optional, Sequence

from blocks.mirroring_coach.context import ROLL_EVERY, WINDOW_SIZE, LaneContext
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

_log = logging.getLogger("blocks.mirroring_coach")

BLOCK_ID = "mirroring-coach"

# Operator's point 3 (2026-10-20): the coach's full reply is EITHER exactly
# this token OR starts with one of these labels, and is <= this many chars.
COACH_SILENT = "[SILENT]"
COACH_LABELS = ("Spiegel:", "Struktur:", "Frage:")
COACH_MAX_CHARS = 600

COACH_REQUEST = (
    "Trage zum laufenden Thema bei, wenn es etwas gibt beizutragen -- "
    "sonst nicht. Antworte NUR mit genau \"[SILENT]\", oder beginne "
    "deine ganze Antwort mit genau einem der Label \"Spiegel:\", "
    "\"Struktur:\" oder \"Frage:\", gefolgt von deinem Beitrag. "
    "Hoechstens 600 Zeichen insgesamt.")

# The template's `grounding` rule (docs/architecture/target.md §6), in the
# language of the briefing: facts only from block or tool results.
GROUNDING_RULE = (
    "Regel \"grounding\": Nenne Fakten ueber das System, das Material und "
    "den gespeicherten Stand nur aus Block- oder Werkzeugergebnissen, nie "
    "aus der eigenen Erinnerung an das Gespraech.")

COACH_MIN_DELAY_S = 45.0

# Rhythm (operator, 2026-10-20, default off): the coach helps at the START
# of a topic (orient), at its END (structure the closed topic) and at most
# once more in a long pause -- fostering the discussion, never
# interrupting. Live fires (start, long pause) only happen in a pause with
# no utterance for COACH_PAUSE_S.
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

# A contribution that lands after its topic is no longer the current one is
# a Nachtrag: labelled as such, with the old topic's title.
NACHTRAG_PREFIX = "Nachtrag "

COACH_SPEAKER = "Robo (Coach)"

# Block configuration. The first three keys (`lane_coach_delay`,
# `coach_rhythm`, `lane_coach_worker_command`) replace the former session-host
# coach settings, same names, same defaults; the rest are the block's own
# knobs.
DEFAULT_CONFIG: Mapping[str, object] = {
    "lane_coach_delay": COACH_MIN_DELAY_S,
    "coach_rhythm": False,
    "lane_coach_worker_command": "",
    "grounding": GROUNDING_RULE,
    "project_agenda": "",
    "window_size": WINDOW_SIZE,
    "roll_every": ROLL_EVERY,
}


def resolve_config(config: Optional[Mapping[str, object]]) -> dict:
    """`DEFAULT_CONFIG` with the template's / instance's keys on top."""
    out = dict(DEFAULT_CONFIG)
    out.update(config or {})
    return out


def valid_coach_reply(text: str) -> bool:
    """Operator's point 3 output contract, checked before anything of a
    coach reply is trusted enough to go anywhere near the stage."""
    text = text.strip()
    if text == COACH_SILENT:
        return True
    return (len(text) <= COACH_MAX_CHARS
            and any(text.startswith(label) for label in COACH_LABELS))


def speaker_lines(speaker: str, text: str) -> list:
    """--speaker-split: a segment whose text carries several lines of
    `Name: ...` (one per speaker turn) goes into the window as one
    utterance PER LINE under its own name. Single-line text stays one
    utterance under `speaker`."""
    if "\n" not in text:
        return [(speaker, text)]
    out: list = []
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


class MirroringCoach:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._agent = None
        self._config = resolve_config(None)
        self.context = LaneContext(project_agenda=GROUNDING_RULE)
        self._project_agenda = ""
        self._lock = threading.Lock()           # the state below
        self._ctx_lock = threading.Lock()       # the LaneContext window
        self._min_delay_s = COACH_MIN_DELAY_S
        self._rhythm = False
        self._topic_started_at = 0.0
        self._eligible = False
        self._contributed = False
        self._topic_id = ""
        self._topic_title = ""
        self._titles: dict = {}                 # entry id -> title
        self._last_utterance_at = 0.0
        self._rhythm_inflight = False
        self._rhythm_spoken: list = []          # times of spoken live fires
        self._rhythm_tries = {"start": 0, "pause": 0}
        self._rhythm_done = {"start": False, "pause": False}
        self._rhythm_next_try_at = 0.0
        # The id of the RUNNING topic, only when the host knows it: a
        # confirmed return carries none, so it stays "" then.
        self._rhythm_entry_id = ""
        self._job_counter = 0
        self._intent_counter = 0
        self._nachtrag_pending = False    # a Nachtrag notice may be showing
        self._contributions: list = []          # the artefact's lines

    # ---------------------------------------------------------- lifecycle
    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._agent = ctx.agent
        cfg = self._config = resolve_config(config)
        self._min_delay_s = float(cfg["lane_coach_delay"])
        self._rhythm = bool(cfg["coach_rhythm"])
        self._project_agenda = str(cfg["project_agenda"] or "")
        self.context = LaneContext(
            project_agenda=self.briefing(),
            window_size=int(cfg["window_size"]),
            roll_every=int(cfg["roll_every"]))
        self._last_utterance_at = ctx.clock()
        if self._agent is None:
            return
        starter = getattr(self._agent, "start", None)
        if starter is not None:
            # Starts the worker NOW (~19 s to become ready), never lazily on
            # the first topic, which would put that latency in front of the
            # room.
            _log.info("mirroring coach: agent lane started=%s", starter())

    def stop(self) -> None:
        stopper = getattr(self._agent, "stop", None)
        if stopper is not None:
            stopper()

    def briefing(self) -> str:
        """The coach briefing: the project/agenda text, with the template's
        `grounding` rule appended. It is the stable prefix of every coach
        prompt (cache hits: it changes at most once per meeting)."""
        grounding = str(self._config.get("grounding") or "").strip()
        parts = [self._project_agenda.strip(), grounding]
        return "\n\n".join(p for p in parts if p)

    def stats(self) -> dict:
        base = dict(self._agent.stats()) if self._agent is not None else {}
        base["cache_read_ratio"] = self.context.last_cache_read_ratio
        return base

    # ------------------------------------------------------------- events
    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if self._ctx is None or self._agent is None:
            return ()
        kind = ev.kind
        if kind == "segment":
            self._on_segment(ev.payload)
        elif kind == "utterance":
            with self._lock:
                self._last_utterance_at = self._ctx.clock()
        elif kind == "block_output":
            self._on_block_output(ev.payload)
        elif kind == "tick":
            self.coach_tick()
        return ()

    def _on_segment(self, payload: Mapping[str, object]) -> None:
        if payload.get("wake_word"):
            return                     # a call: the call lane's, not ours
        text = str(payload.get("agent_text") or payload.get("text") or "")
        speaker = str(payload.get("speaker") or "unbekannt")
        for line_speaker, line_text in speaker_lines(speaker, text):
            self._add_utterance(line_speaker, line_text)
        if not self._rhythm:
            self._maybe_fire_coach()

    def _on_block_output(self, payload: Mapping[str, object]) -> None:
        if payload.get("from") == self.id:
            return
        topic = payload.get("topic")
        if isinstance(topic, Mapping):
            self._on_topic(topic)
        line = payload.get("robo_line")
        if isinstance(line, Mapping):
            self._add_utterance(str(line.get("speaker") or ""),
                                str(line.get("text") or ""))
        prefix = payload.get("stable_prefix")
        if isinstance(prefix, str):
            self.update_stable_prefix(prefix)

    def update_stable_prefix(self, project_agenda: str) -> None:
        """The stable prefix (project/agenda text) may change exactly once,
        when the intro round ends and enrolled speaker names are known --
        an accepted cache-hit break, never repeated."""
        with self._ctx_lock:
            self._project_agenda = project_agenda
            self.context.project_agenda = self.briefing()

    def _add_utterance(self, speaker: str, text: str) -> None:
        with self._ctx_lock:
            rolled = self.context.add_utterance(speaker, text)
        if rolled:
            _log.info("coach lane: context window rolled, Stand folded")
            on_roll = getattr(self._agent, "on_roll", None)
            if on_roll is not None:
                on_roll()          # a seam lane opens a fresh session next

    # ------------------------------------------------------------- topics
    def _on_topic(self, topic: Mapping[str, object]) -> None:
        entry_id = str(topic.get("id") or "")
        title = str(topic.get("title") or "")
        if topic.get("retitled"):
            with self._lock:
                if entry_id:
                    self._titles[entry_id] = title
                if entry_id and entry_id == self._topic_id:
                    self._topic_title = title
            return
        returned = bool(topic.get("returned"))
        if self._rhythm:
            # END: the topic that just closed gets its structure -- before
            # the new topic is installed, so the header still names it.
            self._fire_rhythm_end()
        clear = False
        with self._lock:
            self._topic_started_at = self._ctx.clock()
            self._eligible = bool(topic.get("coach_eligible")) and not returned
            self._contributed = False
            self._rhythm_tries = {"start": 0, "pause": 0}
            self._rhythm_done = {"start": False, "pause": False}
            self._rhythm_next_try_at = 0.0
            self._rhythm_entry_id = ""
            if returned or not entry_id:
                return
            self._topic_id = entry_id
            self._topic_title = title
            self._rhythm_entry_id = entry_id
            self._titles[entry_id] = title
            # A Nachtrag is about a topic that is no longer current -- the
            # moment a genuinely NEW topic lands, it no longer applies.
            clear, self._nachtrag_pending = self._nachtrag_pending, False
        if clear:
            self._emit("", contribution="clear")

    def _topic_header(self) -> str:
        """The current topic's id/title as a small header on every turn."""
        with self._lock:
            tid, title = self._topic_id, self._topic_title
        if not tid:
            return ""
        return f"[Thema {tid}: {title}]" if title else f"[Thema {tid}]"

    def _next_job_id(self, prefix: str) -> str:
        with self._lock:
            self._job_counter += 1
            return f"{prefix}-{self._job_counter}"

    def _build_prompt(self, request: str) -> str:
        with self._ctx_lock:
            return self.context.build_prompt(request)

    # -------------------------------------------------------- one-shot fire
    def _maybe_fire_coach(self) -> None:
        with self._lock:
            due = (self._eligible and not self._contributed
                   and (self._ctx.clock() - self._topic_started_at)
                   >= self._min_delay_s)
            if not due:
                return
            self._contributed = True
            meta = {"topic_started_at": self._topic_started_at,
                    "topic_entry_id": self._topic_id}
        _log.info("coach lane: firing for topic %s", meta["topic_entry_id"] or "?")
        self._trace("coach_fired", kind="topic")
        prompt = self._build_prompt(f"{self._topic_header()}\n{COACH_REQUEST}")
        self._submit(self._next_job_id("coach"), prompt, meta)

    # ------------------------------------------------------ rhythm / ticks
    def coach_tick(self) -> Optional[str]:
        """Rhythm only: fires START or the one LONG-PAUSE contribution when
        the room has been quiet for COACH_PAUSE_S and the live budget
        allows it. Returns the kind fired (tests/eval), else None."""
        if not self._rhythm or self._ctx is None or self._agent is None:
            return None
        with self._lock:
            kind = self._rhythm_due()
            if kind is None:
                return None
            self._rhythm_tries[kind] += 1
            self._rhythm_inflight = True
            topic_started_at = self._topic_started_at
            entry_id = self._rhythm_entry_id
        self._submit_rhythm(kind, topic_started_at, entry_id,
                            self._topic_header())
        return kind

    def _rhythm_due(self) -> Optional[str]:
        """Caller holds the lock."""
        t = self._ctx.clock()
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
        age = t - self._topic_started_at
        if age >= COACH_LONG_TOPIC_S:
            phase = "pause"
        elif self._eligible and age >= self._min_delay_s:
            phase = "start"
        else:
            return None
        if phase == "pause" and self._eligible and self._rhythm_tries["start"] == 0:
            # A new topic that never had its opening pause: its first
            # quiet moment is still an opening, not a "long pause".
            phase = "start"
        if self._rhythm_done[phase] or self._rhythm_tries[phase] >= COACH_MAX_TRIES:
            return None
        return phase

    def _fire_rhythm_end(self) -> None:
        with self._lock:
            entry_id = self._rhythm_entry_id
            topic_started_at = self._topic_started_at
            age = self._ctx.clock() - topic_started_at
        if not entry_id or age < self._min_delay_s:
            return
        self._submit_rhythm("end", topic_started_at, entry_id,
                            self._topic_header())

    def _submit_rhythm(self, kind: str, topic_started_at: float,
                       entry_id: str, header: str) -> None:
        _log.info("coach lane: rhythm %s for topic %s", kind, entry_id or "?")
        self._trace("coach_fired", kind=kind)
        prompt = self._build_prompt(
            f"{header}\n{COACH_RHYTHM_REQUESTS[kind]}\n{COACH_REQUEST}")
        self._submit(self._next_job_id(f"coach-{kind}"), prompt,
                     {"topic_started_at": topic_started_at,
                      "topic_entry_id": entry_id, "rhythm": kind})

    # ----------------------------------------------------------- the agent
    def _submit(self, job_id: str, prompt: str, meta: dict) -> None:
        self._agent.submit(
            job_id, prompt,
            on_result=lambda result, duration: self._on_result(
                meta, result, duration),
            on_dropped=lambda: self._on_dropped(meta))

    def _on_dropped(self, meta: Mapping[str, object]) -> None:
        _log.info("coach lane: job superseded")
        self._rhythm_settle(meta, spoken=False)

    def _rhythm_settle(self, meta: Mapping[str, object], *, spoken: bool) -> None:
        """A rhythm job ended. [SILENT]/failure consumes no slot: only a
        retry pause; a spoken live fire starts the min gap and counts
        toward the hourly cap. END never counts (it lands in the closed
        topic, it does not speak into the running round)."""
        kind = meta.get("rhythm")
        if not kind:
            return
        with self._lock:
            if kind == "end":
                return
            self._rhythm_inflight = False
            if meta.get("topic_started_at") != self._topic_started_at:
                return                        # its topic is gone already
            if spoken:
                self._rhythm_done[kind] = True
                self._rhythm_spoken.append(self._ctx.clock())
            else:
                self._rhythm_next_try_at = self._ctx.clock() + COACH_RETRY_S

    def _on_result(self, meta: Mapping[str, object], result: dict,
                   duration: float) -> None:
        """Emits the coach's reply for the stage sink to file into ITS
        topic's history entry -- a Nachtrag when its topic is no longer the
        current one, an Abschluss for a rhythm END. Silence shows nothing
        (an unrequested voice costs attention); a failed or unusable
        attempt is emitted as a `failure` record, never shown."""
        if result.get("timed_out"):
            # Nobody asked the coach anything, so an "abgebrochen" line
            # would be a surprise interruption: logged and recorded only.
            _log.info("coach lane timed out, worker killed")
            self._trace("coach_reply", outcome="timeout")
            self._rhythm_settle(meta, spoken=False)
            self._record(meta, result, stale=False)
            return
        text = (result.get("final") or "").strip()
        if not result.get("ok") or not valid_coach_reply(text):
            _log.info("coach lane: no usable reply: %s",
                      result.get("error") or text[:COACH_MAX_CHARS])
            self._trace("coach_reply", outcome="unusable")
            self._rhythm_settle(meta, spoken=False)
            self._record(meta, result, stale=False)
            return
        if text == COACH_SILENT:
            # Silence is the normal case; logged so a run's measurement can
            # tell "fired and chose silence" apart from "never fired".
            _log.info("coach lane: [SILENT]")
            self._trace("coach_reply", outcome="silent")
            self._rhythm_settle(meta, spoken=False)
            return
        self._rhythm_settle(meta, spoken=True)
        entry_id = str(meta.get("topic_entry_id") or "")
        with self._lock:
            current_started_at = self._topic_started_at
            current_id = self._topic_id
            title = self._titles.get(entry_id) or "?"
        stale = meta.get("topic_started_at") != current_started_at
        at = datetime.now().astimezone().strftime("%H:%M")
        addition = text
        md = text
        contribution = "reply"
        if meta.get("rhythm") == "end":
            # The closing structure of THIS entry's own topic, not a late
            # answer -- labelled as what it is.
            contribution = "Abschluss"
            addition = f"Abschluss {at}: {text}"
            md = f"**Abschluss {at}:** {text}"
        elif stale:
            contribution = "Nachtrag"
            addition = f"{NACHTRAG_PREFIX}{at} zu „{title}“: {text}"
            md = f"**Nachtrag {at}:** {text}"
        notice = (contribution == "Nachtrag" and entry_id != current_id)
        self._trace("coach_reply", outcome="spoken")
        self._add_utterance(COACH_SPEAKER, text)
        if notice:
            with self._lock:
                self._nachtrag_pending = True
        self._emit(f"{COACH_SPEAKER}: {addition}", line=text, addition=md,
                   contribution=contribution, topic_id=entry_id,
                   topic_title=title, stale=stale, notice=notice,
                   rhythm=str(meta.get("rhythm") or ""),
                   record=self._attempt(meta, result, stale=stale))
        self._keep(at, addition)

    # ------------------------------------------------------ output / record
    def _emit(self, text: str, **extra: object) -> None:
        with self._lock:
            self._intent_counter += 1
            n = self._intent_counter
        payload: dict = {"text": text, "speaker": COACH_SPEAKER, **extra}
        self._ctx.emit(ViewIntent(
            id=f"mirror:{n}", block=self.id, kind="prompt", audience="room",
            payload=payload))

    def _keep(self, at: str, addition: str) -> None:
        """The session's own record of what the coach said: an artefact the
        host writes into the session folder."""
        with self._lock:
            self._contributions.append(f"- {at} {addition}")
            body = "# Mirroring coach\n\n" + "\n".join(self._contributions) + "\n"
        try:
            self._ctx.artefact("mirroring-coach.md", body)
        except Exception as e:                # noqa: BLE001
            _log.warning("coach record not written: %r", e)

    def _attempt(self, meta: Mapping[str, object], result: dict, *,
                 stale: bool) -> dict:
        return {"at": datetime.now().astimezone().isoformat(),
                "stale": stale, "ok": bool(result.get("ok")),
                "final": result.get("final") or "",
                "topic_started_at": meta.get("topic_started_at")}

    def _record(self, meta: Mapping[str, object], result: dict, *,
                stale: bool) -> None:
        """A failed or unusable attempt, for the record: a `failure`
        intent (the stage sink appends it to the session's JSONL, as the
        lane always did) and the block's own state file."""
        entry = self._attempt(meta, result, stale=stale)
        self._emit("", contribution="failure", record=entry,
                   topic_id=str(meta.get("topic_entry_id") or ""))

        def add(data: dict) -> dict:
            data.setdefault("attempts", []).append(entry)
            return data
        try:
            self._ctx.state.update("coach-history.json", add)
        except Exception as e:                # noqa: BLE001
            _log.warning("coach history not written: %r", e)

    def _trace(self, event: str, **fields: object) -> None:
        trace = self._ctx.trace if self._ctx is not None else None
        if trace is None:
            return
        try:
            trace(event, fields)
        except Exception:                     # noqa: BLE001
            pass
