#!/usr/bin/env python3
# CoThinker - the vault graph as one payload
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Scan, select, cut the edges: one payload, three consumers.

The SAME dict goes into `graph.json`, into one history position of
`buehne.json`, and into the `data-graph` attribute the stage draws from.
One contract, so the picture in the history is the picture that was up.

Edges only between VISIBLE nodes (the `visibleGraph` rule from
an earlier graph surface): an edge to a node the cap left out would be a line
into nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from graph.neighbours import HardEdges, TAG_GROUP_MAX
from graph.ring_select import MAX_NODES, RING_BUDGETS, Hysteresis, select_nodes
from graph.vault_notes import DEFAULT_EXCLUDE, scan_vault


class GraphSource:
    """The vault as a graph. Rescans on demand, keeps the grace list."""

    def __init__(self, root, *, exclude=DEFAULT_EXCLUDE,
                 budgets: tuple[int, ...] = RING_BUDGETS,
                 max_nodes: int = MAX_NODES,
                 tag_group_max: int = TAG_GROUP_MAX) -> None:
        self.root = Path(root)
        self.exclude = tuple(exclude)
        self.budgets = budgets
        self.max_nodes = max_nodes
        self.tag_group_max = tag_group_max
        # The grace list lives ACROSS builds -- that is what makes it
        # hysteresis and not a filter.
        self.hysteresis = Hysteresis()
        self.index = None
        self.rescan()

    def rescan(self):
        self.index = scan_vault(self.root, exclude=self.exclude)
        self.edges = HardEdges(self.index, tag_group_max=self.tag_group_max)
        return self.index

    def build(self, seed_rel: str) -> dict:
        """The payload for one seed. Raises KeyError for an unknown seed."""
        seed = self.index.notes[seed_rel]
        ranked = select_nodes(seed, self.edges, budgets=self.budgets,
                              max_nodes=self.max_nodes)
        sichtbar = self.hysteresis.apply(set(ranked), set(self.index.notes))
        nodes = []
        for rel in sorted(sichtbar):
            note = self.index.notes[rel]
            nodes.append({"id": rel, "label": note.stem,
                          "ring": ranked.get(rel, len(self.budgets)),
                          "seed": rel == seed_rel,
                          "grace": rel not in ranked})
        edges = {}
        for rel in sichtbar:
            for nachbar in self.edges.neighbours(self.index.notes[rel]):
                if nachbar.note.rel not in sichtbar:
                    continue
                a, b = sorted((rel, nachbar.note.rel))
                kennung = f"{nachbar.kind[0]}:{a}|{b}"
                edges.setdefault(kennung, {
                    "id": kennung, "source": a, "target": b,
                    "kind": nachbar.kind, "weight": nachbar.weight})
        bericht = self.index.report()
        bericht["tags_capped"] = sum(
            1 for gruppe in self.index.tag_groups.values()
            if len(gruppe) > self.tag_group_max)
        return {"seed": seed_rel, "nodes": nodes,
                "edges": [edges[k] for k in sorted(edges)],
                "counts": bericht}


def write_graph_json(payload: dict, path) -> None:
    """Write the payload atomically -- a reader never sees half of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
