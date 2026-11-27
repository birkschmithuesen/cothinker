# CoThinker — blocks/capture_bullets: the main turn's reply as bullets
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `capture-bullets` (issue #49, Wave B.8).

Input: `CoreEvent("agent_reply", {"text": str, "turn": int})`, the main
agent turn's raw reply. The block writes no file (ADR 0005, ADR 0006); it
only emits `block_output` events, all with `"from": "capture-bullets"`:

- `{"kind": "bullet", "section", "text", "topic_id", "turn"}`, one per item
  of verlauf / entscheidungen / aufgaben / offene_fragen.
- `{"kind": "stage_proposal", "turn", "action": {...}}`, one per `buehne`
  item. The session host appends these to the proposal file; it stays the
  only writer of the proposal file and of buehne.json.
- `{"kind": "parse_failed", "turn", "reason", "failed", "raw"}` when the
  reply does not parse: the turn counts as failed and `raw` goes to the
  run trace. No bullet and no proposal is emitted then.

The host passes `failure_markers` (the worker's `FAILURE_MARKERS`) to the
constructor; a reply containing one is a failed parse. The block itself
never imports `gateway`.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from shared.agent import FAILURE_MARKERS
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent

from .parse import MINUTES_SECTIONS, CapturedTurn, ParseFailure, parse_reply

BLOCK_ID = "capture-bullets"

__all__ = ["BLOCK_ID", "CaptureBulletsBlock", "CapturedTurn", "ParseFailure",
           "make_block", "parse_reply"]


def _out(**payload: object) -> CoreEvent:
    return CoreEvent("block_output", {"from": BLOCK_ID, **payload})


class CaptureBulletsBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self, failure_markers: Sequence[str] = ()) -> None:
        self._failure_markers = tuple(failure_markers)

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        pass

    def stop(self) -> None:
        pass

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "agent_reply" or not isinstance(ev.payload, Mapping):
            return []
        turn = ev.payload.get("turn")
        got = parse_reply(ev.payload.get("text", ""),
                          self._failure_markers)
        if isinstance(got, ParseFailure):
            return [_out(kind="parse_failed", turn=turn, reason=got.reason,
                         failed=got.failed, raw=got.raw)]
        out = []
        for section in MINUTES_SECTIONS:
            for item in getattr(got, section):
                out.append(_out(kind="bullet", section=section,
                                text=item["text"], topic_id=item["topic_id"],
                                turn=turn))
        for action in got.buehne:
            out.append(_out(kind="stage_proposal", turn=turn, action=action))
        return out


def make_block() -> CaptureBulletsBlock:
    # The worker's failure markers (shared.agent), injected exactly as the
    # session host's former block list did.
    return CaptureBulletsBlock(failure_markers=FAILURE_MARKERS)
