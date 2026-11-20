#!/usr/bin/env python3
# CoThinker - where an edge comes from
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The edge source, and nothing else knows where an edge comes from.

THE SEAM (docs/entwurf-graph-schirm.md section 6): everything downstream
uses ONE method, `neighbours(note)`. Embeddings and, later, Hindsight's
`recall` implement the same method and the surface never learns about it.
This stage has two hard kinds and makes no network call.

THE CAP IS THE POINT, not the weight. A shared tag is a CLIQUE: with 180
notes, one single tag alone is 180*179/2 = 16110 edges. Measured on the
real vault (M4):

    no cap  : 38436 tag edges (519/519 tags) -- against 1234 wikilink edges
    cap 25  :  2931           (508/519)
    cap 12  :  1344           (492/519)   <-- about 1:1, 95 % of tags kept
    cap  8  :   781           (475/519)

So a smaller weight cannot make tags "weaker than links" (the card,
point B): there are simply 31 times as many of them. The cap can, and it
costs 5 % of the tags. Edge density falls from 23-26 per node to 3.8.
"""
from __future__ import annotations

from dataclasses import dataclass

from graph.vault_notes import Note, NoteIndex

# Derivation above. Withdrawal signal: the picture shows barely any tag
# edges any more (raise) or turns into a mesh again (lower).
TAG_GROUP_MAX = 12

# A link is a relation somebody drew; a tag is a category somebody
# assigned. Both are hard -- a human wrote them -- but they do not mean
# the same thing, so they rank differently INSIDE a ring. The count, not
# this number, is what keeps tags from swamping the picture.
WEIGHTS = {"wikilink": 1.0, "tag": 0.4}


@dataclass(frozen=True)
class Neighbour:
    """The entwurf's `(notiz, art, gewicht)`."""
    note: Note
    kind: str
    weight: float


class HardEdges:
    """`neighbours(note)` over wikilinks and shared tags. No I/O, no model."""

    def __init__(self, index: NoteIndex, *,
                 tag_group_max: int = TAG_GROUP_MAX) -> None:
        self.index = index
        self.tag_group_max = tag_group_max
        # Undirected: a link from A to B makes B a neighbour of A AND A a
        # neighbour of B. Somebody drew the relation; which end they wrote
        # it on is an accident of how the note was written.
        self._backlinks: dict[str, set[str]] = {}
        for note in index.notes.values():
            for ziel in note.links:
                treffer = index.resolve(ziel)
                if treffer is not None and treffer.rel != note.rel:
                    self._backlinks.setdefault(treffer.rel, set()).add(note.rel)

    def neighbours(self, note: Note) -> list[Neighbour]:
        """Every hard neighbour of `note`, links first, each note once."""
        gesehen: dict[str, Neighbour] = {}
        for ziel in note.links:
            treffer = self.index.resolve(ziel)
            if treffer is not None and treffer.rel != note.rel:
                gesehen.setdefault(treffer.rel, Neighbour(
                    treffer, "wikilink", WEIGHTS["wikilink"]))
        for rel in sorted(self._backlinks.get(note.rel, ())):
            gesehen.setdefault(rel, Neighbour(
                self.index.notes[rel], "wikilink", WEIGHTS["wikilink"]))
        for tag in note.tags:
            gruppe = self.index.tag_groups.get(tag, [])
            if len(gruppe) > self.tag_group_max:
                # Over the cap this tag is not an edge source AT ALL --
                # not a weaker one. See the module docstring.
                continue
            for andere in gruppe:
                if andere.rel != note.rel:
                    gesehen.setdefault(andere.rel, Neighbour(
                        andere, "tag", WEIGHTS["tag"]))
        return [gesehen[rel] for rel in sorted(gesehen)]
