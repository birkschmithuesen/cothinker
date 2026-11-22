# CoThinker — blocks/timer: planned items, current item, drift, projected end
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `timer` block (ADR 0010, ADR 0008): planned items with minutes, the
current item, and (via `clock.py`) drift against the plan and the projected
end. Config: `{"items": [{"label", "minutes"}], "deadline": "HH:MM" | null}`.

Consumes `action` (`op` `"next"` | `"back"`) and `tick`; emits the view
intent `timer:current` (kind `timer`) only when the current item changes.
The payload carries only absolute times (`item_started_at`, `anchor_at`,
`meeting_deadline`) and plan data (`item_budget_s`, `planned_cumulative_min`),
never a ticking value — the stage turns those into a countdown itself, so
nothing here depends on how long ago `now` was (ADR 0008 §9 fingerprint).
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent
from shared.surface import ViewIntent

from .clock import planned_cumulative

__all__ = ["TimerBlock"]


def _label(item: Mapping[str, object]) -> Optional[str]:
    label = item.get("label")
    return str(label) if label is not None else None


def _minutes(item: Mapping[str, object]) -> object:
    return item.get("minutes")


class TimerBlock:
    id = "timer"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._items: list[Mapping[str, object]] = []
        self._deadline: Optional[str] = None
        self._cumulative_min: tuple[float, ...] = ()
        self._index = 0
        self._anchor: Optional[float] = None
        self._item_started_at: dict[int, float] = {}

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._items = list(config.get("items", []))
        self._deadline = config.get("deadline")
        self._cumulative_min = planned_cumulative([_minutes(i) for i in self._items])
        self._index = 0
        self._anchor = ctx.clock()
        self._item_started_at = {0: self._anchor}

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "action" and self._move(ev.payload.get("op")):
            self._emit_current()
        return ()

    def stop(self) -> None:
        pass

    def _move(self, op: object) -> bool:
        if not self._items:
            return False
        last = len(self._items) - 1
        if op == "next" and self._index < last:
            self._index += 1
        elif op == "back" and self._index > 0:
            self._index -= 1
        else:
            return False
        if self._index not in self._item_started_at:
            self._item_started_at[self._index] = self._ctx.clock()
        return True

    def _budget_s(self, index: int) -> float:
        start_min = self._cumulative_min[index - 1] if index > 0 else 0.0
        return (self._cumulative_min[index] - start_min) * 60.0

    def _emit_current(self) -> None:
        total = len(self._items)
        current = self._index
        next_index = current + 1 if current + 1 < total else None
        self._ctx.emit(ViewIntent(
            id=f"{self.id}:current",
            block=self.id,
            kind="timer",
            audience="room",
            payload={
                "current": current,
                "current_text": _label(self._items[current]),
                "next": next_index,
                "next_text": _label(self._items[next_index]) if next_index is not None else None,
                "total": total,
                "item_started_at": self._item_started_at.get(current, self._anchor),
                "item_budget_s": self._budget_s(current),
                "meeting_deadline": self._deadline,
                "anchor_at": self._anchor,
                "planned_cumulative_min": self._cumulative_min,
            },
        ))
