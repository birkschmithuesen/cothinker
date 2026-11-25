# CoThinker — tests for shared/templates.py: template format + loader
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_templates.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared import instance as si
from shared import templates as st

REPO = Path(__file__).resolve().parents[1]
REFLECT = REPO / "templates" / "reflect" / "template.toml"

# ADR 0010's own 12, plus `voice-attribution` as a 13th (issue #178) and
# `call-window` as a 14th (issue #180) and `live-transcript` as a 15th
# (issue #181) -- the ADR's text stays as it was, unchanged (it is only
# ever superseded or amended, never rewritten); see
# docs/architecture/blocks-inventory.md.
REFLECT_15 = (
    "capture-bullets",
    "topics",
    "decisions-tasks-questions",
    "condense",
    "minutes-document",
    "mirroring-coach",
    "knowledge-link",
    "timer",
    "speaker-queue",
    "speaking-share",
    "status-line",
    "graph",
    "voice-attribution",
    "call-window",
    "live-transcript",
)

TEMPLATE_HEADER = '[template]\nname = "x"\napi_version = 1\nrules = ["grounding", "add-dont-restructure"]\n'


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "template.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _instance_dir(tmp_path, extra: str = "") -> Path:
    d = tmp_path / "inst"
    d.mkdir()
    (d / "instance.toml").write_text(
        '[instance]\nname = "x"\ntimezone = "UTC"\nlanguages = ["en"]\n'
        'app_format = "x/1"\nposter_format = "x/1"\n' + extra,
        encoding="utf-8",
    )
    return d


def test_reflect_template_loads():
    t = st.load_template(REFLECT)
    assert t.name == "reflect"


def test_reflect_block_ids_match_adr_0010_order():
    t = st.load_template(REFLECT)
    assert tuple(b.id for b in t.blocks) == REFLECT_15


def test_reflect_has_one_session_phase_with_all_blocks():
    t = st.load_template(REFLECT)
    assert len(t.phases) == 1
    phase = t.phases[0]
    assert phase.id == "session"
    assert phase.blocks == REFLECT_15


def test_phase_naming_unknown_block_raises(tmp_path):
    body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a", "missing"]\n'
    )
    with pytest.raises(st.TemplateError):
        st.load_template(_write(tmp_path, body))


def test_missing_grounding_rule_raises(tmp_path):
    body = (
        '[template]\nname = "x"\napi_version = 1\nrules = ["add-dont-restructure"]\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
    )
    with pytest.raises(st.TemplateError):
        st.load_template(_write(tmp_path, body))


def test_unknown_top_level_key_raises(tmp_path):
    body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
        '[extra]\nfoo = 1\n'
    )
    with pytest.raises(st.TemplateError):
        st.load_template(_write(tmp_path, body))


def test_duplicate_block_id_raises(tmp_path):
    body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "a"\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
    )
    with pytest.raises(st.TemplateError):
        st.load_template(_write(tmp_path, body))


def test_wrong_api_version_raises(tmp_path):
    body = (
        '[template]\nname = "x"\napi_version = 2\nrules = ["grounding", "add-dont-restructure"]\n'
        '[[blocks]]\nid = "a"\n'
        '[[phases]]\nid = "p"\nblocks = ["a"]\n'
    )
    with pytest.raises(st.TemplateError):
        st.load_template(_write(tmp_path, body))


def test_merged_block_config_overlays_instance_over_template_default(tmp_path):
    body = TEMPLATE_HEADER + (
        '[[blocks]]\nid = "timer"\ndefault_minutes = 10\n'
        '[[blocks]]\nid = "other"\n'
        '[[phases]]\nid = "p"\nblocks = ["timer", "other"]\n'
    )
    t = st.load_template(_write(tmp_path, body))
    inst_dir = _instance_dir(tmp_path, '[blocks.timer]\ndefault_minutes = 45\n')
    inst = si._from_dir(inst_dir)
    merged = st.merged_block_config(t, inst)
    assert merged["timer"] == {"default_minutes": 45}
    assert merged["other"] == {}


def test_instance_without_template_table_has_no_template_name(tmp_path):
    inst_dir = _instance_dir(tmp_path)
    inst = si._from_dir(inst_dir)
    assert inst.template_name() is None
