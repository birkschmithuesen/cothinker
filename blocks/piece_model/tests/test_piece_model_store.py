# CoThinker — tests for blocks/piece_model/store.py: PieceStore (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_piece_model_store.py"""
from __future__ import annotations

import json
import time

import pytest

from blocks.piece_model.store import Busy, PieceStore, empty_piece
from shared.modules import StateStore


def make_store(tmp_path, keep=100):
    return PieceStore(StateStore(tmp_path / "piece"), keep=keep)


def test_empty_piece_shape(tmp_path):
    store = make_store(tmp_path)
    piece = store.read()
    assert piece == empty_piece()
    assert piece == {
        "rev": 0, "title": "", "frame": {"place": "", "time": ""},
        "cast": [], "scenes": [], "busy": False,
    }


def test_update_bumps_rev_and_snapshots_history(tmp_path):
    store = make_store(tmp_path)

    def set_title(piece):
        piece["title"] = "Alpha"

    updated = store.update("set_title", "agent", set_title)
    assert updated["rev"] == 1
    assert updated["title"] == "Alpha"
    assert store.read()["rev"] == 1
    assert store.history_revs() == [0]
    snapshot = json.loads((tmp_path / "piece" / "history-0.json").read_text(encoding="utf-8"))
    assert snapshot == empty_piece()


def test_undo_restores_previous_as_new_rev(tmp_path):
    store = make_store(tmp_path)
    store.update("set_title", "agent", lambda p: p.update(title="Alpha"))
    store.update("set_title", "agent", lambda p: p.update(title="Beta"))
    restored = store.undo()
    assert restored["rev"] == 3
    assert restored["title"] == "Alpha"
    assert store.history_revs() == [0, 1, 2]
    # The busy flag is live state of a running text run, not piece content:
    # undo must neither clear it nor bring back an old value from a snapshot.
    store.set_busy(True)
    assert store.undo()["busy"] is True
    assert store.busy() is True


def test_history_is_pruned_beyond_keep(tmp_path):
    store = make_store(tmp_path, keep=3)
    for i in range(10):
        store.update("set_title", "agent", lambda p, i=i: p.update(title=f"t{i}"))
    assert store.history_revs() == [7, 8, 9]
    assert store.read()["rev"] == 10


def test_max_piece_under_1_mib_and_100_ms(tmp_path):
    store = make_store(tmp_path)

    def fill_maximum(piece):
        piece["title"] = "A Synthetic Maximum Piece"
        piece["frame"] = {"place": "Main Stage" * 5, "time": "An evening in autumn"}
        piece["cast"] = [
            {"id": i, "name": f"Character {i}", "role": "role " * 10}
            for i in range(1, 31)
        ]
        piece["scenes"] = [
            {
                "id": i, "order": i, "phase": "development", "title": f"Scene {i}",
                "cast_ids": [1, 2, 3],
                "text": {
                    "de": ("Ein langer Probetext fuer Szene. " * 100),
                    "en": ("A long rehearsal text for the scene. " * 100),
                },
            }
            for i in range(1, 41)
        ]

    store.update("fill", "agent", fill_maximum)

    def touch(piece):
        piece["title"] = "A Synthetic Maximum Piece, touched"

    started = time.monotonic()
    store.update("touch", "agent", touch)
    elapsed = time.monotonic() - started

    size = len(json.dumps(store.read()).encode("utf-8"))
    assert size < 1024 * 1024
    assert elapsed < 0.1


def test_busy_flag_blocks_text_writes_only(tmp_path):
    store = make_store(tmp_path)
    store.set_busy(True)

    with pytest.raises(Busy):
        store.update("write_text", "text_run", lambda p: p.update(title="from text run"))

    updated = store.update("set_title", "agent", lambda p: p.update(title="from agent"))
    assert updated["title"] == "from agent"

    store.set_busy(False)
    updated = store.update("write_text", "text_run", lambda p: p.update(title="from text run now"))
    assert updated["title"] == "from text run now"
