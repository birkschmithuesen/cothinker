# CoThinker — tests of the shared text and wiki-name helpers
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Helpers that several blocks share: `shared/text.py`, `shared/wiki_names.py`.

    python3 -m pytest -q tests/test_shared_helpers.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import text as shared_text  # noqa: E402
from shared.text import derived_title, one_line  # noqa: E402
from shared.wiki_names import page_path, page_slug  # noqa: E402


def test_one_line_collapses_whitespace():
    assert one_line("  Ein \n Titel\t\tüber   zwei Zeilen ") == \
        "Ein Titel über zwei Zeilen"
    assert one_line("") == ""
    assert len(one_line("x" * 500)) == shared_text.MAX_TITLE_CHARS


def test_derived_title_takes_first_heading():
    assert derived_title("\n## Das Thema  \nText\n# Zwei") == "Das Thema"
    # no heading: the first non-empty line, list bullet stripped
    assert derived_title("\n\n- erster Punkt\n- zweiter") == "erster Punkt"
    assert derived_title("") == ""


def test_stage_state_uses_shared_max_pending_label():
    from gateway import stage_state
    assert stage_state.MAX_PENDING_LABEL is shared_text.MAX_PENDING_LABEL
    assert shared_text.MAX_PENDING_LABEL == 60
    assert stage_state.one_line is shared_text.one_line
    assert stage_state.derived_title is shared_text.derived_title


def test_page_slug_is_stable():
    created = datetime(2026, 11, 21, 9, 5)
    assert page_slug("Größe der Küche", created) == "groesse-der-kueche"
    assert page_slug("Größe der Küche", created) == \
        page_slug("Größe der Küche", created)
    assert page_slug("???", created) == "seite-0905"
    assert page_slug(None, created) == "seite-0905"


def test_page_path_counts_up_on_collision():
    base = Path("/tmp/session")
    assert page_path(base, "thema") == base / "entwuerfe" / "thema.md"
    assert page_path(base, "thema", 1) == base / "entwuerfe" / "thema.md"
    assert page_path(base, "thema", 2) == base / "entwuerfe" / "thema-2.md"
    assert page_path(str(base), "thema", 3) == base / "entwuerfe" / "thema-3.md"
