#!/usr/bin/env python3
# CoThinker — shared/session_runtime.py: one runtime per session
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Block host + phase engine + intent board for one session (issue #28).

The meeting's session host builds it through
`gateway.block_host_wiring.build_session_blocks()`; other templates build
it directly. Blocks come from `shared.block_registry.load_block` (or an
injected `load`). Block state lives in `session_dir/.blocks/<id>`; a block
whose template config says `state_scope = "group"` keeps it in
`group_dir/.blocks/<id>`, so it outlives the session. Artefacts go to
`session_dir/artefacts/<block id>/<name>`.

A template with ONE phase (the meeting) gets no phase event, no phase
intent and no phase tools, and every block stays active: for it the
runtime behaves exactly like the block host it replaces. `dispatch` takes
no lock, as before -- the session host dispatches from several threads and
`collect` is per thread.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from shared.block_registry import load_block
from shared.blocks import Block, BlockContext, BlockError, BlockHost
from shared.modules import CoreEvent, StateStore, ToolSpec
from shared.phases import PhaseEngine
from shared.surface import IntentBoard, Surface, UserAction, ViewIntent
from shared.templates import BlockRef, Template, merged_block_config

ARTEFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PHASE_BAR = "phase:bar"
STATE_DIR = ".blocks"

ArtefactWriter = Callable[[str, str], None]


@dataclass
class RuntimeConfig:
    template: Template
    session_dir: Path
    instance: object = None
    group_dir: Optional[Path] = None
    language: str = "de"
    agent_for: Optional[Callable[[str], object]] = None
    llm_for: Optional[Callable[[str], object]] = None
    sink: Optional[Callable[[ViewIntent], None]] = None
    artefact_for: Optional[Callable[[str], ArtefactWriter]] = None
    trace: Optional[Callable[[str, Mapping], None]] = None
    clock: Callable[[], float] = time.time
    log: Callable[[str], None] = print
    block_configs: Optional[Mapping[str, Mapping[str, object]]] = None


class SessionRuntime:
    def __init__(self, cfg: RuntimeConfig, surfaces: Sequence[Surface] = (),
                 load: Callable[[str], Block] = load_block,
                 select: Optional[Callable[[BlockRef], bool]] = None) -> None:
        self.cfg = cfg
        self.log = cfg.log
        self._lock = threading.RLock()
        self._collect_tl = threading.local()
        self._listeners: list[Callable[[Mapping], None]] = [self._on_collect]
        self.surfaces = list(surfaces)
        self.intents = IntentBoard(cfg.clock)
        self._phased = len(cfg.template.phases) > 1
        refs = [r for r in cfg.template.blocks if select is None or select(r)]
        self._configs = merged_block_config(cfg.template, cfg.instance)
        for block_id, extra in (cfg.block_configs or {}).items():
            self._configs[block_id] = {**self._configs.get(block_id, {}), **extra}
        for ref in refs:
            scope = self._configs.get(ref.id, {}).get("state_scope", "session")
            if scope not in ("session", "group"):
                raise BlockError(f"block {ref.id!r}: unknown state_scope {scope!r}")
            if scope == "group" and cfg.group_dir is None:
                raise BlockError(f"block {ref.id!r}: state_scope 'group' needs a group_dir")
        blocks, missing = [], []
        for ref in refs:
            try:
                blocks.append(load(ref.id))
            except BlockError:
                cfg.log(f"block host: no block registered for '{ref.id}'")
                missing.append(ref.id)
        self.missing_blocks = tuple(missing)
        self._blocks = {b.id: b for b in blocks}
        if self._phased:
            Path(cfg.session_dir, STATE_DIR).mkdir(parents=True, exist_ok=True)
        self.host = BlockHost(blocks, ctx_for=self._ctx_for, log=cfg.log, clock=cfg.clock,
                              observe=self._observe)
        self.phases = PhaseEngine(cfg.template.phases,
                                  Path(cfg.session_dir) / STATE_DIR / "phase-history.jsonl", cfg.clock)

    # -- contexts -------------------------------------------------------
    def _state_root(self, block_id: str) -> Path:
        if self._configs.get(block_id, {}).get("state_scope", "session") == "group":
            return Path(self.cfg.group_dir) / STATE_DIR / block_id
        return Path(self.cfg.session_dir) / STATE_DIR / block_id

    def _emit(self, intent: ViewIntent) -> None:
        self.intents.put(intent)
        if self.cfg.sink is not None:
            self.cfg.sink(intent)

    def _artefact_writer(self, block_id: str) -> ArtefactWriter:
        def artefact(name: str, text: str) -> None:
            if not ARTEFACT_NAME.match(name):
                raise ValueError(f"invalid artefact name {name!r}")
            out = Path(self.cfg.session_dir) / "artefacts" / block_id
            out.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=out, prefix=f".{name}.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, out / name)
        return artefact

    def _ctx_for(self, block_id: str) -> BlockContext:
        c = self.cfg
        writer = c.artefact_for(block_id) if c.artefact_for is not None else self._artefact_writer(block_id)
        return BlockContext(instance=c.instance, session_dir=Path(c.session_dir),
                            state=StateStore(self._state_root(block_id)),
                            agent=c.agent_for(block_id) if c.agent_for is not None else None,
                            llm=c.llm_for(block_id) if c.llm_for is not None else None,
                            language=c.language, clock=c.clock, emit=self._emit,
                            artefact=writer, trace=c.trace)

    # -- output listeners -------------------------------------------------
    def _observe(self, ev: CoreEvent) -> None:
        if ev.kind != "block_output":
            return
        for fn in list(self._listeners):
            try:
                fn(ev.payload)
            except Exception as e:  # noqa: BLE001 -- a listener never breaks dispatch
                self.log(f"block host: output listener failed: {type(e).__name__}: {e}")

    def _on_collect(self, payload) -> None:
        seen = getattr(self._collect_tl, "seen", None)
        if seen is not None and hasattr(payload, "get") and payload.get("from"):
            seen.append(dict(payload))

    # -- the interface the meeting's session host uses -------------------
    def get(self, block_id: str) -> Optional[Block]:
        return self._blocks.get(block_id)

    def on_output(self, fn: Callable[[Mapping], None]) -> None:
        self._listeners.append(fn)

    def dispatch(self, kind: str, payload: Mapping[str, object]) -> int:
        """Never raises: an unknown kind or a host-level failure is logged
        and counted as zero block calls."""
        try:
            return self.host.dispatch(CoreEvent(kind, payload))
        except Exception as e:  # noqa: BLE001 -- see the docstring above
            self.log(f"block host: dispatch of {kind!r} failed: {type(e).__name__}: {e}")
            return 0

    def collect(self, kind: str, payload: Mapping[str, object],
                from_id: str) -> list[dict]:
        """Dispatches one event and returns what block `from_id` answered
        with, in order. Dispatch is synchronous, so the answers reach the
        listeners on this very thread before `dispatch` returns; each
        thread collects only its own."""
        outer = getattr(self._collect_tl, "seen", None)
        self._collect_tl.seen = []
        try:
            self.dispatch(kind, payload)
            got = self._collect_tl.seen
        finally:
            self._collect_tl.seen = outer
        return [o for o in got if o.get("from") == from_id]

    def stop(self) -> None:
        self.host.stop()

    # -- lifecycle and phases -------------------------------------------------
    def start(self) -> None:
        with self._lock:
            self.host.start(self._configs)
            if self._phased:
                self._enter(None, "start")

    def _enter(self, prev: Optional[str], cause: str) -> None:
        cur = self.phases.current()
        self.host.set_active(cur.blocks)
        self._emit(ViewIntent(id=PHASE_BAR, block="phase", kind="status", audience="room", payload={
            "current": cur.id,
            "phases": [{"id": p.id, "titles": dict(p.titles)} for p in self.cfg.template.phases]}))
        self.dispatch("phase", {"from": prev, "to": cur.id, "cause": cause})

    def advance(self, to: str, cause: str) -> bool:
        with self._lock:
            prev = self.phases.current().id
            if not self.phases.advance(to, cause):
                return False
            self._enter(prev, cause)
            return True

    def back(self, cause: str = "back") -> bool:
        with self._lock:
            prev = self.phases.current().id
            if not self.phases.back(cause):
                return False
            self._enter(prev, cause)
            return True

    def act(self, action: UserAction) -> None:
        with self._lock:
            if self._phased and action.intent_id == PHASE_BAR:
                cause = action.person or "person"
                if action.payload.get("back"):
                    self.back(cause)
                else:
                    self.advance(str(action.payload["to"]), cause)
                return
        self.dispatch("action", asdict(action))

    def tick(self) -> None:
        self.dispatch("tick", {"at": self.cfg.clock()})

    def render(self) -> None:
        with self._lock:
            for s in self.surfaces:
                s.render(self.intents.for_surface(s.kinds, self.log))

    # -- tools ------------------------------------------------------------------
    def _phase_tools(self) -> list[ToolSpec]:
        if not self._phased:
            return []

        def phase_status():
            cur = self.phases.current()
            return {"ok": True, "warnings": [], "rev": None, "error": None, "data": {
                "current": cur.id, "titles": dict(cur.titles), "goal": dict(cur.goal),
                "phases": [p.id for p in self.cfg.template.phases]}}

        def set_phase(to):
            try:
                moved = self.advance(to, "agent")
            except KeyError:
                return {"ok": False, "data": {}, "warnings": [], "rev": None, "error": f"unknown_phase: {to}"}
            return {"ok": True, "data": {"current": self.phases.current().id},
                    "warnings": [] if moved else ["already in this phase"], "rev": None, "error": None}

        return [
            ToolSpec("phase_status", "Current phase, its goal and the phase list.",
                     {"type": "object", "properties": {}}, phase_status, read_only=True),
            ToolSpec("set_phase", "Move to another phase. Only when the group asked for it.",
                     {"type": "object", "properties": {"to": {"type": "string"}}, "required": ["to"]},
                     set_phase),
        ]

    def tools(self) -> list[ToolSpec]:
        with self._lock:
            return [s for _, s in self.host.tools()] + self._phase_tools()

    def call_tool(self, name: str, args: Mapping[str, object]) -> dict:
        with self._lock:
            for spec in self._phase_tools():
                if spec.name == name:
                    return spec.fn(**dict(args))
            return self.host.call_tool(name, args)

    def tool_calls_since(self, t: float) -> list[dict]:
        with self._lock:
            return self.host.tool_calls_since(t)
