# CoThinker — blocks/status_line/group.py: group-mode progress derivation
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`pick_progress()` and the two intent payload shapes for `mode = "group"`
(issue #67): the long format's group status (phase, progress of the piece).

Ported idea, not the identifiers, from interview-theater's
`cothinker_status.leite_ab()` -- a state derived purely from existing facts,
first match wins, no model call. That source's facts carry timestamps
(`lauf_seit`, `segment_empfangen_am`) and derive a freshness timeout; this
block only receives `phase` events and `block_output` facts (piece_rev,
interviews, findings) with no timestamps at all, so there is nothing to
time out -- `pick_progress()` instead picks the most advanced progress
signal out of whatever facts have arrived so far.
"""
from __future__ import annotations

# Most advanced stage of the piece first.
PROGRESS_PRECEDENCE = ("piece_rev", "findings", "interviews")


def pick_progress(available: dict) -> str | None:
    """The name of the winning progress field out of everything reported so
    far, or None if nothing has been reported yet."""
    for candidate in PROGRESS_PRECEDENCE:
        if candidate in available:
            return candidate
    return None


def group_payload(phase: str | None, piece_rev, interviews, findings) -> dict:
    """The `status:group` (room audience) payload: phase plus the single
    most advanced progress signal."""
    available = {}
    if piece_rev is not None:
        available["piece_rev"] = piece_rev
    if findings is not None:
        available["findings"] = findings
    if interviews is not None:
        available["interviews"] = interviews
    winner = pick_progress(available)
    progress = {winner: available[winner]} if winner else {}
    return {"phase": phase, "progress": progress}


def operator_payload(phase: str | None, piece_rev, interviews, findings) -> dict:
    """The `status:operator` payload: every sticky progress fact, not just
    the winning one."""
    return {
        "phase": phase,
        "piece_rev": piece_rev,
        "interviews": interviews,
        "findings": findings,
    }
