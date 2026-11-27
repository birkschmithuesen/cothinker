# CoThinker — tests for blocks/forms_styles: the forms & styles block
# (issue #77)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_forms_styles.py"""
from __future__ import annotations

from pathlib import Path

import pytest

import blocks.forms_styles as forms_styles
from blocks.forms_styles import catalogue
from shared.blocks import BlockContext
from shared.modules import StateStore


def make_block(tmp_path, *, music_forms=False):
    block = forms_styles.make_block()
    ctx = BlockContext(
        instance=None, session_dir=Path(tmp_path),
        state=StateStore(Path(tmp_path) / block.id), agent=None, llm=None,
        language="en", clock=lambda: 0.0, emit=lambda intent: None,
        artefact=lambda name, text: None)
    block.start(ctx, {"music_forms": music_forms})
    return block, ctx


def write_form(directory: Path, *, slug: str, name: str, keywords: list[str], info: str = "Info.") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    keyword_list = ", ".join(f'"{k}"' for k in keywords)
    (directory / f"{slug}.md").write_text(
        f'+++\nslug = "{slug}"\nname = "{name}"\nkeywords = [{keyword_list}]\n+++\n{info}\n',
        encoding="utf-8")


def test_every_scene_form_has_a_non_empty_prompt_file():
    scene_forms, _default = catalogue.load_scene_forms(forms_styles.SCENE_FORMS_FILE)
    for name in scene_forms:
        path = forms_styles.SCENE_FORM_PROMPTS_DIR / f"{name}.en.md"
        assert path.is_file(), f"missing prompt for scene form {name!r}: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"empty prompt for scene form {name!r}"


def test_default_scene_form_is_in_the_catalogue():
    scene_forms, default = catalogue.load_scene_forms(forms_styles.SCENE_FORMS_FILE)
    assert default in scene_forms


def test_empty_or_duplicate_form_catalogue_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(catalogue.CatalogueError):
        catalogue.load_forms(empty_dir)

    dup_dir = tmp_path / "dup"
    write_form(dup_dir, slug="same-slug", name="First", keywords=["one"])
    # A second, differently-named file that declares the same `slug` field
    # -- the catalogue identity collides even though the filenames don't.
    (dup_dir / "second-file.md").write_text(
        '+++\nslug = "same-slug"\nname = "Second"\nkeywords = ["two"]\n+++\nInfo.\n',
        encoding="utf-8")
    with pytest.raises(catalogue.CatalogueError):
        catalogue.load_forms(dup_dir)


def test_find_forms_matches_whole_words_not_substrings(tmp_path):
    directory = tmp_path / "catalogue"
    write_form(directory, slug="song-form", name="Song Form", keywords=["song"])
    write_form(directory, slug="songbird-piece", name="Songbird Piece", keywords=["songbird"])
    forms = catalogue.load_forms(directory)

    assert catalogue.find_forms(forms, "song") == ["song-form"]


def test_music_forms_only_match_when_music_forms_is_enabled(tmp_path):
    off_block, _ctx = make_block(tmp_path / "off", music_forms=False)
    on_block, _ctx2 = make_block(tmp_path / "on", music_forms=True)

    # "ostinato" is a keyword of the music-only catalogue entry
    # "ostinato-and-ground-bass" and does not occur in the theatre catalogue.
    assert off_block._find_forms("ostinato")["data"]["forms"] == []
    on_hits = on_block._find_forms("ostinato")["data"]["forms"]
    assert [hit["slug"] for hit in on_hits] == ["ostinato-and-ground-bass"]


def test_find_forms_golden_query(tmp_path):
    directory = tmp_path / "catalogue"
    write_form(directory, slug="alpha-piece", name="Alpha Piece", keywords=["mirror", "echo"])
    write_form(directory, slug="beta-piece", name="Beta Piece", keywords=["echo chamber", "shadow"])
    write_form(directory, slug="gamma-piece", name="Gamma Piece", keywords=["mirror image"])
    forms = catalogue.load_forms(directory)

    assert catalogue.find_forms(forms, "echo") == ["alpha-piece", "beta-piece"]


def test_style_rule_block_strips_the_leading_header_comment():
    path = forms_styles.STYLE_PROMPTS_DIR / "measured-baseline.en.md"
    raw = path.read_text(encoding="utf-8")
    assert raw.lstrip().startswith("<!--")

    rule_block = catalogue.load_style_rule_block(path)

    assert not rule_block.startswith("<!--")
    assert "<!--" not in rule_block
    assert "Measured baseline" in rule_block


def test_set_scene_form_marks_the_scene_stale(tmp_path):
    block, ctx = make_block(tmp_path)
    set_scene_form = next(spec.fn for spec in block.tools() if spec.name == "set_scene_form")

    result = set_scene_form(scene="scene-1", form="monologue")

    assert result["data"] == {"scene": "scene-1", "form": "monologue", "stale": True}
    assert ctx.state.read("scenes.json")["scene-1"]["stale"] is True
