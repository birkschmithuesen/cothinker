# CoThinker — blocks/piece_model/views.py: read-only views of the piece (issue #69)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Every view here derives from the piece; none of them store anything of
their own. `scene_card_view` in particular never duplicates place or cast
onto a scene: a scene only ever holds `cast_ids`, resolved here against
the piece's single cast list and single `frame.place`."""
from __future__ import annotations

from typing import Sequence

from .gaps import gap_scene_orders
from .store import live, scene_by_order


def _scene_summary(scene: dict, has_gap: bool) -> dict:
    return {
        "order": scene["order"],
        "phase": scene.get("phase", ""),
        "title": scene.get("title", ""),
        "has_gap": has_gap,
    }


def piece_summary(piece: dict, scene: int | None = None) -> dict:
    gapped = gap_scene_orders(piece)
    scenes = live(piece)
    if scene is not None:
        scenes = [s for s in scenes if s.get("order") == scene]
    return {
        "rev": piece.get("rev", 0),
        "title": piece.get("title", ""),
        "frame": dict(piece.get("frame", {})),
        "cast": [{"id": c["id"], "name": c.get("name", "")} for c in piece.get("cast", [])],
        "scenes": [_scene_summary(s, s["order"] in gapped) for s in scenes],
    }


def piece_full(piece: dict, scene: int | None = None) -> dict:
    if scene is not None:
        return {"rev": piece.get("rev", 0), "scene": scene_by_order(piece, scene)}
    return {"rev": piece.get("rev", 0), "piece": piece}


def scene_card_view(piece: dict, order: int) -> dict | None:
    scene = scene_by_order(piece, order)
    if scene is None:
        return None
    cast_by_id = {c["id"]: c for c in piece.get("cast", [])}
    cast = [cast_by_id[i] for i in scene.get("cast_ids", []) if i in cast_by_id]
    return {
        "order": scene["order"],
        "phase": scene.get("phase", ""),
        "title": scene.get("title", ""),
        "place": piece.get("frame", {}).get("place", ""),
        "cast": cast,
    }


def status_view(piece: dict, languages: Sequence[str]) -> dict:
    return {
        "rev": piece.get("rev", 0),
        "busy": bool(piece.get("busy", False)),
        "scene_count": len(live(piece)),
        "capabilities": {
            "languages": list(languages),
            "agent_can_write_script": True,
            "text_versions_per_language": True,
            "can_search_material": True,
        },
    }
