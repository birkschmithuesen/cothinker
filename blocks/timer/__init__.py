# CoThinker — blocks/timer: planned items, current item, drift, projected end
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `timer` block (ADR 0010, ADR 0008): planned items with minutes, the
current item, and (via `clock.py`) drift against the plan and the projected
end. Config: `{"items": [{"label", "minutes"}], "deadline": epoch seconds | null}`.

Consumes `action` with `intent_id` `timer:current` (`payload` `{"op":
"next" | "back" | "goto", "index": 0-based int}` for `"goto"`, any kind,
voice and tap included) and `block_output` from `speaker-queue` carrying
`round_wrapped` (moves to the next item), and `tick`; with config
`follow_phases: true` (default `false`), also consumes `phase` CoreEvents
(payload `{"to", "at", ...}`, see `shared/phases.py`): when an item's
`"phase"` key matches the event's `to`, that item becomes current and its
start is set to the event's `at` (falling back to `ctx.clock()`); a `to`
matching no item's `"phase"` is a no-op. With `follow_phases` unset or
false, `phase` events are ignored exactly as before. Emits the view intent
`timer:current` (kind `timer`) once on `start`
(when there are items, so the stage shows the agenda before the first move,
and the fresh position is persisted so the anchor survives a restart) and
then only when the current item changes. `next` always restarts the new current item's clock, also on the last
item, where it stays. `current`/`next` are 1-based item numbers (`next` is None on the last
item); `item_budget_s` is None for an item without numeric minutes;
`meeting_deadline` is passed through from the config (epoch seconds).
The position (index, anchor, item starts) persists in block state
(`timer.json`) and is restored on `start`.
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

__all__ = ["TimerBlock", "make_block"]

STATE_FILE = "timer.json"


def _label(item: Mapping[str, object]) -> Optional[str]:
    label = item.get("label")
    return str(label) if label is not None else None


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _minutes(item: Mapping[str, object]) -> object:
    return item.get("minutes")


class TimerBlock:
    id = "timer"
    api_version = BLOCK_API_VERSION

    def __init__(self) -> None:
        self._ctx: Optional[BlockContext] = None
        self._items: list[Mapping[str, object]] = []
        self._deadline: Optional[float] = None
        self._cumulative_min: tuple[float, ...] = ()
        self._follow_phases = False
        self._index = 0
        self._anchor: Optional[float] = None
        self._item_started_at: dict[int, float] = {}

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._items = list(config.get("items", []))
        self._deadline = config.get("deadline")
        self._cumulative_min = planned_cumulative([_minutes(i) for i in self._items])
        self._follow_phases = bool(config.get("follow_phases", False))
        self._index = 0
        self._anchor = ctx.clock()
        self._item_started_at = {0: self._anchor}
        try:
            restored = self._restore(ctx.state.read(STATE_FILE))
        except (OSError, ValueError, TypeError, AttributeError):
            restored = False        # unreadable/corrupt state: start fresh
        if self._items:
            if not restored:
                self._save()
            self._emit_current()

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "phase":
            if self._follow_phases and self._follow_phase(ev.payload):
                self._save()
                self._emit_current()
            return ()
        op = None
        index = None
        if ev.kind == "action" and ev.payload.get("intent_id") == "timer:current":
            action_payload = ev.payload.get("payload") or {}
            op = action_payload.get("op")
            index = action_payload.get("index")
        elif (ev.kind == "block_output" and ev.payload.get("from") == "speaker-queue"
              and "round_wrapped" in ev.payload):
            op = "next"
        if op is not None and self._move(op, index):
            self._save()
            self._emit_current()
        return ()

    def stop(self) -> None:
        pass

    def _restore(self, data: Mapping[str, object]) -> bool:
        index = data.get("index")
        anchor = data.get("anchor")
        started = data.get("started")
        if not (isinstance(index, int) and isinstance(anchor, (int, float))
                and isinstance(started, dict) and 0 <= index < len(self._items)):
            return False
        parsed = {int(k): float(v) for k, v in started.items()}
        self._index = index
        self._anchor = float(anchor)
        self._item_started_at = parsed
        return True

    def _save(self) -> None:
        self._ctx.state.write(STATE_FILE, {
            "index": self._index, "anchor": self._anchor,
            "started": {str(k): v for k, v in self._item_started_at.items()},
        })

    def _move(self, op: object, index: object = None) -> bool:
        if not self._items:
            return False
        last = len(self._items) - 1
        if op == "next":
            if self._index < last:
                self._index += 1
            self._item_started_at[self._index] = self._ctx.clock()
            return True
        if op == "back":
            if self._index > 0:
                self._index -= 1
            else:
                return False
        elif op == "goto":
            if (isinstance(index, int) and not isinstance(index, bool)
                    and 0 <= index <= last and index != self._index):
                self._index = index
            else:
                return False
        else:
            return False
        if self._index not in self._item_started_at:
            self._item_started_at[self._index] = self._ctx.clock()
        return True

    def _follow_phase(self, payload: Mapping[str, object]) -> bool:
        """Move to the item whose `"phase"` matches `payload["to"]`.

        No match (a phase outside the cue list) or a match on the current
        item is a no-op: no index change, no emit.
        """
        index = self._phase_index(payload.get("to"))
        if index is None or index == self._index:
            return False
        self._index = index
        at = payload.get("at")
        self._item_started_at[index] = float(at) if _is_number(at) else self._ctx.clock()
        return True

    def _phase_index(self, phase_id: object) -> Optional[int]:
        if phase_id is None:
            return None
        for i, item in enumerate(self._items):
            if item.get("phase") == phase_id:
                return i
        return None

    def _budget_s(self, index: int) -> Optional[float]:
        if not _is_number(_minutes(self._items[index])):
            return None
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
                "current": current + 1,
                "current_text": _label(self._items[current]),
                "next": next_index + 1 if next_index is not None else None,
                "next_text": _label(self._items[next_index]) if next_index is not None else None,
                "total": total,
                "item_started_at": self._item_started_at.get(current, self._anchor),
                "item_budget_s": self._budget_s(current),
                "meeting_deadline": self._deadline,
                "anchor_at": self._anchor,
                "planned_cumulative_min": self._cumulative_min,
            },
        ))


def make_block() -> TimerBlock:
    return TimerBlock()
