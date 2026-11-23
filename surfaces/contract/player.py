"""Fixture-mode replay (ADR 0019 point 2): plays a scenario's frames over
the real endpoints, in time-lapse, never through a separate simulation.
"""
from __future__ import annotations

import asyncio

from surfaces.contract.fixtures import Frame
from surfaces.contract.models import StageStateSnapshot


class ScenarioPlayer:
    """Holds the latest snapshot and fans out view intents to SSE readers."""

    def __init__(self, frames: list[Frame], initial_snapshot: StageStateSnapshot):
        self._frames = frames
        self.snapshot = initial_snapshot
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def _publish(self, intent) -> None:
        for queue in list(self._subscribers):
            await queue.put(intent)

    async def run(self) -> None:
        """Replay every frame once, in order, respecting its `delay_s`."""
        for frame in self._frames:
            await asyncio.sleep(frame.delay_s)
            self.snapshot = frame.snapshot
            for intent in frame.view_intents:
                await self._publish(intent)
