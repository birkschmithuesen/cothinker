# CoThinker — phase engine: every transition recorded, every transition reversible
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Core phase engine (ADR 0009).

The history file is the source of truth: on construction the engine replays
`history_path` (JSON lines) and derives the current phase from it; nothing
else is persisted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class PhaseSpec:
    id: str
    titles: Mapping[str, str]
    blocks: tuple
    surfaces: tuple
    goal: Mapping[str, str] = field(default_factory=dict)
    enter_when: tuple = ()
    exit_when: tuple = ()


@dataclass(frozen=True)
class PhaseChange:
    from_id: str
    to_id: str
    cause: str
    at: float


class PhaseEngine:
    def __init__(self, phases: Sequence[PhaseSpec], history_path: Path,
                 clock: Callable[[], float]) -> None:
        if not phases:
            raise ValueError("a template needs at least one phase")
        self._phases = {p.id: p for p in phases}
        self._first = phases[0].id
        self._path = Path(history_path)
        self._clock = clock
        self._history: list[PhaseChange] = []
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    self._history.append(PhaseChange(d["from"], d["to"], d["cause"], d["at"]))
        self._current = self._history[-1].to_id if self._history else self._first

    def current(self) -> PhaseSpec:
        return self._phases[self._current]

    def history(self) -> list[PhaseChange]:
        return list(self._history)

    def _record(self, to: str, cause: str) -> None:
        ch = PhaseChange(self._current, to, cause, self._clock())
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"from": ch.from_id, "to": ch.to_id, "cause": ch.cause, "at": ch.at}) + "\n")
        self._history.append(ch)
        self._current = to

    def advance(self, to: str, cause: str) -> bool:
        if to not in self._phases:
            raise KeyError(to)
        if to == self._current:
            return False
        self._record(to, cause)
        return True

    def back(self, cause: str = "back") -> bool:
        if not self._history:
            return False
        self._record(self._history[-1].from_id, cause)
        return True
