# CoThinker — blocks/piece_model/tools_read.py: read tools for the agent (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`read_piece`, `piece_status`, `scene_card`: the only tools through which
the agent may state facts about the saved piece (the grounding rule points
at these, not at its own memory of the conversation)."""
from __future__ import annotations

from typing import Sequence

from shared.modules import ToolSpec

from .store import PieceStore
from .views import piece_full, piece_summary, scene_card_view, status_view


def _ok(data: dict, rev: int) -> dict:
    return {"ok": True, "data": data, "warnings": [], "rev": rev, "error": None}


def read_piece(store: PieceStore, view: str = "summary", scene: int | None = None) -> dict:
    piece = store.read()
    data = piece_full(piece, scene) if view == "full" else piece_summary(piece, scene)
    return _ok(data, piece.get("rev", 0))


def piece_status(store: PieceStore, languages: Sequence[str]) -> dict:
    piece = store.read()
    return _ok(status_view(piece, languages), piece.get("rev", 0))


def scene_card(store: PieceStore, scene: int) -> dict:
    piece = store.read()
    card = scene_card_view(piece, scene)
    if card is None:
        return {"ok": False, "data": {}, "warnings": [], "rev": piece.get("rev", 0),
                 "error": f"unknown_scene: {scene}"}
    return _ok(card, piece.get("rev", 0))


def make_tools(store: PieceStore, languages: Sequence[str]) -> list[ToolSpec]:
    return [
        ToolSpec(
            "read_piece", "The piece: a summary view, or the full text.",
            {"type": "object", "properties": {
                "view": {"type": "string", "enum": ["summary", "full"]},
                "scene": {"type": "integer"}}},
            lambda view="summary", scene=None: read_piece(store, view, scene),
            read_only=True),
        ToolSpec(
            "piece_status", "Revision, busy flag and the agent's fixed capabilities.",
            {"type": "object", "properties": {}},
            lambda: piece_status(store, languages),
            read_only=True),
        ToolSpec(
            "scene_card", "One scene's place and cast, from the piece's single sources.",
            {"type": "object", "properties": {"scene": {"type": "integer"}},
             "required": ["scene"]},
            lambda scene: scene_card(store, scene),
            read_only=True),
    ]
