# CoThinker — blocks/condense: condense what was said per topic
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The condense block (ADR 0010): one short text per topic, quotes anchored
in the transcript, no topic dropped silently, written in the session language.

The checks run in code, not in the prompt:
  * a quote that is not a verbatim substring of a segment is dropped;
  * a topic without a usable text is retried ONCE (one retry for the whole
    call, covering only the missing topics), then listed in `not_condensed`;
  * the language of the condensed `text` must match the session language
    (`shared.language_guard`). A text in the wrong language counts like a
    missing topic: it shares the single retry and, if still wrong, the topic
    lands in `not_condensed`. Quotes are NOT language-checked; speakers mix
    languages and a quote stays as spoken.

The block sees the model only as `shared.llm.LlmPort` (`ctx.llm`) and the
session language as `ctx.language`.

Events consumed:
  * `segment`      payload {"id", "text", ...}: buffered for the open topic.
  * `block_output` payload {"topic": {"id": str, "title": str, "closed"?: bool}}:
    a topic event opens that topic; the previously open topic (or, with
    `"closed": True`, this one) is condensed from the segments buffered
    since it opened. Other `block_output` events (including this block's own)
    are ignored.
Events emitted, per condensed topic:
  * ctx.emit(ViewIntent("condense:<topic_id>", "condense", "artefact", "room",
    {"topic_id", "title", "text", "quotes"})) when a text exists;
  * CoreEvent("block_output", {"from": "condense", "condensed":
    {"topic_id", "text", "quotes", "not_condensed"}}).
A topic that closes with no buffered segments emits the `block_output` with
empty text and `not_condensed: [topic_id]` (no LLM call, no view intent).
An LLM exception emits nothing (the failure goes to `ctx.trace`); an exception
on the retry also discards the first call's results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from blocks.condense.coverage import missing_topics, parse_reply
from blocks.condense.quotes import verbatim_quotes
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.language_guard import matches_language
from shared.llm import LlmPort
from shared.modules import CoreEvent
from shared.surface import ViewIntent

DEFAULT_PROMPT = "prompts/condense.{lang}.md"
_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "reflect"


@dataclass(frozen=True)
class TopicCondensed:
    topic_id: str
    text: str
    quotes: tuple


@dataclass(frozen=True)
class Condensed:
    text: str                                  # per-topic texts joined by a blank line
    quotes: list                               # verbatim quotes of all covered topics
    topics_covered: list                       # topic ids with a usable text
    not_condensed: list = field(default_factory=list)   # topic ids that stayed uncovered
    by_topic: dict = field(default_factory=dict)         # topic_id -> TopicCondensed


def load_prompt(language: str, template: str = DEFAULT_PROMPT,
                template_dir: Optional[Path] = None) -> str:
    base = Path(template_dir) if template_dir is not None else _TEMPLATE_DIR
    return (base / template.format(lang=language)).read_text(encoding="utf-8")


def _user_message(segments: Sequence[Mapping], topics: Sequence[Mapping], language: str) -> str:
    return json.dumps({
        "language": language,
        "topics": [{"topic_id": t["id"], "title": t.get("title", "")} for t in topics],
        "segments": [{"id": s.get("id"), "text": s["text"]} for s in segments],
    }, ensure_ascii=False)


def condense(segments: Sequence[Mapping], topics: Sequence[Mapping], language: str,
             llm: LlmPort, *, system: Optional[str] = None,
             log: Optional[Callable[[str], None]] = None) -> Condensed:
    """Condenses `segments` (mappings with "id", "text") per topic (mappings
    with "id", "title"). Raises whatever `llm.complete` raises. `log` gets
    event names only (`condense_quote_dropped`, `condense_wrong_language`),
    never conversation text. The language check ignores the topic's
    verbatim quotes."""
    system = system if system is not None else load_prompt(language)
    seg_texts = [s["text"] for s in segments]
    ids = [t["id"] for t in topics]
    got: dict[str, TopicCondensed] = {}
    pending = list(topics)
    for _attempt in (1, 2):                   # first try + exactly one retry
        if not pending:
            break
        raw = llm.complete(system, _user_message(segments, pending, language))
        parsed = parse_reply(raw)
        for t in pending:
            item = parsed.get(t["id"])
            if not item or not item["text"]:
                continue
            kept = verbatim_quotes(item["quotes"], seg_texts, log)
            prose = item["text"]
            for q in kept:                     # a quote stays as spoken: not language-checked
                prose = prose.replace(q, " ")
            if not matches_language(prose, language):
                if log:
                    log("condense_wrong_language")
                continue
            got[t["id"]] = TopicCondensed(t["id"], item["text"], tuple(kept))
        pending = [t for t in topics if t["id"] in missing_topics(ids, got)]
    covered = [i for i in ids if i in got]
    quotes: list = []
    for i in covered:
        quotes.extend(q for q in got[i].quotes if q not in quotes)
    return Condensed(
        text="\n\n".join(got[i].text for i in covered), quotes=quotes,
        topics_covered=covered, not_condensed=missing_topics(ids, got), by_topic=got)


class CondenseBlock:
    id = "condense"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._system: Optional[str] = None
        self._open: Optional[dict] = None
        self._segments: list = []

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        """`config["prompt"]` (template.toml) is the prompt path with a `{lang}`
        placeholder, relative to `config["template_dir"]` (default: the
        repository's templates/reflect)."""
        self._ctx = ctx
        template_dir = config.get("template_dir")
        self._system = load_prompt(
            ctx.language, str(config.get("prompt", DEFAULT_PROMPT)),
            Path(template_dir) if template_dir else None)
        self._open, self._segments = None, []
        if ctx.llm is None:                    # e.g. no API key: make it visible
            self._trace("condense_no_llm", {})

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "segment":
            if self._open is not None and str(ev.payload.get("text", "")).strip():
                self._segments.append({"id": ev.payload.get("id"), "text": str(ev.payload["text"])})
            return ()
        if ev.kind != "block_output" or not isinstance(ev.payload.get("topic"), Mapping):
            return ()
        topic = ev.payload["topic"]
        if "id" not in topic:
            return ()
        topic = {"id": str(topic["id"]), "title": str(topic.get("title", ""))}
        done: Optional[dict] = None
        if ev.payload["topic"].get("closed"):
            if self._open is not None and self._open["id"] == topic["id"]:
                done, self._open = self._open, None
        elif self._open is None or self._open["id"] != topic["id"]:
            done, self._open = self._open, topic
        if done is None:
            return ()
        segments, self._segments = self._segments, []
        return self._condense_topic(done, segments)

    def stop(self) -> None:
        self._ctx = None

    def _trace(self, event: str, fields: Mapping) -> None:
        if self._ctx is not None and self._ctx.trace is not None:
            self._ctx.trace(event, fields)

    def _condense_topic(self, topic: dict, segments: list) -> Sequence[CoreEvent]:
        ctx = self._ctx
        if ctx is None:
            return ()
        if not segments:                       # no topic is dropped: report it, no LLM call
            return (CoreEvent("block_output", {"from": self.id, "condensed": {
                "topic_id": topic["id"], "text": "", "quotes": [],
                "not_condensed": [topic["id"]]}}),)
        try:
            res = condense(segments, [topic], ctx.language, ctx.llm,
                           system=self._system,
                           log=lambda event: self._trace(event, {"id": topic["id"]}))
        except Exception as e:  # noqa: BLE001 -- LLM failure: emit nothing
            # Only allow-listed, token-shaped fields reach the trace file
            # (shared/events.py); never conversation text.
            self._trace("condense_llm_failed", {"id": topic["id"], "kind": type(e).__name__})
            return ()
        if res.not_condensed:
            self._trace("condense_not_condensed", {"id": topic["id"], "count": len(res.not_condensed)})
        item = res.by_topic.get(topic["id"])
        if item is not None:
            ctx.emit(ViewIntent(
                id=f"condense:{topic['id']}", block=self.id, kind="artefact", audience="room",
                payload={"topic_id": topic["id"], "title": topic["title"],
                         "text": item.text, "quotes": list(item.quotes)}))
        return (CoreEvent("block_output", {"from": self.id, "condensed": {
            "topic_id": topic["id"], "text": res.text, "quotes": res.quotes,
            "not_condensed": res.not_condensed}}),)


def make_block() -> CondenseBlock:
    return CondenseBlock()
