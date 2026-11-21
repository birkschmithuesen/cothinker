# CoThinker — blocks/condense/quotes.py: verbatim quote check
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A quote is only kept when it is a verbatim substring of what was said."""
from __future__ import annotations

from typing import Callable, Iterable, Optional


def verbatim_quotes(quotes: Iterable[object], segment_texts: Iterable[str],
                    log: Optional[Callable[[str], None]] = None) -> list[str]:
    """Returns the quotes that occur unchanged in one of `segment_texts`,
    de-duplicated, in order. Every other quote is dropped and logged."""
    texts = list(segment_texts)
    kept: list[str] = []
    for q in quotes:
        if isinstance(q, str) and q.strip() and any(q in t for t in texts):
            if q not in kept:
                kept.append(q)
        elif log is not None:
            log(f"condense: dropped non-verbatim quote {q!r}")
    return kept
