# CoThinker — tests for template formats: one template, several phase lists and surface sets
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_template_formats.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared import instance as si
from shared import templates as st

TEMPLATE_HEADER = (
    '[template]\nname = "x"\napi_version = 1\nrules = ["grounding", "add-dont-restructure"]\n'
)


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "template.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_template_without_formats_table_loads_unchanged(tmp_path):
    body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
    )
    t = st.load_template(_write(tmp_path, body))
    assert t.format is None
    assert t.formats == ()
    assert [b.id for b in t.blocks] == ["a"]
    assert t.phases[0].id == "p" and t.phases[0].blocks == ("a",)


def test_formatted_template_without_format_argument_raises(tmp_path):
    body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall", "doc"]\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = ["a"]\n'
    )
    path = _write(tmp_path, body)
    with pytest.raises(st.TemplateError, match="name one"):
        st.load_template(path)


def test_unknown_format_argument_raises_naming_it(tmp_path):
    body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall", "doc"]\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = ["a"]\n'
    )
    path = _write(tmp_path, body)
    with pytest.raises(st.TemplateError, match="unknown"):
        st.load_template(path, "nonexistent-format")
    with pytest.raises(st.TemplateError, match="nonexistent-format"):
        st.load_template(path, "nonexistent-format")


def test_phases_are_filtered_by_format(tmp_path):
    body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall", "doc"]\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = ["a"]\n'
    )
    path = _write(tmp_path, body)
    short = st.load_template(path, "short")
    assert [p.id for p in short.phases] == ["p"]
    long_ = st.load_template(path, "long")
    assert [p.id for p in long_.phases] == ["q"]


def test_phase_without_format_in_formatted_template_raises(tmp_path):
    body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall"]\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
    )
    path = _write(tmp_path, body)
    with pytest.raises(st.TemplateError, match="format"):
        st.load_template(path, "short")


def test_block_without_format_is_shared_across_formats_and_format_key_not_leaked(tmp_path):
    body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall"]\n'
        '[[blocks]]\nid = "shared"\n'
        '[[blocks]]\nid = "only-short"\nformat = "short"\nfoo = 1\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["shared", "only-short"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = ["shared"]\n'
    )
    path = _write(tmp_path, body)
    t_short = st.load_template(path, "short")
    assert {b.id for b in t_short.blocks} == {"shared", "only-short"}
    only_short = next(b for b in t_short.blocks if b.id == "only-short")
    assert only_short.config == {"foo": 1}
    t_long = st.load_template(path, "long")
    assert {b.id for b in t_long.blocks} == {"shared"}


def test_format_surfaces_replace_template_level_surfaces(tmp_path):
    body = (
        '[template]\nname = "x"\napi_version = 1\n'
        'rules = ["grounding", "add-dont-restructure"]\n'
        'surfaces = ["top-surface"]\noptional_surfaces = ["top-optional"]\n'
        '[formats.short]\nsurfaces = ["wall"]\noptional_surfaces = ["side"]\n'
        '[formats.long]\nsurfaces = ["wall", "doc"]\noptional_surfaces = []\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = ["a"]\n'
    )
    path = _write(tmp_path, body)
    t = st.load_template(path, "short")
    assert t.surfaces == ("wall",)
    assert t.optional_surfaces == ("side",)
    assert t.format == "short"
    assert t.formats == ("short", "long")


def test_duplicate_block_id_within_one_format_raises_but_across_formats_is_fine(tmp_path):
    dup_body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall"]\n'
        '[[blocks]]\nid = "a"\nformat = "short"\n'
        '[[blocks]]\nid = "a"\nformat = "short"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = []\n'
    )
    dup_path = _write(tmp_path, dup_body)
    with pytest.raises(st.TemplateError, match="duplicate"):
        st.load_template(dup_path, "short")

    ok_body = TEMPLATE_HEADER + (
        '[formats.short]\nsurfaces = ["wall"]\n'
        '[formats.long]\nsurfaces = ["wall"]\n'
        '[[blocks]]\nid = "a"\nformat = "short"\n'
        '[[blocks]]\nid = "a"\nformat = "long"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
        '[[phases]]\nid = "q"\nformat = "long"\nblocks = ["a"]\n'
    )
    ok_path = (tmp_path / "ok.toml")
    ok_path.write_text(ok_body, encoding="utf-8")
    t_short = st.load_template(ok_path, "short")
    t_long = st.load_template(ok_path, "long")
    assert [b.id for b in t_short.blocks] == ["a"]
    assert [b.id for b in t_long.blocks] == ["a"]


def test_format_key_without_formats_table_raises_on_block_and_phase(tmp_path):
    block_body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "a"\nformat = "short"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
    )
    with pytest.raises(st.TemplateError, match=r"no \[formats\]"):
        st.load_template(_write(tmp_path, block_body))

    phase_body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nformat = "short"\nblocks = ["a"]\n'
    )
    phase_path = tmp_path / "phase.toml"
    phase_path.write_text(phase_body, encoding="utf-8")
    with pytest.raises(st.TemplateError, match=r"no \[formats\]"):
        st.load_template(phase_path)


def test_instance_config_template_format_reads_template_table(tmp_path):
    (tmp_path / "instance.toml").write_text(
        '[instance]\nname = "x"\ntimezone = "UTC"\nlanguages = ["en"]\n'
        'app_format = "x/1"\nposter_format = "x/1"\n'
        '[template]\nname = "t"\nformat = "long"\n',
        encoding="utf-8",
    )
    cfg = si.load(tmp_path)
    assert cfg.template_name() == "t"
    assert cfg.template_format() == "long"
