"""Fixture scenarios for the surface contract (ADR 0019 point 2).

Every scenario is realistic, invented data -- no real persons -- that a
fixture-mode server replays over the real endpoints, never a separate
simulation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from surfaces.contract.models import StageStateSnapshot


@dataclass
class Frame:
    """One step of a scenario's time-lapse replay.

    `delay_s` is the real seconds to wait after the PREVIOUS frame before
    this one is applied; `snapshot` is the full stage-state snapshot after
    this frame, and `view_intents` are the view intents pushed over the
    SSE channel at this frame.
    """

    delay_s: float
    snapshot: StageStateSnapshot
    view_intents: list = field(default_factory=list)


def load_scenario(name: str) -> list[Frame]:
    """The frames of fixture scenario `name`, built anew (times anchored to
    now) each call; raises for an unknown scenario."""
    if name == "meeting":
        from surfaces.contract.fixtures import meeting

        return meeting.build_frames()
    raise ValueError(f"unknown fixture scenario: {name!r}")
