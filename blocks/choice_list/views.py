# CoThinker — blocks/choice_list/views.py: row/state/counter view
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Turns a stored choice list into the `choice_list` intent payload (issue
#86): headings -> rows -> counter, analogous to the archive's `auswahl.py`
view functions (`auswahl`/`auswahl_fertig`, read-only reference, not
imported). Pure and deterministic so the golden test can replay it."""
from __future__ import annotations

from typing import Mapping, Sequence

STATES = ("open", "accepted", "rejected", "edited")


def build_groups(groups: Sequence[Mapping], items: Mapping[str, Mapping]) -> list[dict]:
    """One row per item id, in the stored group order. An edited item shows
    its edited text, not the original one."""
    out = []
    for group in groups:
        rows = []
        for item_id in group["items"]:
            item = items[item_id]
            rows.append({
                "id": item_id,
                "text": item["edited_text"] if item["state"] == "edited" else item["text"],
                "state": item["state"],
            })
        out.append({"heading": group["heading"], "rows": rows})
    return out


def counter(items: Mapping[str, Mapping]) -> dict:
    counts = {state: 0 for state in STATES}
    for item in items.values():
        counts[item["state"]] += 1
    counts["total"] = len(items)
    return counts


def build_view(list_id: str, data: Mapping) -> dict:
    """The `choice_list` intent payload for one list's current state."""
    return {
        "list_id": list_id,
        "title": data["title"],
        "groups": build_groups(data["groups"], data["items"]),
        "counter": counter(data["items"]),
        "allow_edit": data["allow_edit"],
        "done_label": data["done_label"],
    }
