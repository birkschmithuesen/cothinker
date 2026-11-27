# CoThinker — blocks/piece_model/gaps.py: the gap list (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The gap list is derived from the piece, not kept as a second source of
truth: a scene is a gap when it is missing a title, cast or text, nothing
more. Capped at 8 so the agent gets a short, actionable list rather than
an inventory of everything left to do."""
from __future__ import annotations

from .store import live

PHASE_ORDER = ("setup", "development", "climax", "resolution")
MAX_GAPS = 8


def _phase_index(phase: str) -> int:
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return len(PHASE_ORDER)


def _scene_gap_kinds(scene: dict) -> list[str]:
    kinds = []
    if not scene.get("title"):
        kinds.append("missing_title")
    if not scene.get("cast_ids"):
        kinds.append("missing_cast")
    if not scene.get("text"):
        kinds.append("missing_text")
    return kinds


def gap_scene_orders(piece: dict) -> set[int]:
    return {scene["order"] for scene in live(piece) if _scene_gap_kinds(scene)}


def gap_list(piece: dict) -> list[dict]:
    gaps = []
    for scene in live(piece):
        for kind in _scene_gap_kinds(scene):
            gaps.append({"scene": scene["order"], "phase": scene.get("phase", ""), "kind": kind})
    gaps.sort(key=lambda g: (_phase_index(g["phase"]), g["scene"]))
    return gaps[:MAX_GAPS]
