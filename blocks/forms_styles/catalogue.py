# CoThinker — blocks/forms_styles/catalogue.py: deterministic matching/lookup
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The form catalogue (theatre + optional music), the scene forms and the
writing styles: stdlib-only loading and matching, no agent/model calls.

A catalogue file is a `.md` file with a TOML frontmatter block fenced by
`+++` lines (`slug`, `name`, `keywords`), followed by the descriptive text
returned to the agent as `info`. `scene_forms.toml` and `styles.toml` hold
their small, fixed catalogues directly as TOML tables.

`find_forms` matches a query against a form's name and keywords by whole
word, case- and accent-insensitive (`normalize`) -- a query of "song" does
not match a keyword like "songbird" that merely contains it.
"""
from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: How many matches `find_forms` returns at most -- a form's `info` text can
#: run to a few hundred words, so the agent's context stays small even when
#: a query is broad.
MAX_RESULTS = 5

_FENCE = "+++\n"
_HEADER_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class CatalogueError(Exception):
    pass


@dataclass(frozen=True)
class Form:
    slug: str
    name: str
    keywords: tuple[str, ...]
    info: str


@dataclass(frozen=True)
class SceneForm:
    name: str
    label: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Style:
    slug: str
    name: str
    summary: str


def normalize(text: str) -> str:
    """Lower case, accents and umlauts stripped (`é` -> `e`), everything but
    letters and digits collapsed to a single space -- so "song" and "Song,"
    and "Chanson" differ only where they should."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _word_in(term: str, haystack: str) -> bool:
    """Whole-word match of `term` (already normalized) inside `haystack`
    (already normalized), plural-s allowed -- never a substring hit such as
    "song" inside "songbird"."""
    if not term:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"s?(?![a-z0-9])", haystack) is not None


def strip_header_comment(text: str) -> str:
    """Removes a leading `<!-- ... -->` block (where a ported file's
    provenance note lives) -- that note is for the human reading the
    repository, not for the agent's prompt."""
    return _HEADER_COMMENT.sub("", text, count=1).strip()


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_FENCE):
        raise CatalogueError(f"{path}: missing +++ frontmatter")
    _, _, rest = text.partition(_FENCE)
    front, sep, body = rest.partition(_FENCE)
    if not sep:
        raise CatalogueError(f"{path}: unterminated +++ frontmatter")
    return tomllib.loads(front), body.strip()


def load_form(path: Path) -> Form:
    meta, body = _frontmatter(path)
    slug, name = meta.get("slug"), meta.get("name")
    if not slug or not name or not body:
        raise CatalogueError(f"{path}: slug, name or body missing")
    return Form(slug=slug, name=name, keywords=tuple(meta.get("keywords", ())), info=body)


def load_forms(directory: Path) -> dict[str, Form]:
    """Every `*.md` file in `directory`, keyed by its `slug` field. Raises on
    an empty directory (a catalogue with nothing in it is a loading bug, not
    a valid, if boring, catalogue) and on two files naming the same slug."""
    forms: dict[str, Form] = {}
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise CatalogueError(f"empty catalogue: {directory}")
    for path in paths:
        form = load_form(path)
        if form.slug in forms:
            raise CatalogueError(f"duplicate form slug {form.slug!r} in {directory}")
        forms[form.slug] = form
    return forms


def load_scene_forms(path: Path) -> tuple[dict[str, SceneForm], str]:
    """The fixed scene-form catalogue (`[[scene_form]]` tables) and the
    `default` name. Raises on an empty table, a duplicate name or a default
    that the table itself does not list."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("scene_form", [])
    if not entries:
        raise CatalogueError(f"empty scene form catalogue: {path}")
    forms: dict[str, SceneForm] = {}
    for entry in entries:
        name = entry["name"]
        if name in forms:
            raise CatalogueError(f"duplicate scene form {name!r} in {path}")
        forms[name] = SceneForm(name=name, label=entry["label"],
                                keywords=tuple(entry.get("keywords", ())))
    default = data.get("default")
    if default not in forms:
        raise CatalogueError(f"default scene form {default!r} not in catalogue: {path}")
    return forms, default


def load_styles(path: Path) -> dict[str, Style]:
    """The fixed style catalogue (`[[style]]` tables). Raises on an empty
    table or a duplicate slug."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("style", [])
    if not entries:
        raise CatalogueError(f"empty style catalogue: {path}")
    styles: dict[str, Style] = {}
    for entry in entries:
        slug = entry["slug"]
        if slug in styles:
            raise CatalogueError(f"duplicate style {slug!r} in {path}")
        styles[slug] = Style(slug=slug, name=entry["name"], summary=entry["summary"])
    return styles


def find_forms(catalogue: Mapping[str, Form], query: str, *, limit: int = MAX_RESULTS) -> list[str]:
    """Slugs whose name or keywords contain `query` as a whole word,
    normalized, sorted for a deterministic result, at most `limit`."""
    term = normalize(query)
    if not term:
        return []
    hits = [slug for slug, form in catalogue.items()
            if _word_in(term, normalize(form.name))
            or any(_word_in(term, normalize(k)) for k in form.keywords)]
    return sorted(hits)[:limit]


def load_style_rule_block(path: Path) -> str:
    """The style's rule block for the scene prompt, header comment stripped
    (mirrors the archived `stile.regelblock`'s behaviour)."""
    return strip_header_comment(path.read_text(encoding="utf-8"))
