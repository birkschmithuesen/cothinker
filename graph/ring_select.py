#!/usr/bin/env python3
# CoThinker - which notes the surface shows
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The selection. It knows an edge source, never a file.

A BUDGET PER RING, not one global cap. Measured on the real vault (M5):

    seed                  cap  limit  rings of what was VISIBLE
    ars-electronica-2026   25     40  {0:1, 1:39}
    ars-electronica-2026   25    120  {0:1, 1:119}
    example-vault-b        25    120  {0:1, 1:97, 2:22}

Ring 1 alone is 97-227 nodes, so under a global cap the picture is the
seed plus a slice of ring 1 -- a star, while the design says rings. With
budgets 25/20/15 the same vault gives 61 nodes over three rings and 3-6
edges per node instead of 23-26.

The hard cap stays ON TOP of the budgets (the card, point C): it is the
emergency brake in the spirit of `MAX_STAGE_CHARS`, for the day somebody
widens the budgets, and it is what bounds the size of `buehne.json`.
"""
from __future__ import annotations

# Measured, see the module docstring.
RING_BUDGETS: tuple[int, ...] = (25, 20, 15)
# 1 + 25 + 20 + 15 = 61, so 19 nodes of head room. SET, not measured.
# Withdrawal signal: the surface becomes unreadable (lower it) or the
# rings run into it permanently (look at the budgets, not at this).
MAX_NODES = 80

# The grace window, taken over from an earlier graph surface:
# measured there at 2-3 exits per update, so a window of 3 overshoots the
# cap by single digits at worst and decays back to it on its own.
GRACE_ROUNDS = 3


def select_nodes(seed, source, *, budgets: tuple[int, ...] = RING_BUDGETS,
                 max_nodes: int = MAX_NODES) -> dict[str, int]:
    """`rel -> ring index` for the seed and its rings.

    Ranking inside a ring: closeness to what is already chosen first (the
    summed edge weight, so a linked note beats a merely co-tagged one),
    then novelty (`mtime`), then the path, so the same vault always gives
    the same picture.
    """
    chosen: dict[str, int] = {seed.rel: 0}
    rand = [seed]
    for tiefe, budget in enumerate(budgets, start=1):
        kandidaten: dict[str, list] = {}
        for note in rand:
            for nachbar in source.neighbours(note):
                if nachbar.note.rel in chosen:
                    continue
                eintrag = kandidaten.setdefault(nachbar.note.rel,
                                                [0.0, nachbar.note])
                eintrag[0] += nachbar.weight
        rangliste = sorted(
            kandidaten.items(),
            key=lambda kv: (-kv[1][0], -getattr(kv[1][1], "mtime", 0.0), kv[0]))
        rand = []
        for rel, (_, note) in rangliste[:budget]:
            if len(chosen) >= max_nodes:
                break
            chosen[rel] = tiefe
            rand.append(note)
        if not rand:
            break
    return chosen


class Hysteresis:
    """The grace list. ADDITIVE -- it displaces nobody.

    Taken over from an earlier graph surface, including
    the reason given there: the alternative -- a grace entry taking the
    slot of whoever it would have lost to -- can only protect a stale
    node by blocking a BRAND NEW one, and the new one is exactly what the
    room is talking about right now.

    So the visible count can sit slightly above the nominal cap. That
    overflow is bounded, not runaway: at most (exits per update) x
    (grace rounds), and every held node counts itself down. A node that is
    no longer reachable at all is dropped, never resurrected.
    """

    def __init__(self, rounds: int = GRACE_ROUNDS) -> None:
        self.rounds = rounds
        self._left: dict[str, int] = {}
        self._last: set[str] = set()

    def apply(self, chosen: set[str], eligible: set[str]) -> set[str]:
        """The selection plus whatever still holds a grace round."""
        for rel in self._last - chosen:
            self._left.setdefault(rel, self.rounds)
        kept: set[str] = set()
        for rel in list(self._left):
            if rel in chosen or rel not in eligible:
                del self._left[rel]
                continue
            if self._left[rel] <= 0:
                del self._left[rel]
                continue
            self._left[rel] -= 1
            kept.add(rel)
        final = set(chosen) | kept
        self._last = final
        return final
