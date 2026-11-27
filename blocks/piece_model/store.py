# CoThinker — blocks/piece_model/store.py: PieceStore (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`PieceStore`: the one path a piece is ever written through.

Every write snapshots the piece as it was before the change to
`history-<rev>.json`, then bumps `rev`; `undo` restores the previous
snapshot as a NEW revision rather than rewinding `rev`, so the history
list only ever grows (until pruned) and never needs special-casing a
rev that was "undone back to".
"""
from __future__ import annotations

from typing import Callable

from shared.modules import StateStore

PIECE_FILE = "piece.json"
TEXT_RUN_SOURCE = "text_run"


class Busy(RuntimeError):
    """Raised by `PieceStore.update` when a text run holds the busy flag
    and the caller's `source` is itself a text run."""


def empty_piece() -> dict:
    return {
        "rev": 0,
        "title": "",
        "frame": {"place": "", "time": ""},
        "cast": [],
        "scenes": [],
        "busy": False,
    }


def next_id(piece: dict) -> int:
    """The next free scene id: one higher than every id already in the piece."""
    ids = [s["id"] for s in piece.get("scenes", [])]
    return (max(ids) + 1) if ids else 1


def live(piece: dict) -> list[dict]:
    """The piece's scenes. Every scene today; no soft-delete yet."""
    return list(piece.get("scenes", []))


def scene_by_order(piece: dict, order: int) -> dict | None:
    for scene in live(piece):
        if scene.get("order") == order:
            return scene
    return None


class PieceStore:
    def __init__(self, state: StateStore, keep: int = 100) -> None:
        self._state = state
        self._keep = keep

    def read(self) -> dict:
        data = self._state.read(PIECE_FILE)
        return data if data else empty_piece()

    def busy(self) -> bool:
        return bool(self.read().get("busy", False))

    def set_busy(self, on: bool) -> None:
        def mutate(current: dict) -> dict:
            piece = current if current else empty_piece()
            piece["busy"] = bool(on)
            return piece

        self._state.update(PIECE_FILE, mutate)

    def update(self, tool: str, source: str, fn: Callable[[dict], None]) -> dict:
        if source == TEXT_RUN_SOURCE and self.busy():
            raise Busy(f"tool {tool!r}: piece is busy with a text run")

        def mutate(current: dict) -> dict:
            piece = current if current else empty_piece()
            old_rev = piece.get("rev", 0)
            self._state.write(f"history-{old_rev}.json", piece)
            fn(piece)
            piece["rev"] = old_rev + 1
            self._prune(old_rev + 1)
            return piece

        return self._state.update(PIECE_FILE, mutate)

    def undo(self) -> dict:
        """Restores the previous history snapshot as a NEW revision. A
        no-op (returns the current piece unchanged) at rev 0: there is
        nothing before the first revision.

        The snapshot is read inside the store lock (via `update`), so a
        concurrent write cannot slip in between choosing and restoring it.
        `busy` is live state of a running text run, not piece content: it
        is kept as it is, never taken from the snapshot."""
        if self.read().get("rev", 0) == 0:
            return self.read()

        def restore(working: dict) -> None:
            previous = self._state.read(f"history-{working['rev'] - 1}.json")
            busy = working.get("busy", False)
            working.clear()
            working.update(previous)
            working["busy"] = busy

        return self.update("undo", "undo", restore)

    def history_revs(self) -> list[int]:
        out = []
        for p in self._state.root.glob("history-*.json"):
            try:
                out.append(int(p.stem.split("-", 1)[1]))
            except ValueError:
                continue
        return sorted(out)

    def _prune(self, rev_after: int) -> None:
        if self._keep <= 0:
            return
        threshold = rev_after - self._keep
        if threshold <= 0:
            return
        for p in self._state.root.glob("history-*.json"):
            try:
                idx = int(p.stem.split("-", 1)[1])
            except ValueError:
                continue
            if idx < threshold:
                p.unlink(missing_ok=True)
