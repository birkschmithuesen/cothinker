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

import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence

from shared.modules import CoreEvent, StateStore, ToolSpec

BLOCK_API_VERSION = 1
MAX_FANOUT_DEPTH = 8
# "block" and "cue" stay valid for module API v1 listeners (workshop/hooks.py).
EVENT_KINDS = frozenset({"utterance", "segment", "phase", "action", "tick",
                         "block_output", "agent_reply", "block", "cue"})
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


class BlockError(Exception):
    pass


def _check_args(schema: Mapping[str, object], args: Mapping[str, object]) -> Optional[str]:
    """A minimal, stdlib-only check of `args` against a tool's JSON schema:
    every `required` property present, no properties outside `properties`.
    Returns a reason string, or None if `args` is acceptable."""
    properties = schema.get("properties", {})
    for key in schema.get("required", ()):
        if key not in args:
            return f"missing required argument: {key}"
    for key in args:
        if key not in properties:
            return f"unknown argument: {key}"
    return None


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
    trace: Optional[Callable[[str, Mapping], None]] = None  # (event, fields) -> None


class Block(Protocol):
    id: str
    api_version: int

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None: ...

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]: ...

    def stop(self) -> None: ...

    # Optional: a started block may return ToolSpecs the host exposes to the
    # agent. Not part of the Protocol above since most blocks have none.
    # def tools(self) -> Sequence[ToolSpec]: ...


class BlockHost:
    def __init__(self, blocks: Sequence[Block], *, ctx_for: Callable[[str], BlockContext],
                 log: Callable[[str], None] = print, clock: Callable[[], float] = time.time,
                 observe: Optional[Callable[[CoreEvent], None]] = None) -> None:
        ids = [b.id for b in blocks]
        if len(ids) != len(set(ids)):
            raise BlockError(f"duplicate block ids: {ids}")
        self._blocks = list(blocks)
        self._ctx_for = ctx_for
        self._log = log
        self._clock = clock
        self._observe = observe
        self._active: Optional[set[str]] = None   # None = all
        self._tools: list[tuple[str, ToolSpec]] = []
        self._tools_by_name: dict[str, tuple[str, ToolSpec]] = {}
        self._tool_log: list[dict] = []

    def start(self, configs: Mapping[str, Mapping[str, object]]) -> None:
        for b in self._blocks:
            if b.api_version != BLOCK_API_VERSION:
                raise BlockError(f"block {b.id!r}: api_version {b.api_version} != {BLOCK_API_VERSION}")
        for b in self._blocks:
            b.start(self._ctx_for(b.id), configs.get(b.id, {}))
        for b in self._blocks:
            get_tools = getattr(b, "tools", None)
            if get_tools is None:
                continue
            for spec in get_tools() or ():
                if not TOOL_NAME_RE.match(spec.name):
                    raise BlockError(f"block {b.id!r}: bad tool name {spec.name!r}")
                if spec.name in self._tools_by_name:
                    raise BlockError(f"duplicate tool name {spec.name!r}")
                self._tools_by_name[spec.name] = (b.id, spec)
                self._tools.append((b.id, spec))

    def tools(self) -> list[tuple[str, ToolSpec]]:
        """The tools every started block declared, (block id, spec), in block order."""
        return list(self._tools)

    def call_tool(self, name: str, args: Mapping[str, object]) -> dict:
        """Validates `args` against the tool's JSON schema and calls its bound
        function. Never raises: a bad name, bad args or a raising tool all
        come back as an `{"ok": False, ...}` envelope."""
        entry = self._tools_by_name.get(name)
        if entry is None:
            return {"ok": False, "data": {}, "warnings": [], "rev": None,
                     "error": f"unknown_tool: {name}"}
        block_id, spec = entry
        bad_args = _check_args(spec.parameters, args)
        if bad_args is not None:
            return {"ok": False, "data": {}, "warnings": [], "rev": None,
                     "error": f"bad_args: {bad_args}"}
        try:
            raw = spec.fn(**args)
            if not isinstance(raw, Mapping):
                raise TypeError(f"tool returned {type(raw).__name__}, not a mapping")
            result = {
                "ok": bool(raw.get("ok", True)),
                "data": raw.get("data", {}),
                "warnings": list(raw.get("warnings", [])),
                "rev": raw.get("rev"),
                "error": raw.get("error"),
            }
        except Exception as e:  # noqa: BLE001 -- a tool failure must never propagate
            self._log(f"tool {name} (block {block_id}): {type(e).__name__}: {e}")
            result = {"ok": False, "data": {}, "warnings": [], "rev": None,
                      "error": f"tool_failed: {type(e).__name__}"}
            self._record_tool_call(name, block_id, result, spec.read_only)
            return result
        self._record_tool_call(name, block_id, result, spec.read_only)
        if result["ok"] and not spec.read_only:
            self.dispatch(CoreEvent("block_output", {
                "from": block_id,
                "tool_call": {"tool": name, "rev": result["rev"]},
            }))
        return result

    def _record_tool_call(self, name: str, block_id: str, result: dict, read_only: bool) -> None:
        entry = {"tool": name, "block": block_id, "ok": result["ok"], "at": self._clock()}
        if read_only:
            entry["result"] = result
        self._tool_log.append(entry)

    def tool_calls_since(self, t: float) -> list[dict]:
        return [e for e in self._tool_log if e["at"] >= t]

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
            if self._observe is not None:
                self._observe(cur)
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
