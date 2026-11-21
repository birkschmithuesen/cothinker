# CoThinker - one-line text helpers shared by the blocks
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Titles and labels that have to be one line. Stdlib only.

Moved verbatim from `gateway/stage_state.py` so blocks can use them
without importing the gateway.
"""
from __future__ import annotations

import re

# A title is one line and a history label, not a paragraph.
MAX_TITLE_CHARS = 80

# One line on the page, not a paragraph.
MAX_PENDING_LABEL = 60

_WHITESPACE = re.compile(r"\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")


def one_line(text: str) -> str:
    """A value that has to be ONE line, collapsed and capped.

    Collapsed, not cut at the first newline: a title that arrives with a
    line break still says what it says, and dropping everything after the
    break would silently shorten the one label the history has.
    """
    return _WHITESPACE.sub(" ", text).strip()[:MAX_TITLE_CHARS]


def derived_title(markdown: str) -> str:
    """A history label DERIVED from the surface -- never invented.

    A call-out has no title: the round ordered a surface, not a topic. The
    history needs a label for every entry anyway, and the only honest
    source is what is on the surface -- its first heading, otherwise its
    first non-empty line. Writing a title the agent never wrote and nobody
    said would put invention on the stage, which is the one thing this
    whole module refuses to do (see `quote_is_spoken`). An empty label is
    better than a made-up one; the page falls back to the identity.
    """
    for line in markdown.split("\n"):
        heading = _HEADING.match(line)
        if heading is not None:
            return one_line(heading.group(1))
        if line.strip():
            # A list item is the common first line. The bullet is markup,
            # not text -- the rest of the line is quoted as it stands.
            return one_line(line.strip().lstrip("-*>").strip()) or one_line(line)
    return ""
