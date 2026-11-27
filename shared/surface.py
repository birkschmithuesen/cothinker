# CoThinker — Surface API: view intents, user actions, and the intent board
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Surface API (ADR 0006): view intents, user actions, and the intent board.

Generic code describes what should be shown as a `ViewIntent` and reads back
what a person did as a `UserAction`; a `Surface` renders intents for its own
kind of device and reports actions back. The `IntentBoard` holds the current
set of intents and hands each `Surface` only the kinds it understands.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol, Sequence

INTENT_KINDS = frozenset(
    {
        "prompt",
        "choice_list",
        "timer",
        "status",
        "artefact",
        "queue",
        "voice",
        "share",
        "graph",
        "knowledge",
        "listen",
        "transcript",
        "transcript_partial",
        "tasks",
    }
)

ACTION_KINDS = frozenset(
    {"choose", "accept", "reject", "edit", "tap", "voice", "message"}
)

_AUDIENCE_RE = re.compile(r"^(room|operator|person:[A-Za-z0-9_-]{1,32})$")


@dataclass(frozen=True)
class ViewIntent:
    id: str
    block: str
    kind: str
    audience: str
    payload: Any
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in INTENT_KINDS:
            raise ValueError(f"unknown intent kind: {self.kind!r}")
        if not _AUDIENCE_RE.match(self.audience):
            raise ValueError(f"invalid audience: {self.audience!r}")


@dataclass(frozen=True)
class UserAction:
    intent_id: str
    kind: str
    person: str
    payload: Any
    at: float

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {self.kind!r}")


class Surface(Protocol):
    name: str
    kinds: frozenset

    def render(self, intents: Sequence[ViewIntent]) -> None: ...

    def actions(self) -> Iterator[UserAction]: ...


class IntentBoard:
    """Blocks emit from more than one thread (a decider's `on_result`, an
    agent turn's take_turn/end_turn, the SSE reader, the ticker) -- `put`
    and `current` touch the SAME plain dict, and `current` both iterates
    and mutates it (expiring entries). Without a lock, a `put` from one
    thread racing a `current` sweep on another can corrupt the dict or
    drop an entry: an announce or a settle that loses that race never
    reaches the surface, and its pending can freeze into the final stage
    file (issue #184). A leaf lock -- nothing in here calls back out."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._intents: dict[str, ViewIntent] = {}
        self._logged_unrenderable_ids: set[str] = set()
        self._lock = threading.Lock()

    def put(self, intent: ViewIntent) -> None:
        with self._lock:
            self._intents[intent.id] = intent

    def remove(self, intent_id: str) -> None:
        with self._lock:
            self._intents.pop(intent_id, None)

    def current(self) -> list[ViewIntent]:
        now = self._clock()
        with self._lock:
            expired = [
                intent_id
                for intent_id, intent in self._intents.items()
                if intent.expires_at is not None and intent.expires_at <= now
            ]
            for intent_id in expired:
                del self._intents[intent_id]
            return list(self._intents.values())

    def for_surface(
        self, kinds: frozenset, log: Callable[[str], None]
    ) -> list[ViewIntent]:
        result = []
        for intent in self.current():
            if intent.kind in kinds:
                result.append(intent)
            elif intent.id not in self._logged_unrenderable_ids:
                self._logged_unrenderable_ids.add(intent.id)
                log(
                    f"intent {intent.id!r} of kind {intent.kind!r} "
                    "cannot be rendered by this surface"
                )
        return result
