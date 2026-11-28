# CoThinker — blocks/release/logic.py: the released/withdrawn result shape
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`build_release`, `build_withdrawal`, `state_of` (issue #102): the pure
shape of a published result, ported from the archive's
`workshop/release.py`. A released object carries `released_at` (int ms),
`withdrawn_at` (null until withdrawn), `version`, `quote` and `prologue`;
a withdrawal sets `withdrawn_at` and nulls `quote`/`prologue`, keeping the
rest.
"""
from __future__ import annotations

from typing import Optional


def build_release(*, version: str, quote: str, prologue: str, at_ms: int) -> dict:
    return {"released_at": at_ms, "withdrawn_at": None,
            "version": version, "quote": quote, "prologue": prologue}


def build_withdrawal(released: dict, at_ms: int) -> dict:
    return {**released, "withdrawn_at": at_ms, "quote": None, "prologue": None}


def state_of(released: Optional[dict]) -> str:
    if released is None:
        return "none"
    return "withdrawn" if released.get("withdrawn_at") is not None else "released"
