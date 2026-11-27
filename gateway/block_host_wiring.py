#!/usr/bin/env python3
# CoThinker — gateway/block_host_wiring.py: the session host's block host
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Builds the session runtime the session host (`gateway/consumer.py`)
feeds every session event to (issue #38, Wave B.1; issue #28).

Reflect is the first template (ADR 0010) and every building block plugs
into the block host (ADR 0008, `docs/architecture/target.md` §5). There is
no template flag and no off switch (migration rule, ADR 0018): the
session host always builds its runtime from `REFLECT_TEMPLATE`. Block id
`x-y` is built by package `blocks.x_y`'s `make_block()`
(`shared.block_registry`); a block id the template lists but no package
provides is skipped, with one log line, rather than failing the whole
session.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

from blocks.mirroring_coach.coach import resolve_config
from gateway.infomaniak_chat import ChatError, Client as InfomaniakClient, api_key
from gateway.sync_safe import atomic_write_text
from shared.block_registry import load_block
from shared.blocks import Block
from shared.session_runtime import RuntimeConfig, SessionRuntime
from shared.surface import ViewIntent
from shared.templates import load_template, merged_block_config

REFLECT_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "reflect" / "template.toml"

# Moves to the instance's text-model configuration (target.md §9).
CONDENSE_MODEL = "google/gemma-4-31B-it"

# The condense block's reply shape (blocks/condense/coverage.parse_reply);
# the EU text endpoint only accepts a strict json_schema response format.
_CONDENSE_SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {
        "type": "object",
        "properties": {"topic_id": {"type": "string"},
                       "text": {"type": "string"},
                       "quotes": {"type": "array", "items": {"type": "string"}}},
        "required": ["topic_id", "text", "quotes"],
        "additionalProperties": False}}},
    "required": ["topics"],
    "additionalProperties": False,
}


class InfomaniakLlm:
    """`shared.llm.LlmPort` over the EU text client (`gateway/infomaniak_chat.py`).

    The model is `CONDENSE_MODEL`. Raises `ChatError` on failure; the
    client already rejects an empty reply.
    """

    def __init__(self, client=None, *, model: Optional[str] = None,
                 schema: Optional[dict] = None) -> None:
        self._client = client if client is not None else InfomaniakClient()
        self._model = model if model is not None else CONDENSE_MODEL
        self._schema = schema if schema is not None else _CONDENSE_SCHEMA

    def complete(self, system: str, user: str, *, timeout_s: float = 60) -> str:
        reply = self._client.chat(self._model, system, user, self._schema,
                                  schema_name="condense", max_tokens=2048,
                                  request_timeout_s=timeout_s)
        if not reply.text.strip():
            raise ChatError("answer carries no content", kind="format")
        return reply.text


def default_llm_for(block_id: str) -> Optional[object]:
    """The LLM the host hands a block; only condense has one so far, and
    only when an Infomaniak key is configured."""
    if block_id == "condense" and api_key():
        return InfomaniakLlm()
    return None

_ARTEFACT_NAME_RE = re.compile(r"^[a-z0-9-]+\.md$")


def _make_artefact(session_dir: Path, block_id: str) -> Callable[[str, str], None]:
    def artefact(name: str, text: str) -> None:
        if not _ARTEFACT_NAME_RE.match(name):
            raise ValueError(
                f"artefact name {name!r} must match {_ARTEFACT_NAME_RE.pattern!r}")
        path = session_dir / "artefacts" / block_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text)
    return artefact


