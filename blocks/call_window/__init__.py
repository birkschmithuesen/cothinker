# CoThinker — blocks/call_window: the call window's stage-facing surface
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `call-window` (issue #180, ADR 0024): the stage-facing
side of the call window the wake word opens. The window's state and its
timers live in `gateway.segmenter.BlockBuilder` (pure, unit-tested there);
this block owns none of that -- it only turns a tap into a call to the
real segmenter and the segmenter's state into the view intent.

Consumes `action` events with `intent_id == "listen:room"` (`op` one of
`open`, `close`, `toggle`) and `block_output` events shaped
`{"call_window": state}` (the host dispatches the segmenter's `state()`
after every transition, voice-triggered or not). Emits the view intent
`listen:room` (kind `listen`, audience `room`), payload exactly the
segmenter's `state()` shape (ADR 0024's `ListenSnapshot`): `state`,
`since`, `opened_by`, `closes_in_s`, `last_close`.

Interim host handle (same pattern as `blocks/knowledge_link`'s
`use_project`): until a template-level seam exists for a block to reach
the segmenter, the session host (`gateway/consumer.py`,
`gateway.block_host_wiring.wire_call_window`) calls `bind_host` directly
with the real `BlockBuilder.open_call`/`close_call`/`state`, and nothing
else does.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

__all__ = ["CallWindowBlock", "BLOCK_ID", "INTENT_ID", "LISTEN_OPS", "make_block"]

BLOCK_ID = "call-window"
INTENT_ID = "listen:room"
LISTEN_OPS = ("open", "close", "toggle")


class CallWindowBlock:
    id = BLOCK_ID
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._open_call: Optional[Callable[[str], None]] = None
        self._close_call: Optional[Callable[[str], None]] = None
        self._state_fn: Optional[Callable[[], Mapping[str, object]]] = None
        self._last_payload: Optional[dict] = None

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._last_payload = None

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "action" and ev.payload.get("intent_id") == INTENT_ID:
            self._on_action(ev.payload)
        elif ev.kind == "block_output" and "call_window" in ev.payload:
            self._emit_if_changed(ev.payload["call_window"])
        return ()

    def stop(self) -> None:
        pass

    # -- interim host handle -----------------------------------------
    def bind_host(self, *, open_call: Callable[[str], None],
                 close_call: Callable[[str], None],
                 state: Callable[[], Mapping[str, object]]) -> None:
        self._open_call = open_call
        self._close_call = close_call
        self._state_fn = state

    @property
    def bound(self) -> bool:
        return self._open_call is not None

    # -- internal -------------------------------------------------------
    def _on_action(self, payload: Mapping[str, object]) -> None:
        inner = payload.get("payload") or {}
        op = inner.get("op")
        if op not in LISTEN_OPS or self._open_call is None or self._close_call is None:
            return
        listening = self._is_listening()
        if op == "open" or (op == "toggle" and not listening):
            self._open_call("tap")
        elif op == "close" or (op == "toggle" and listening):
            self._close_call("tap")
        if self._state_fn is not None:
            self._emit_if_changed(self._state_fn())

    def _is_listening(self) -> bool:
        if self._state_fn is None:
            return False
        return self._state_fn().get("state") == "listening"

    def _emit_if_changed(self, state: Optional[Mapping[str, object]]) -> None:
        if state is None:
            return
        payload = dict(state)
        if payload == self._last_payload:
            return
        self._last_payload = payload
        self._ctx.emit(ViewIntent(
            id=INTENT_ID, block=self.id, kind="listen", audience="room",
            payload=payload))


def make_block() -> CallWindowBlock:
    return CallWindowBlock()
