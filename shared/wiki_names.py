# CoThinker - wiki page names shared by the blocks
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""File names of topic pages and the slug they are built from. Stdlib only.

Moved verbatim from `gateway/wiki.py` so blocks can use them without
importing the gateway.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Topic pages (MM-G D4/D5): where they go.
PAGE_DIR = "entwuerfe"

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(text) -> str:
    """casefold, umlauts spelled out, every other non-alnum run -> '-'."""
    if not isinstance(text, str):
        return ""
    # NFC first: a decomposed 'u' + diaeresis (macOS file names) must
    # become 'ue' like the composed one, not 'u'.
    t = unicodedata.normalize("NFC", text).casefold().translate(_UMLAUTS)
    # Accents that are not German umlauts: 'é' -> 'e', not a hyphen.
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return _NON_ALNUM.sub("-", t).strip("-")


def slugify(text, limit: int = 60) -> str:
    """The file-name/matching form of a title. "" when nothing is left."""
    return _normalise(text)[:max(0, limit)].rstrip("-")


def page_slug(titel, created) -> str:
    """The page's file name: the title's slug, else `seite-HHMM`."""
    return slugify(titel) or f"seite-{created:%H%M}"


def page_path(session_dir, slug: str, n: int = 1) -> Path:
    """`<session_dir>/entwuerfe/<slug>.md`, from the 2nd on `<slug>-<n>.md`."""
    name = slug if n <= 1 else f"{slug}-{n}"
    return Path(session_dir) / PAGE_DIR / f"{name}.md"
