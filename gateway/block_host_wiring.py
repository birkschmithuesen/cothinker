#!/usr/bin/env python3
# CoThinker — gateway/block_host_wiring.py: the session host's block host
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Builds the block host the session host (`gateway/consumer.py`) feeds
every session event to (issue #38, Wave B.1).

Reflect is the first template (ADR 0010) and every building block plugs
into the block host (ADR 0008, `docs/architecture/target.md` §5). There is
no template flag and no off switch (migration rule, ADR 0018): the
session host always builds its block host from `REFLECT_TEMPLATE`. The
template already names all twelve Reflect blocks; `REGISTRY` only grows
one line per block as each one's own pull request lands, so a block id
the template lists but nobody has registered yet is skipped, with one log
line, rather than failing the whole session.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

from blocks.decisions_tasks_questions import DecisionsBlock
from blocks.knowledge_link import KnowledgeLinkBlock
import blocks.status_line as status_line_block
from blocks.condense import CondenseBlock
from blocks.topics import TopicsBlock
from gateway.infomaniak_chat import ChatError, Client as InfomaniakClient, api_key
from blocks.minutes_document import MinutesDocumentBlock
from gateway.sync_safe import atomic_write_text
from shared.blocks import BLOCK_API_VERSION, Block, BlockContext, BlockHost
from shared.modules import CoreEvent, StateStore
from shared.surface import IntentBoard, ViewIntent
from shared.templates import load_template, merged_block_config

REFLECT_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "reflect" / "template.toml"

# One line per block, added by the pull request that builds it.
REGISTRY: dict[str, Callable[[], Block]] = {
    "decisions-tasks-questions": DecisionsBlock,
    "knowledge-link": KnowledgeLinkBlock,
    "status-line": status_line_block.StatusLineBlock,
    "minutes-document": MinutesDocumentBlock,
    "topics": TopicsBlock,
}

REGISTRY["condense"] = CondenseBlock

HOST_LISTENER_ID = "session-host"

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


class _OutputListenerBlock:
    """Forwards every `block_output` to this session's `on_output` listeners.

    Not named in any template -- it is the host's own ear, not a block a
    template author places.
    """
    id = HOST_LISTENER_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._listeners: list[Callable[[Mapping], None]] = []

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        pass

    def on_event(self, ev: CoreEvent):
        if ev.kind == "block_output":
            for fn in list(self._listeners):
                fn(ev.payload)
        return ()

    def stop(self) -> None:
        pass

    def add_listener(self, fn: Callable[[Mapping], None]) -> None:
        self._listeners.append(fn)


class SessionBlocks:
    """The session's block host plus the pieces `gateway/consumer.py` and
    the legacy stage sink need around it."""

    def __init__(self, *, host: BlockHost, intents: IntentBoard,
                by_id: Mapping[str, object], log: Callable[[str], None]) -> None:
        self.host = host
        self.intents = intents
        self.log = log
        self._by_id = dict(by_id)
        self._listener = self._by_id[HOST_LISTENER_ID]

    def get(self, block_id: str) -> Optional[object]:
        return self._by_id.get(block_id)

    def dispatch(self, kind: str, payload: Mapping[str, object]) -> int:
        """Never raises: an unknown kind or a host-level failure is logged
        and counted as zero block calls."""
        try:
            return self.host.dispatch(CoreEvent(kind, payload))
        except Exception as e:  # noqa: BLE001 -- see the docstring above
            self.log(f"block host: dispatch of {kind!r} failed: {type(e).__name__}: {e}")
            return 0

    def on_output(self, fn: Callable[[Mapping], None]) -> None:
        self._listener.add_listener(fn)

    def stop(self) -> None:
        self.host.stop()


def _make_emit(intents: IntentBoard, sink: Callable[[ViewIntent], None]) -> Callable[[ViewIntent], None]:
    def emit(intent: ViewIntent) -> None:
        intents.put(intent)
        sink(intent)
    return emit


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
    registry: Mapping[str, Callable[[], Block]] = REGISTRY,
) -> SessionBlocks:
    session_dir = Path(session_dir)
    if llm_for is None:
        llm_for = default_llm_for
    template = load_template(template_path)

    listener = _OutputListenerBlock()
    by_id: dict[str, object] = {HOST_LISTENER_ID: listener}
    block_list: list = [listener]
    for ref in template.blocks:
        factory = registry.get(ref.id)
        if factory is None:
            log(f"block host: no block registered for '{ref.id}'")
            continue
        block = factory()
        by_id[block.id] = block
        block_list.append(block)

    intents = IntentBoard(clock=clock)

    def ctx_for(block_id: str) -> BlockContext:
        return BlockContext(
            instance=None,
            session_dir=session_dir,
            state=StateStore(session_dir / ".blocks" / block_id),
            agent=agent_for(block_id) if agent_for is not None else None,
            llm=llm_for(block_id) if llm_for is not None else None,
            language="de",
            clock=clock,
            emit=_make_emit(intents, sink),
            artefact=_make_artefact(session_dir, block_id),
            trace=trace,
        )

    host = BlockHost(block_list, ctx_for=ctx_for, log=log)
    host.start(merged_block_config(template, None))
    return SessionBlocks(host=host, intents=intents, by_id=by_id, log=log)


def feed_utterance(minutes, blocks: SessionBlocks, text: str,
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
