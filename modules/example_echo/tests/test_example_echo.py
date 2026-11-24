# CoThinker — modules/example_echo: proves the module seam end to end (repo split 5.3)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q modules/example_echo/tests/test_example_echo.py

Drives the loaded module's own `on_event` directly with a "block" `CoreEvent`
(the shape `workshop.hooks.ModuleHook` builds in the private deployment) --
`workshop/` is not part of this repo, so the hook layer is out of scope here.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from shared import instance as si
from shared.modules import CoreEvent, ModuleContext, StateStore, load_modules

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "instances" / "example"
MODULES_DIR = REPO / "modules"


@pytest.fixture
def cfg():
    return si.load(EXAMPLE)


@pytest.fixture
def loaded(cfg):
    mods = load_modules(cfg, [MODULES_DIR])
    assert len(mods) == 1 and mods[0].name == "example_echo"
    return mods


def test_instances_example_enables_example_echo(cfg):
    assert cfg.modules == ("example_echo",)


def test_load_modules_finds_example_echo_under_modules_dir(loaded):
    assert len(loaded) == 1 and loaded[0].name == "example_echo"


def test_module_on_event_writes_state_with_the_blocks_word_count(cfg, loaded, tmp_path):
    ctx = ModuleContext(instance=cfg, state=StateStore(tmp_path / "state"))
    loaded[0].on_event(CoreEvent(kind="block", payload={"text": "hallo welt wie gehts"}), ctx)
    assert ctx.state.read("echo.json") == {"words": 4}


def test_module_declares_the_echo_count_tool(loaded):
    tool = next((t for t in loaded[0].tools() if t.name == "echo_count"), None)
    assert tool is not None


def test_echo_count_tool_fn_returns_the_word_count(loaded):
    tool = next(t for t in loaded[0].tools() if t.name == "echo_count")
    assert tool.fn(text="hallo welt wie gehts") == {"words": 4}


def test_load_modules_with_enabled_empty_loads_nothing(cfg):
    cfg_off = dataclasses.replace(cfg, modules=())
    assert load_modules(cfg_off, [MODULES_DIR]) == []
