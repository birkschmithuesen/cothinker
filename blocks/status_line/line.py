# CoThinker — blocks/status_line/line.py: the winning operating state
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`pick()`: the winning state out of everything that currently applies.

Moved verbatim out of the former `gateway/status_line.py` (issue #41, Wave
B.4, ADR 0018) -- the rendering stayed behind in `gateway/stage_texts.py`,
this stays with the block that owns the precedence decision.
"""
from __future__ import annotations

# Highest first.
PRECEDENCE = ("failed", "working", "backlog", "listening")


def pick(states: list[str]) -> str:
    """The winning state out of everything that currently applies."""
    for candidate in PRECEDENCE:
        if candidate in states:
            return candidate
    return "listening"
