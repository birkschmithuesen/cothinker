# CoThinker — blocks/timer/clock.py: planned minutes, drift, projected end
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure timer math, ported from the archive hub's cue clock (`workshop/hub.py`
`_planned_cumulative_ms`, `drift_s`, `projected_end_ms`) with two changes: an
injected clock (no wall-clock reads here, callers pass `now`/`anchor` in as
plain floats) and seconds instead of milliseconds.
"""
from __future__ import annotations

from typing import Optional, Sequence


def planned_cumulative(minutes: Sequence[object]) -> tuple[float, ...]:
    """Cumulative planned minutes per item; a non-numeric/missing entry counts 0."""
    cumulative = []
    total = 0.0
    for value in minutes:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += value
        cumulative.append(total)
    return tuple(cumulative)


def drift_s(*, anchor: Optional[float], actual_start: Optional[float], index: int,
            cumulative_min: Sequence[float], now: float) -> Optional[float]:
    """Seconds the current item has slipped against `cumulative_min`'s plan.

    `index` is clamped to the item range. The current item's actual start
    falls back to `anchor` when it has no recorded start. Drift is the sum
    of how late the item started against its planned start, and how far
    `now` has overrun the item's planned duration since it actually
    started — both floored at 0, since starting or running early is not
    credited against a later item's lateness.
    """
    if anchor is None:
        return None
    if not cumulative_min:
        return 0.0
    clamped = max(0, min(index, len(cumulative_min) - 1))
    planned_start_min = cumulative_min[clamped - 1] if clamped > 0 else 0.0
    planned_duration_min = cumulative_min[clamped] - planned_start_min
    planned_start = anchor + planned_start_min * 60.0
    start = actual_start if actual_start is not None else anchor
    late_start = max(0.0, start - planned_start)
    overrun = max(0.0, (now - start) - planned_duration_min * 60.0)
    return late_start + overrun


def projected_end(*, anchor: Optional[float], cumulative_min: Sequence[float],
                   drift: Optional[float]) -> Optional[float]:
    """The plan's end time, pushed out by `drift`."""
    if anchor is None or drift is None:
        return None
    total_min = cumulative_min[-1] if cumulative_min else 0.0
    return anchor + total_min * 60.0 + drift
