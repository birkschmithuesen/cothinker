#!/usr/bin/env python3
# CoThinker — gateway/stage_surface.py: the stage renders view intents
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The stage as a Surface (ADR 0006, `docs/architecture/target.md` §2.3, §7;
issue #46, Wave C.1). It replaces the interim legacy sink.

Blocks emit `ViewIntent`s; `render(intents)` receives the CURRENT set (the
`IntentBoard`'s snapshot) on every change and maps each intent, by the
block-id prefix of its `id`, onto the `StageState` / `StageWriter` calls
the stage already had. Each mapping rule below is one row of the intent
table of the migration plan:

    topic:<id>      status    -> rewrite the stage file; a confirmed
                                 retitle swaps the provisional title in
                                 place (the call lane pushes the history
                                 entry itself, not this row)
    pending:<n>     status    -> announce / settle / warn, via the writer
    status:line     status    -> the operating line, clock stamped here
    links:<topic>   artefact  -> `links` (generic sink; no producer since
                                 issue #148's knowledge-link rename, and no
                                 longer read by the stage's "Vertiefen"
                                 sidebar either -- see `knowledge:<id>` below)
    mirror:<n>      prompt    -> file the coach's contribution into the
                                 topic history (or the JSONL record when
                                 it did not land), the Nachtrag notice
    condense:<id>   artefact  -> none (the minutes block writes the text)
    queue:room      queue     -> content()["queue"]
    voice:room      voice     -> content()["voice"]
    share:room      share     -> content()["share"]
    graph:session   graph     -> content()["session_graph"]
    timer:current   timer     -> content()["agenda"]
    knowledge:<id>  knowledge -> content()["knowledge"] (issue #148: the
                                 knowledge-link block's named hit payload,
                                 replacing the old bare-path `links:` kind)
    listen:room     listen    -> content()["listen"] (issue #180, ADR 0024:
                                 the call-window block's state)
    transcript:recent transcript -> content()["transcript_recent"] (issue
                                 #181: the live-transcript block's last 10
                                 finished lines; `transcript:partial`, the
                                 same block's in-progress line, is not
                                 mapped here yet -- it stays an unmapped
                                 kind, logged once and dropped, until a
                                 follow-up wires the panel's live delivery)

Rendering is idempotent: an intent whose payload is unchanged since the
last call is not applied again, so passing the whole snapshot each time is
safe. The writer stays the ONLY writer of `buehne.json`; this class only
changes the state and asks the writer to publish. No clock is read here
and every payload carries absolute data only, so nothing ticking reaches
the stage fingerprint (target.md §9) -- except `status:line`'s own "at"
stamp, read from the wall clock at the moment the intent is applied, the
same contract the interim sink gave the status-line block (issue #41).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from blocks.mirroring_coach.coach import NACHTRAG_PREFIX  # the block owns the constant
from shared.surface import UserAction, ViewIntent

KINDS = frozenset({"prompt", "status", "artefact", "graph", "timer", "queue",
                   "voice", "share", "knowledge", "listen", "transcript"})

# Keys of the agenda the legacy AgendaController owns and the timer intent
# does not carry; they survive a timer render.
_AGENDA_PEOPLE_KEYS = ("people", "speaker", "next_speaker")


def _prefix(intent_id: str) -> str:
    return intent_id.split(":", 1)[0]


class StageSurface:
    name = "stage"
    kinds = KINDS

    def __init__(self, writer: Any, state: Any, log: Callable[[str], None],
                 coach_history_path: Optional[Path] = None) -> None:
        self._writer = writer
        self._state = state
        self._log = log
        self._lock = threading.RLock()
        self._seen: dict[str, Any] = {}        # intent id -> payload last applied
        self._pending_ids: dict[str, str] = {}  # pending intent id -> state id
        self._logged: set[str] = set()
        # `mirror:<n>` row state (ADR 0010, the mirroring coach): a late
        # contribution filed on a topic no longer current raises a notice
        # in the SAME single-slot warning channel as operational warnings
        # (StageWriter.warn()/clear_warning()); every text this row sets
        # starts with NACHTRAG_PREFIX, and it only ever clears a warning
        # that already starts with it, so it never clobbers someone else's.
        self._coach_history_path = Path(coach_history_path) if coach_history_path else None
        self._mirror_pending: list = []        # titles of pending Nachtraege
        self._mirror_last_text = ""            # the notice text this row wrote
        self._rules: dict[str, tuple[str, Callable[[ViewIntent], bool]]] = {
            "topic": ("status", self._topic),
            "pending": ("status", self._pending),
            "status": ("status", self._status),
            "links": ("artefact", self._links),
            "mirror": ("prompt", self._mirror),
            "condense": ("artefact", self._condense),
            "queue": ("queue", self._region("queue")),
            "voice": ("voice", self._region("voice")),
            "share": ("share", self._region("share")),
            "graph": ("graph", self._region("session_graph")),
            "timer": ("timer", self._timer),
            "knowledge": ("knowledge", self._region("knowledge")),
            "listen": ("listen", self._region("listen")),
            "transcript": ("transcript", self._region("transcript_recent")),
        }

    # ------------------------------------------------------------ Surface
    def render(self, intents: Sequence[ViewIntent]) -> None:
        with self._lock:
            changed = False
            present: set[str] = set()
            for intent in intents:
                if intent.kind not in KINDS:
                    self._log_once(f"kind:{intent.kind}",
                                   f"stage surface: dropped intent of unknown kind {intent.kind!r}")
                    continue
                prefix = _prefix(intent.id)
                rule = self._rules.get(prefix)
                if rule is None or rule[0] != intent.kind:
                    self._log_once(f"prefix:{prefix}:{intent.kind}",
                                   f"stage surface: no mapping rule for intent prefix "
                                   f"{prefix!r} of kind {intent.kind!r}")
                    continue
                present.add(intent.id)
                if intent.id in self._seen and self._seen[intent.id] == intent.payload:
                    continue
                try:
                    changed = rule[1](intent) or changed
                    # Only after the rule succeeded: a failed intent is retried.
                    self._seen[intent.id] = intent.payload
                except Exception as e:  # noqa: BLE001 -- a bad payload must not cost the stage
                    self._log(f"stage surface: intent {intent.id!r} not rendered: "
                              f"{type(e).__name__}: {e}")
            changed = self._clear_gone(present) or changed
            if changed:
                self._writer.write()

    def actions(self) -> Iterator[UserAction]:
        """Empty until the tap channel lands (Wave C.3)."""
        return iter(())

    # -------------------------------------------------------------- rules
    def _topic(self, intent: ViewIntent) -> bool:
        """A provisional topic needs only the stage file rewritten: the
        call lane (the ONLY writer of topic history entries, issue #39)
        already pushed the history entry from the block's own `topic`
        output. The final (non-provisional) one swaps the provisional
        title in place of `replaces`, as the call lane's titler callback
        used to."""
        p = intent.payload
        if not p.get("provisional", True):
            old = p.get("replaces")
            if old:
                for entry in reversed(list(self._state.history)):
                    if entry.title == old:
                        self._state.retitle(entry.id, old, str(p["title"]))
                        break
        return True

    def _pending(self, intent: ViewIntent) -> bool:
        """The decisions-tasks-questions block's own announce / settle /
        warn, exactly as the policy runner called them on the stage writer
        before it emitted intents. The intent id is the block's; the
        pending id the writer returned is kept per row (`_pending_ids`),
        so a settle ends the very pending its announce put up."""
        p = intent.payload
        op = p["op"]
        if op == "announce":
            state_id = self._writer.announce(str(p["kind"]), str(p["label"]))
            if not state_id:
                return False
            self._pending_ids[intent.id] = state_id
            return True
        if op == "settle":
            state_id = self._pending_ids.pop(intent.id, "")
            return bool(state_id) and bool(self._writer.settle(state_id, str(p["outcome"])))
        if op == "warn":
            self._writer.warn(str(p["text"]))
            return True
        return False

    def _status(self, intent: ViewIntent) -> bool:
        # The intent never carries a clock value (issue #41): the "zuletzt
        # {at}" timestamp is stamped here, from this call's own clock, not
        # by the status-line block.
        p = intent.payload
        detail = p.get("detail") or {}
        self._writer.set_status(str(p["state"]),
                                at=datetime.now().strftime("%H:%M:%S"),
                                count=detail.get("count", 0),
                                failed=detail.get("failed"))
        return True

    def _links(self, intent: ViewIntent) -> bool:
        """`links:<topic_id>`: dead since issue #148's knowledge-link
        rename -- nothing emits this kind any more, and the stage's own
        "Vertiefen" sidebar now reads `knowledge:<id>` instead (see
        `_region("knowledge")`). The rule stays registered only so an
        intent of this prefix is rejected cleanly rather than falling
        through to the "no mapping rule" log line."""
        items = [{"file": str(path),
                  "label": str(path).rsplit("/", 1)[-1].removesuffix(".md")}
                 for path in intent.payload.get("links", ())]
        return self._state.apply({"action": "links", "items": items}) == ""

    def _mirror(self, intent: ViewIntent) -> bool:
        """`mirror:<n>` (the mirroring coach): files a contribution into
        ITS topic's history entry -- never `state.mirror`, which the call
        lane sets and expires on its own -- plus the Nachtrag notice and
        the JSONL record of attempts that did not land."""
        p = intent.payload
        contribution = p.get("contribution")
        if contribution == "clear":
            return self._mirror_clear_notice()
        if contribution == "failure":
            self._mirror_file_record(p.get("record"))
            return False
        if contribution not in ("reply", "Abschluss", "Nachtrag"):
            return False
        entry_id = str(p.get("topic_id") or "")
        if not entry_id or not self._state.append_to_history(
                entry_id, str(p.get("addition") or "")):
            # The entry aged out of MAX_HISTORY, or none was ever pushed
            # (the brake refused it) -- fall back to the JSONL record so
            # the contribution is not silently lost.
            self._mirror_file_record(p.get("record"))
            return False
        if p.get("notice"):
            title = self._state.history_title(entry_id) or "?"
            self._log(f"stage surface: coach contribution filed as a "
                      f"Nachtrag on an older topic ({title!r}), not "
                      f"shown as current")
            self._mirror_note_nachtrag(title)
        return True

    def _mirror_note_nachtrag(self, title: str) -> None:
        """A late result on a topic no longer current: several pending
        Nachtraege collapse into one line, cleared by the next genuinely
        new topic."""
        self._mirror_pending.append(title)
        pending = list(self._mirror_pending)
        try:
            current = self._writer.warning()
        except Exception as e:                # noqa: BLE001
            self._log(f"stage surface: nachtrag notice skipped: {e!r}")
            return
        if current and not current.startswith(NACHTRAG_PREFIX):
            # Never clobber someone else's warning: the notice waits and
            # gets another chance on the next Nachtrag.
            return
        if len(pending) == 1:
            text = f'{NACHTRAG_PREFIX}zu „{pending[0]}" — ← zum Anzeigen'
        else:
            text = (f"{len(pending)} {NACHTRAG_PREFIX}e: "
                    + ", ".join(f'„{t}"' for t in pending))
        try:
            self._writer.warn(text)
            self._mirror_last_text = text
        except Exception as e:                # noqa: BLE001
            self._log(f"stage surface: nachtrag notice not written: {e!r}")

    def _mirror_clear_notice(self) -> bool:
        had_pending = bool(self._mirror_pending)
        self._mirror_pending = []
        last_text, self._mirror_last_text = self._mirror_last_text, ""
        if not had_pending or not last_text:
            return False
        try:
            # `only=` (not an unconditional clear): if some OTHER warning
            # has since taken the slot, this must not clobber it either.
            self._writer.clear_warning(only=last_text)
        except Exception as e:                # noqa: BLE001
            self._log(f"stage surface: nachtrag notice not cleared: {e!r}")
            return False
        return True

    def _mirror_file_record(self, record: object) -> None:
        if not self._coach_history_path or not isinstance(record, Mapping):
            return
        try:
            with open(self._coach_history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
        except OSError as e:
            self._log(f"stage surface: coach history not written: {e!r}")

    def _condense(self, intent: ViewIntent) -> bool:
        # No stage region shows the condensed text yet; the minutes block
        # writes it into the protocol. Noted once per intent, no change.
        self._log(f"stage surface: {intent.id} noted, nothing to show")
        return False

    def _region(self, attribute: str) -> Callable[[ViewIntent], bool]:
        def rule(intent: ViewIntent) -> bool:
            setattr(self._state, attribute, intent.payload)
            return True
        return rule

    def _timer(self, intent: ViewIntent) -> bool:
        # Until D.4 replaces AgendaRuntime, the legacy AgendaController still
        # sets state.agenda and can overwrite this intent's agenda.
        agenda = dict(intent.payload)
        old = self._state.agenda or {}
        for key in _AGENDA_PEOPLE_KEYS:
            agenda.setdefault(key, old.get(key, [] if key == "people" else None))
        self._state.agenda = agenda
        return True

    # ------------------------------------------------------------ helpers
    def _clear_gone(self, present: set[str]) -> bool:
        """Region intents that left the board leave the stage."""
        changed = False
        for intent_id in [i for i in self._seen if i not in present]:
            del self._seen[intent_id]
            attribute = {"queue": "queue", "voice": "voice", "share": "share",
                         "graph": "session_graph",
                         "knowledge": "knowledge",
                         "listen": "listen"}.get(_prefix(intent_id))
            if attribute is not None and getattr(self._state, attribute) is not None:
                setattr(self._state, attribute, None)
                changed = True
        return changed

    def _log_once(self, key: str, line: str) -> None:
        if key not in self._logged:
            self._logged.add(key)
            self._log(line)
