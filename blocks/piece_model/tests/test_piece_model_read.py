# CoThinker — tests for blocks/piece_model read tools and views (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_piece_model_read.py"""
from __future__ import annotations

from blocks.piece_model.gaps import gap_list
from blocks.piece_model.store import PieceStore
from blocks.piece_model.tools_read import piece_status, read_piece, scene_card
from shared.modules import StateStore


def make_store(tmp_path):
    return PieceStore(StateStore(tmp_path / "piece"))


def fill_piece(piece):
    piece["title"] = "The Piece"
    piece["frame"] = {"place": "Theater X", "time": "evening"}
    piece["cast"] = [
        {"id": 1, "name": "Alice", "role": "lead"},
        {"id": 2, "name": "Bob", "role": "support"},
    ]
    piece["scenes"] = [
        {"id": 1, "order": 1, "phase": "setup", "title": "Opening", "cast_ids": [1],
         "text": {"de": "Hallo"}},
        {"id": 2, "order": 2, "phase": "climax", "title": "", "cast_ids": [], "text": {}},
    ]


def test_read_piece_summary_and_full(tmp_path):
    store = make_store(tmp_path)
    store.update("fill", "agent", fill_piece)

    summary = read_piece(store, view="summary")
    assert summary["ok"] is True
    assert summary["data"]["title"] == "The Piece"
    assert summary["data"]["rev"] == 1
    assert len(summary["data"]["scenes"]) == 2
    assert "text" not in summary["data"]["scenes"][0]
    assert summary["data"]["scenes"][1]["has_gap"] is True

    full = read_piece(store, view="full")
    assert full["ok"] is True
    assert full["data"]["piece"]["scenes"][0]["text"] == {"de": "Hallo"}

    one_scene = read_piece(store, view="full", scene=1)
    assert one_scene["data"]["scene"]["id"] == 1
    assert one_scene["data"]["scene"]["text"] == {"de": "Hallo"}


def test_piece_status_has_capabilities_from_instance_languages(tmp_path):
    store = make_store(tmp_path)
    store.update("fill", "agent", fill_piece)

    result = piece_status(store, languages=("de", "en"))
    assert result["ok"] is True
    data = result["data"]
    assert data["rev"] == 1
    assert data["busy"] is False
    assert data["capabilities"] == {
        "languages": ["de", "en"],
        "agent_can_write_script": True,
        "text_versions_per_language": True,
        "can_search_material": True,
    }


def test_scene_card_takes_place_and_cast_from_single_sources(tmp_path):
    store = make_store(tmp_path)
    store.update("fill", "agent", fill_piece)

    result = scene_card(store, scene=1)
    assert result["ok"] is True
    card = result["data"]
    assert card["place"] == "Theater X"
    assert card["cast"] == [{"id": 1, "name": "Alice", "role": "lead"}]

    missing = scene_card(store, scene=99)
    assert missing["ok"] is False
    assert "unknown_scene" in missing["error"]


def test_gap_list_capped_at_8_sorted_by_phase(tmp_path):
    store = make_store(tmp_path)

    def fill_many_gaps(piece):
        piece["scenes"] = [
            {"id": i, "order": i, "phase": phase, "title": "", "cast_ids": [], "text": {}}
            for i, phase in enumerate(
                ["resolution", "setup", "climax", "development", "setup", "resolution",
                 "climax", "development", "setup", "climax"],
                start=1,
            )
        ]

    store.update("fill", "agent", fill_many_gaps)
    piece = store.read()

    gaps = gap_list(piece)
    assert len(gaps) == 8
    phase_order = ["setup", "development", "climax", "resolution"]
    ranks = [phase_order.index(g["phase"]) for g in gaps]
    assert ranks == sorted(ranks)
