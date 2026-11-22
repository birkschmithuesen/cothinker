# CoThinker — InScribe template: the production format (issue #104)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The production half of the inscribe template: seven phases entered by
the group, their English and German prompts, and the PROVENANCE.toml rows
that cover them. Also guards that the production addition left the
workshop half (issue #103) unchanged.

python3 -m pytest -q tests/test_inscribe_production_format.py
"""
from __future__ import annotations

import hashlib
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from shared.templates import CORE_RULES, load_template

TEMPLATE_PATH = REPO / "templates" / "inscribe" / "template.toml"
PROVENANCE_PATH = REPO / "templates" / "inscribe" / "PROVENANCE.toml"
PROMPTS_DIR = REPO / "templates" / "inscribe" / "prompts" / "production"

PHASE_IDS_IN_ORDER = ("terms", "questions", "interviews", "frame", "selection", "cards", "script")
CUE_IDS_IN_ORDER = ("0", "0b", "1", "1b", "2", "3", "4", "5", "6", "7", "8", "9")


def _load_production():
    return load_template(TEMPLATE_PATH, format="production")


def _load_workshop():
    return load_template(TEMPLATE_PATH, format="workshop")


def test_production_format_loads_with_seven_phases_in_order():
    template = _load_production()
    assert tuple(p.id for p in template.phases) == PHASE_IDS_IN_ORDER


def test_card_variant_only_no_prose_variant_files():
    assert PROMPTS_DIR.is_dir(), f"missing {PROMPTS_DIR.relative_to(REPO)}"
    expected = set()
    for phase_id in PHASE_IDS_IN_ORDER:
        expected.add(f"phase-{phase_id}.en.md")
        expected.add(f"phase-{phase_id}.de.md")
    actual = {p.name for p in PROMPTS_DIR.glob("*.md")}
    assert actual == expected, f"unexpected prompt files {actual - expected}, missing {expected - actual}"


def test_every_phase_has_an_english_and_german_prompt():
    template = _load_production()
    for phase in template.phases:
        en = PROMPTS_DIR / f"phase-{phase.id}.en.md"
        de = PROMPTS_DIR / f"phase-{phase.id}.de.md"
        assert en.is_file(), f"missing {en.relative_to(REPO)}"
        assert de.is_file(), f"missing {de.relative_to(REPO)}"
        assert en.read_text(encoding="utf-8").strip(), f"{en.relative_to(REPO)} is empty"
        assert de.read_text(encoding="utf-8").strip(), f"{de.relative_to(REPO)} is empty"


def test_group_enters_every_phase():
    template = _load_production()
    for phase in template.phases:
        assert phase.enter_when == ("group",), (
            f"phase {phase.id!r} must be advanced by the group only, got {phase.enter_when}"
        )


def test_piece_blocks_are_group_scoped():
    template = _load_production()
    assert template.blocks, "the production format must declare at least one block"
    for block in template.blocks:
        assert block.config.get("state_scope") == "group", (
            f"block {block.id!r} must be state_scope='group' in the production format"
        )


def test_prompts_listed_in_provenance():
    data = tomllib.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in data.get("file", [])}
    for phase_id in PHASE_IDS_IN_ORDER:
        for lang in ("en", "de"):
            path = PROMPTS_DIR / f"phase-{phase_id}.{lang}.md"
            rel = str(path.relative_to(REPO))
            assert rel in entries, f"{rel} not listed in {PROVENANCE_PATH.relative_to(REPO)}"
            entry = entries[rel]
            assert entry.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest(), (
                f"stale sha256 for {rel}"
            )
            assert entry.get("reviewed") is True, f"{rel} not marked reviewed"


def test_workshop_format_unchanged():
    template = _load_workshop()
    assert tuple(p.id for p in template.phases) == CUE_IDS_IN_ORDER
    for phase in template.phases:
        assert phase.enter_when == ("operator",), (
            f"phase {phase.id!r} must still be advanced by the operator only, got {phase.enter_when}"
        )
    for rule in CORE_RULES:
        assert rule in template.rules
