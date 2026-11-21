# CoThinker — shared/blocks.py: block contract and block host (stdlib only)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The block contract (ADR 0008) and the host that runs a template's blocks.

A block has a small fixed external API: `start`, `on_event`, `stop`. It
talks to other blocks only through the `CoreEvent`s the host routes, and it
writes only through its `BlockContext` (`state`, `artefact`, `emit`). The
host owns the session folder.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence

from shared.modules import CoreEvent, StateStore

BLOCK_API_VERSION = 1
MAX_FANOUT_DEPTH = 8
# "block" and "cue" stay valid for module API v1 listeners (workshop/hooks.py).
EVENT_KINDS = frozenset({"utterance", "segment", "phase", "action", "tick",
                         "block_output", "agent_reply", "block", "cue"})


class BlockError(Exception):
    pass


@dataclass
class BlockContext:
    instance: object                       # shared.instance.InstanceConfig (None in tests)
    session_dir: Path
    state: StateStore
    agent: object                          # shared.agent.AgentPort | None
    llm: object                            # LLM client | None
    language: str
    clock: Callable[[], float]
    emit: Callable[[object], None]         # shared.surface.ViewIntent -> None
    artefact: Callable[[str, str], None]   # (name, text), written by the host


class Block(Protocol):
    id: str
    api_version: int

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None: ...

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]: ...

    def stop(self) -> None: ...


class BlockHost:
    def __init__(self, blocks: Sequence[Block], *, ctx_for: Callable[[str], BlockContext],
                 log: Callable[[str], None] = print) -> None:
        ids = [b.id for b in blocks]
        if len(ids) != len(set(ids)):
            raise BlockError(f"duplicate block ids: {ids}")
        self._blocks = list(blocks)
        self._ctx_for = ctx_for
        self._log = log
        self._active: Optional[set[str]] = None   # None = all

    def start(self, configs: Mapping[str, Mapping[str, object]]) -> None:
        for b in self._blocks:
            if b.api_version != BLOCK_API_VERSION:
                raise BlockError(f"block {b.id!r}: api_version {b.api_version} != {BLOCK_API_VERSION}")
        for b in self._blocks:
            b.start(self._ctx_for(b.id), configs.get(b.id, {}))

    def set_active(self, ids: Sequence[str]) -> None:
        self._active = set(ids)

    def dispatch(self, ev: CoreEvent) -> int:
        """Routes `ev` to every active block, then their outputs, breadth first.
        Returns the number of block calls. A block that raises is logged and
        skipped; it never stops the others."""
        calls = 0
        queue = deque([(ev, 0)])
        while queue:
            cur, depth = queue.popleft()
            if cur.kind not in EVENT_KINDS:
                raise ValueError(f"unknown event kind {cur.kind!r}")
            if depth >= MAX_FANOUT_DEPTH:
                self._log(f"block host: fan-out depth {depth} reached, dropping {cur.kind}")
                continue
            for b in self._blocks:
                if self._active is not None and b.id not in self._active:
                    continue
                calls += 1
                try:
                    out = b.on_event(cur) or ()
                except Exception as e:  # noqa: BLE001 -- isolation is the point
                    self._log(f"block {b.id}: {type(e).__name__}: {e}")
                    continue
                for o in out:
                    queue.append((o, depth + 1))
        return calls

    def stop(self) -> None:
        for b in self._blocks:
            try:
                b.stop()
            except Exception as e:  # noqa: BLE001
                self._log(f"block {b.id}: stop failed: {e}")
