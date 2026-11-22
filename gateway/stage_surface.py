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

    topic:<id>      status    -> headline (provisional) / confirmed topic
    pending:<n>     status    -> announce / settle a pending entry
    status:line     status    -> the operating line
    links:<topic>   artefact  -> `links`
    mirror:<n>      prompt    -> `mirror`
    condense:<id>   artefact  -> none (the minutes block writes the text)
    queue:room      queue     -> content()["queue"]
    share:room      share     -> content()["share"]
    graph:session   graph     -> content()["session_graph"]
    timer:current   timer     -> content()["agenda"]

Rendering is idempotent: an intent whose payload is unchanged since the
last call is not applied again, so passing the whole snapshot each time is
safe. The writer stays the ONLY writer of `buehne.json`; this class only
changes the state and asks the writer to publish. No clock is read here
and every payload carries absolute data only, so nothing ticking reaches
the stage fingerprint (target.md §9).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Iterator, Sequence

from shared.surface import UserAction, ViewIntent

KINDS = frozenset({"prompt", "status", "artefact", "graph", "timer", "queue", "share"})

# Keys of the agenda the legacy AgendaController owns and the timer intent
# does not carry; they survive a timer render.
_AGENDA_PEOPLE_KEYS = ("people", "speaker", "next_speaker")


def _prefix(intent_id: str) -> str:
    return intent_id.split(":", 1)[0]


class StageSurface:
    name = "stage"
    kinds = KINDS

    def __init__(self, writer: Any, state: Any, log: Callable[[str], None]) -> None:
        self._writer = writer
        self._state = state
        self._log = log
        self._lock = threading.RLock()
        self._seen: dict[str, Any] = {}        # intent id -> payload last applied
        self._pending_ids: dict[str, str] = {}  # pending intent id -> state id
        self._logged: set[str] = set()
        self._status_payload: Any = None
        self._rules: dict[str, tuple[str, Callable[[ViewIntent], bool]]] = {
            "topic": ("status", self._topic),
            "pending": ("status", self._pending),
            "status": ("status", self._status),
            "links": ("artefact", self._links),
            "mirror": ("prompt", self._mirror),
            "condense": ("artefact", self._condense),
            "queue": ("queue", self._region("queue")),
            "share": ("share", self._region("share")),
            "graph": ("graph", self._region("session_graph")),
            "timer": ("timer", self._timer),
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
        p = intent.payload
        title = str(p["title"])
        if p.get("provisional"):
            return self._state.apply({"action": "headline", "text": title}) == ""
        return self._state.confirmed_topic(title, title) == ""

    def _pending(self, intent: ViewIntent) -> bool:
        p = intent.payload
        if p.get("settled"):
            state_id = self._pending_ids.pop(intent.id, "")
            return bool(state_id) and self._state.settle(state_id)
        # A relabelled, still unsettled entry replaces its predecessor:
        # the old one is settled so it does not linger or lose its id.
        old_id = self._pending_ids.pop(intent.id, "")
        if old_id:
            self._state.settle(old_id)
        # The writer path publishes immediately and traces pending_start.
        state_id = self._writer.announce(str(p["pending_kind"]), str(p["label"]))
        if not state_id:
            return bool(old_id)
        self._pending_ids[intent.id] = state_id
        return True

    def _status(self, intent: ViewIntent) -> bool:
        p = intent.payload
        self._writer.set_status(str(p["state"]), at=str(p.get("detail") or ""))
        self._status_payload = p
        return True

    def status_line(self) -> Any:
        """The payload of the current `status:line` intent, None when there
        is none. The consumer's own operating-line publisher defers to it,
        so there is one writer of the line while an intent is current."""
        with self._lock:
            return self._status_payload

    def _links(self, intent: ViewIntent) -> bool:
        items = [{"file": str(path), "label": str(path)}
                 for path in intent.payload["links"]]
        return self._state.apply({"action": "links", "items": items}) == ""

    def _mirror(self, intent: ViewIntent) -> bool:
        return self._state.apply({"action": "mirror",
                                  "text": intent.payload["text"]}) == ""

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
            if _prefix(intent_id) == "status":
                self._status_payload = None
            attribute = {"queue": "queue", "share": "share",
                         "graph": "session_graph"}.get(_prefix(intent_id))
            if attribute is not None and getattr(self._state, attribute) is not None:
                setattr(self._state, attribute, None)
                changed = True
        return changed

    def _log_once(self, key: str, line: str) -> None:
        if key not in self._logged:
            self._logged.add(key)
            self._log(line)
