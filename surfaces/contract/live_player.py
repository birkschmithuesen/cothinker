"""Live mode (ADR 0019 point 2, issue #141): the player-shaped counterpart
to `surfaces.contract.player.ScenarioPlayer`, fed by a running meeting's
`buehne.json` instead of a replayed fixture scenario.

Read-only: `gateway.consumer.StageWriter` stays the single writer of
`buehne.json`; this class only polls the file and maps its content onto
`StageStateSnapshot`, it never writes it.

Mapping choice (documented in `docs/surface-contract.md`): a change to a
field with an obvious owning view-intent kind (`queue`, `voice`, `share`,
`session_graph`) is emitted as that kind's intent; every other changed
field is folded into one `StatusIntent` naming the changed fields, since
most of the snapshot (headline, history, decisions, ...) has no intent
kind of its own.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from surfaces.contract.models import (
    GraphIntent,
    QueueIntent,
    ShareIntent,
    StageStateSnapshot,
    StatusIntent,
    TasksIntent,
    TranscriptRecentIntent,
    VoiceIntent,
)

POLL_INTERVAL_S = 0.3

_OWNED_INTENT = {
    "queue": (QueueIntent, "queue:room"),
    "voice": (VoiceIntent, "voice:room"),
    "share": (ShareIntent, "share:room"),
    "session_graph": (GraphIntent, "graph:session"),
}

# Fields holding a bare list rather than a single model (issue #147 point 3:
# tasks/transcript_recent are now owned fields too, like queue/share/
# session_graph), mapped to the list kwarg their intent class expects.
_OWNED_LIST_INTENT = {
    "tasks": (TasksIntent, "tasks:live", "tasks"),
    "transcript_recent": (TranscriptRecentIntent, "transcript:live", "lines"),
}


class LivePlayer:
    """Holds the latest mapped snapshot and fans out view intents to SSE
    readers, built from a session dir's `buehne.json` instead of a
    replayed fixture."""

    def __init__(self, session_dir: Path, *, poll_interval_s: float = POLL_INTERVAL_S):
        self._path = Path(session_dir) / "buehne.json"
        self._poll_interval_s = poll_interval_s
        self.snapshot = self._read()
        self._subscribers: set[asyncio.Queue] = set()
        self._status_seq = 0

    def _read(self) -> StageStateSnapshot:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return StageStateSnapshot.model_validate(data)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def _publish(self, intent) -> None:
        for queue in list(self._subscribers):
            await queue.put(intent)

    def _diff(self, before: StageStateSnapshot, after: StageStateSnapshot) -> list:
        """One intent per changed field with an owning kind, plus one
        `StatusIntent` for every other changed field, in that order."""
        intents = []
        other_changed = []
        for name in type(after).model_fields:
            new = getattr(after, name)
            if new == getattr(before, name):
                continue
            owned = _OWNED_INTENT.get(name)
            if owned is not None and new is not None:
                intent_cls, intent_id = owned
                intents.append(intent_cls(id=intent_id, **new.model_dump()))
                continue
            owned_list = _OWNED_LIST_INTENT.get(name)
            if owned_list is not None:
                intent_cls, intent_id, kwarg = owned_list
                intents.append(intent_cls(id=intent_id, **{kwarg: new}))
                continue
            other_changed.append(name)
        if other_changed:
            self._status_seq += 1
            intents.append(StatusIntent(
                id=f"status:live:{self._status_seq}",
                text="stage state changed: " + ", ".join(sorted(other_changed)),
            ))
        return intents

    async def run(self) -> None:
        """Poll `buehne.json` for content changes; never write it."""
        while True:
            await asyncio.sleep(self._poll_interval_s)
            try:
                new_snapshot = self._read()
            except (OSError, ValueError):
                continue
            if new_snapshot == self.snapshot:
                continue
            intents = self._diff(self.snapshot, new_snapshot)
            self.snapshot = new_snapshot
            for intent in intents:
                await self._publish(intent)
