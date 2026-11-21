# CoThinker — blocks/topics: topic detection, hysteresis and titling
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `topics` building block (issue #39, Wave B.2): topic-change
detectors (`detect`), the confirmation hysteresis (`hysteresis`), the laya
worker subprocess (`laya_worker`) and the asynchronous titler (`title`).

`TopicsBlock` consumes `block_output` events whose payload carries a
`decider_result` (the session host sends them from its decider callback
until the decisions block takes that over):

    {"decider_result": {"neues_thema": p | None, "rueckkehr_thema": p | None,
                        "block": str, "previous": str}}

It runs the hysteresis; on a confirmed NEW topic it emits the view intent `topic:<id>` (kind `status`, audience
`room`) and the event `block_output {"topic": {"id", "title", "since",
"provisional"}}` (a confirmed return emits only the event
`block_output {"topic": {"return": true, "since"}}`, no intent). With `titles` on, the titler's answer arrives later as a
second `topic:<id>` intent with `provisional: false`; a titler failure
keeps the provisional title.

Config (`[[blocks]] id = "topics"`): `threshold`, `min_topic_age_s`
(hysteresis, defaults = the measured 2026-10-20 values) and `titles`
(model-made titles, default off).
"""
from __future__ import annotations

import threading
from typing import Callable, Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent
from shared.text import derived_title, one_line

from .hysteresis import MIN_TOPIC_AGE_S, THRESHOLD, TopicHysteresis

TitlerFactory = Callable[[Callable, Callable], object]


def _default_titler(on_title: Callable, on_fail: Callable) -> object:
    from .title import TopicTitler
    return TopicTitler(on_title, on_fail=on_fail)


class TopicsBlock:
    id = "topics"
    api_version = BLOCK_API_VERSION

    def __init__(self, titler_factory: TitlerFactory = _default_titler) -> None:
        self._titler_factory = titler_factory
        self._ctx: Optional[BlockContext] = None
        self._hysteresis: Optional[TopicHysteresis] = None
        self._titler = None
        self._lock = threading.Lock()
        self._topics: dict[str, dict] = {}
        self._counter = 0

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._hysteresis = TopicHysteresis(
            threshold=float(config.get("threshold", THRESHOLD)),
            min_topic_age_s=float(config.get("min_topic_age_s", MIN_TOPIC_AGE_S)),
            now=ctx.clock)
        if config.get("titles", False):
            self._titler = self._titler_factory(self._on_title, self._on_title_failed)

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "block_output":
            return ()
        result = ev.payload.get("decider_result")
        if result is None or self._hysteresis is None:
            return ()
        decision = self._hysteresis.on_result(
            result.get("neues_thema"), result.get("rueckkehr_thema"))
        if not decision.changed:
            return ()
        if decision.returned:
            # A confirmed return opens no topic and no view intent; the
            # output only lets the lanes reset their per-topic state.
            return (CoreEvent("block_output", {"topic": {
                "return": True, "since": self._ctx.clock()}}),)
        block_text = str(result.get("block") or "")
        title = derived_title(block_text) or one_line(block_text) or "Thema"
        with self._lock:
            self._counter += 1
            topic = {"id": f"t{self._counter}", "title": title,
                     "since": self._ctx.clock(), "provisional": self._titler is not None}
            self._topics[topic["id"]] = topic
        self._emit(topic)
        if self._titler is not None:
            self._titler.request(topic["id"], title, block_text,
                                 str(result.get("previous") or ""))
        return (CoreEvent("block_output", {"topic": dict(topic)}),)

    def stop(self) -> None:
        if self._titler is not None:
            self._titler.stop()

    def _emit(self, topic: dict) -> None:
        self._ctx.emit(ViewIntent(id=f"topic:{topic['id']}", block=self.id,
                                  kind="status", audience="room", payload=dict(topic)))

    def _on_title(self, entry_id: str, old: str, new: str, ms: int) -> None:
        """Titler thread: the usable title replaces the provisional one."""
        with self._lock:
            topic = self._topics.get(entry_id)
            if topic is None or not topic["provisional"]:
                return
            topic = {**topic, "title": new, "provisional": False, "replaces": old}
            self._topics[entry_id] = topic
        self._emit(topic)

    def _on_title_failed(self, entry_id: str, token: str, ms: int) -> None:
        """The provisional title stays (the titler already logged it)."""