def build_session_blocks(
    *,
    session_dir: Path,
    sink: Callable[[ViewIntent], None],
    log: Callable[[str], None],
    trace: Optional[Callable[[str, Mapping], None]] = None,
    agent_for: Optional[Callable[[str], object]] = None,
    llm_for: Optional[Callable[[str], object]] = None,
    clock: Callable[[], float] = time.time,
    template_path: Path = REFLECT_TEMPLATE,
    load: Callable[[str], Block] = load_block,
    block_configs: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> SessionRuntime:
    session_dir = Path(session_dir)
    if llm_for is None:
        llm_for = default_llm_for
    runtime = SessionRuntime(RuntimeConfig(
        template=load_template(template_path), session_dir=session_dir,
        language="de", agent_for=agent_for, llm_for=llm_for, sink=sink,
        artefact_for=lambda block_id: _make_artefact(session_dir, block_id),
        trace=trace, clock=clock, log=log, block_configs=block_configs), load=load)
    runtime.start()
    return runtime


def feed_utterance(minutes, blocks: SessionRuntime, text: str,
                   speaker: Optional[str], meta: Mapping[str, object]) -> None:
    """Feeds one STT final into the session: the raw transcript first,
    then the block host.

    `minutes` is the session host's `MinutesFile`. `minutes.append_raw`
    runs FIRST and unguarded -- the transcript is the evidence, the event
    is a convenience, and a raw-append failure must propagate exactly as
    it did before this wiring existed. A
    dispatch failure, in contrast, is logged and swallowed: a block
    breaking must never cost a line of the transcript.
    """
    minutes.append_raw(text, speaker)
    payload = {
        "text": text,
        "speaker": speaker,
        "turn_id": meta.get("turn_id"),
        "t_start": meta.get("t_start"),
        "t_end": meta.get("t_end"),
        "at": meta.get("at"),
    }
    try:
        blocks.dispatch("utterance", payload)
    except Exception as e:  # noqa: BLE001 -- belt and suspenders, dispatch() already never raises
        blocks.log(f"feed_utterance: dispatch failed: {type(e).__name__}: {e}")


# -------------------------------------------------------- call window
def wire_call_window(blocks: SessionRuntime, bb) -> None:
    """Gives the `call-window` block its host handle onto the session's
    real `gateway.segmenter.BlockBuilder` (issue #180, ADR 0024) --
    interim host handle, same pattern as `knowledge-link`'s
    `use_project` (see that block's own docstring). A no-op when the
    template does not carry the block (`bb` typed loosely to avoid this
    module importing `gateway.segmenter` for a type hint alone)."""
    block = blocks.get("call-window")
    if block is None:
        return
    block.bind_host(open_call=bb.open_call, close_call=bb.close_call, state=bb.state)


def dispatch_call_window_state(blocks: SessionRuntime, bb) -> None:
    """Call after every `bb.sentence()`/`bb.tick()` (and once after
    `wire_call_window`) so a voice-triggered transition reaches the
    `listen:room` view intent exactly like a tap-triggered one does --
    the block only reacts to events, it never polls the segmenter."""
    blocks.dispatch("block_output", {"call_window": bb.state()})


# ------------------------------------------------------------ agent lanes
COACH_TURN_TIMEOUT_S = 60.0


class LaneQueuePort:
    """A `shared.lane_port.LanePort` over one agent lane: today's
    `lanes.LaneQueue` around a `worker.WorkerClient`, or -- with a lane
    factory (`COTHINKER_AGENT=api`, `gateway/agent_lane.py`) -- the agent
    seam, same lane API either way.

    `submit` wraps the prompt into a `Job` carrying its own callbacks, so one
    port serves one block and every job answers to its own submitter. The
    lane keeps its rule: one running, one waiting, newest wins. `start`,
    `stop` and `on_roll` go beyond the port: the session host starts the
    worker at once (never lazily on the first job) and a seam lane opens a
    fresh session after a window roll.
    """

    def __init__(self, *, command: list, session_id: str,
                 turn_timeout_s: float = COACH_TURN_TIMEOUT_S,
                 lane_factory: Optional[Callable] = None, briefing: str = "",
                 log: Callable[..., None] = print) -> None:
        gateway_dir = str(Path(__file__).resolve().parent)
        if gateway_dir not in sys.path:
            sys.path.insert(0, gateway_dir)
        import lanes as _lanes
        self._lanes = _lanes
        self.turn_timeout_s = turn_timeout_s
        self.log = log
        # Newest wins -- a superseded request is simply obsolete, nobody
        # waits for it.
        if lane_factory is not None:
            self.lane = lane_factory(
                session_id, on_result=self._on_result,
                on_dropped=self._on_dropped, briefing=briefing)
        else:
            from worker import WorkerClient
            self.lane = _lanes.LaneQueue(
                WorkerClient(command=command, session_id=session_id, log=log),
                on_result=self._on_result, on_dropped=self._on_dropped,
                log=log)

    def submit(self, job_id: str, prompt: str, *,
               on_result: Callable[[dict, float], None],
               on_dropped: Callable[[], None]) -> None:
        self.lane.submit(self._lanes.Job(
            id=job_id, prompt=prompt, timeout=self.turn_timeout_s,
            meta={"on_result": on_result, "on_dropped": on_dropped}))

    def stats(self) -> dict:
        return dict(self.lane.stats())

    def start(self) -> bool:
        return self.lane.start()

    def stop(self) -> None:
        self.lane.stop()

    def on_roll(self) -> None:
        on_roll = getattr(self.lane, "on_roll", None)
        if on_roll is not None:
            on_roll()

    def _on_result(self, job, result: dict, duration: float) -> None:
        result = dict(result)
        if self._lanes.TIMEOUT_MARKER in str(result.get("error") or ""):
            result["timed_out"] = True
        try:
            job.meta["on_result"](result, duration)
        except Exception as e:                # noqa: BLE001 -- the lane lives on
            self.log("agent lane: result callback failed:", repr(e))

    def _on_dropped(self, job) -> None:
        try:
            job.meta["on_dropped"]()
        except Exception as e:                # noqa: BLE001
            self.log("agent lane: dropped callback failed:", repr(e))


def coach_block_config(template_path: Path = REFLECT_TEMPLATE) -> dict:
    """The mirroring coach's block configuration: the template's keys over
    the block's defaults (the former coach command-line flags)."""
    template = load_template(template_path)
    return resolve_config(merged_block_config(template, None).get("mirroring-coach"))


def make_agent_for(*, coach_command: list, session_id: str,
                   lane_factory: Optional[Callable] = None,
                   coach_briefing: str = "",
                   log: Callable[..., None] = print) -> Callable[[str], object]:
    """`build_session_blocks(agent_for=...)`: the `LanePort` each block gets
    as `BlockContext.agent`. Only the mirroring coach has an agent lane; it
    is built the way the coach lane always was -- its own worker process
    and session id (`<session>-coach`)."""
    def agent_for(block_id: str):
        if block_id == "mirroring-coach":
            return LaneQueuePort(
                command=coach_command, session_id=f"{session_id}-coach",
                lane_factory=lane_factory, briefing=coach_briefing, log=log)
        return None
    return agent_for
