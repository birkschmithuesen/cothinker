#!/usr/bin/env python3
# CoThinker - reading the agent's proposal file
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Read `vorschlag-<datum>.jsonl` -- the agent's only way onto the stage.

One file per meeting, dated like `protokoll-<datum>.md`. A single flat
`vorschlag.jsonl` is never rotated: the first run of the next meeting read
it from byte 0 again and put the previous meeting's mirror line,
decisions and question on today's stage.

Append-only, one JSON object per line. Line-oriented on purpose: a
half-written line is either complete or discarded, so the failure class
that made the stage unreadable (review-report point 2) cannot cause
damage here.

Everything in this file is foreign input from a language model. A broken
line is discarded and counted; it never reaches the surface. The VALUES
are not cleaned here -- `stage_state._text()` is the funnel every field
of every action passes through, and it scrubs what UTF-8 cannot encode.
"""
from __future__ import annotations

import json
from pathlib import Path


def read_proposals(path: Path, offset: int) -> tuple[list[dict], int, int]:
    """Return `(actions, new_offset, discarded)` for everything appended
    after `offset` bytes.

    A trailing fragment without a newline is NOT consumed: the agent may
    still be writing it. The offset then stops in front of it and the
    next call picks the line up whole.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset, 0
    if size < offset:
        offset = 0                      # file was replaced or truncated
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            raw = fh.read()
    except OSError:
        return [], offset, 0

    consumed = raw.rfind(b"\n") + 1     # 0 when there is no complete line
    text = raw[:consumed].decode("utf-8", "replace")

    actions: list[dict] = []
    discarded = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            discarded += 1
            continue
        if isinstance(obj, dict):
            actions.append(obj)
        else:
            discarded += 1
    return actions, offset + consumed, discarded
