# CoThinker — shared/modules.py: module seam (stdlib only)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_modules.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared import modules as sm
from shared.instance import InstanceConfig

GOOD_TOML = """\
[module]
name = "fixture_mod"
version = "0.1.0"
api_version = 1
entry = "module:FixtureModule"
profile = "profile"

[state]
files = ["fixture.json"]
"""

MODULE_PY = """\
class FixtureModule:
    def __init__(self, *, name, api_version, module_dir, profile_dir):
        self.name = name
        self.api_version = api_version
        self._module_dir = module_dir
        self._profile_dir = profile_dir

    def tools(self):
        return []

    def phases(self):
        return []

    def profile_dir(self):
        return self._profile_dir

    def on_event(self, ev, ctx):
        pass
"""


@pytest.fixture
def search_root(tmp_path):
    root = tmp_path / "search"
    mod_dir = root / "fixture_mod"
    (mod_dir / "profile").mkdir(parents=True)
    (mod_dir / "module.toml").write_text(GOOD_TOML, encoding="utf-8")
    (mod_dir / "module.py").write_text(MODULE_PY, encoding="utf-8")
    return root


@pytest.fixture
def mod(search_root):
    return sm.load_module(search_root / "fixture_mod")


def test_module_dir_loads_with_declared_name_and_api_version(mod):
    assert mod.name == "fixture_mod"
    assert mod.api_version == 1


def test_profile_dir_resolves_under_the_module_dir(mod, search_root):
    assert mod.profile_dir() == search_root / "fixture_mod" / "profile"


def test_tools_and_phases_return_lists(mod):
    assert mod.tools() == []
    assert mod.phases() == []


def test_api_version_mismatch_raises_module_error(search_root):
    mod_dir = search_root / "fixture_mod"
    (mod_dir / "module.toml").write_text(GOOD_TOML.replace("api_version = 1", "api_version = 2"), encoding="utf-8")
    with pytest.raises(sm.ModuleError):
        sm.load_module(mod_dir)


def test_declared_tests_dir_missing_raises_module_error(search_root):
    mod_dir = search_root / "fixture_mod"
    (mod_dir / "module.toml").write_text(GOOD_TOML.replace(
        'profile = "profile"', 'profile = "profile"\ntests = "tests"'), encoding="utf-8")
    with pytest.raises(sm.ModuleError, match="tests"):
        sm.load_module(mod_dir)


def test_declared_tests_dir_present_loads_fine(search_root):
    mod_dir = search_root / "fixture_mod"
    (mod_dir / "tests").mkdir()
    (mod_dir / "tests" / "test_fixture_mod.py").write_text("", encoding="utf-8")
    (mod_dir / "module.toml").write_text(GOOD_TOML.replace(
        'profile = "profile"', 'profile = "profile"\ntests = "tests"'), encoding="utf-8")
    mod = sm.load_module(mod_dir)
    assert mod.name == "fixture_mod"


def test_load_modules_finds_the_one_known_module(search_root):
    cfg = InstanceConfig(name="demo", root=search_root, timezone="Europe/Berlin", languages=("de",),
                          app_format="x/1", poster_format="y/1", modules=("fixture_mod",))
    loaded = sm.load_modules(cfg, [search_root])
    assert len(loaded) == 1 and loaded[0].name == "fixture_mod"


def test_unknown_module_name_raises_module_error_naming_it(search_root):
    cfg = InstanceConfig(name="demo", root=search_root, timezone="Europe/Berlin", languages=("de",),
                          app_format="x/1", poster_format="y/1", modules=("fixture_mod", "no_such_module"))
    with pytest.raises(sm.ModuleError, match="no_such_module"):
        sm.load_modules(cfg, [search_root])


def test_state_store_read_of_absent_file_returns_empty_dict(tmp_path):
    store = sm.StateStore(tmp_path / "state")
    assert store.read("fixture.json") == {}


def test_state_store_write_then_read_round_trips_with_no_leftover_tmp_file(tmp_path):
    state_dir = tmp_path / "state"
    store = sm.StateStore(state_dir)
    store.write("fixture.json", {"count": 3})
    assert store.read("fixture.json") == {"count": 3}
    assert list(state_dir.glob("*.tmp")) == []


def test_interrupted_write_keeps_previous_content_and_leaves_no_temp_file(tmp_path):
    state_dir = tmp_path / "state"
    store = sm.StateStore(state_dir)
    store.write("fixture.json", {"count": 3})
    with pytest.raises(TypeError):
        store.write("fixture.json", {"count": 4, "bad": object()})
    assert store.read("fixture.json") == {"count": 3}
    assert sorted(p.name for p in state_dir.iterdir()) == ["fixture.json"]


def test_entry_file_that_fails_to_import_raises_module_error_naming_it(search_root):
    broken_dir = search_root / "broken_mod"
    broken_dir.mkdir()
    (broken_dir / "module.toml").write_text(GOOD_TOML.replace('"fixture_mod"', '"broken_mod"'), encoding="utf-8")
    (broken_dir / "module.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(sm.ModuleError, match="broken_mod"):
        sm.load_module(broken_dir)


def test_entry_file_using_a_dataclass_with_postponed_annotations_loads(search_root):
    # Module code may use dataclasses with postponed annotations; that needs
    # the imported file registered in sys.modules while it executes.
    dc_dir = search_root / "dc_mod"
    dc_dir.mkdir()
    (dc_dir / "module.toml").write_text(
        GOOD_TOML.replace('"fixture_mod"', '"dc_mod"').replace("FixtureModule", "DcModule"), encoding="utf-8")
    (dc_dir / "module.py").write_text(
        "from __future__ import annotations\nimport dataclasses, typing\n"
        "@dataclasses.dataclass\nclass Note:\n    text: typing.ClassVar[str] = 'x'\n    n: int = 0\n"
        + MODULE_PY.replace("FixtureModule", "DcModule"), encoding="utf-8")
    dc = sm.load_module(dc_dir)
    assert dc.name == "dc_mod"


def test_manifest_name_differing_from_the_requested_name_raises_module_error(search_root):
    # A directory whose manifest declares another name is not silently accepted.
    alias_dir = search_root / "alias_mod"
    alias_dir.mkdir()
    (alias_dir / "module.toml").write_text(GOOD_TOML, encoding="utf-8")
    (alias_dir / "module.py").write_text(MODULE_PY, encoding="utf-8")
    cfg = InstanceConfig(name="demo", root=search_root, timezone="Europe/Berlin", languages=("de",),
                          app_format="x/1", poster_format="y/1", modules=("alias_mod",))
    with pytest.raises(sm.ModuleError, match="alias_mod"):
        sm.load_modules(cfg, [search_root])


def test_core_api_version_is_1():
    assert sm.CORE_API_VERSION == 1


def test_phase_dataclass_holds_titles_by_language():
    phase = sm.Phase(id="intro", titles={"de": "Einstieg", "en": "Intro"})
    assert phase.titles["de"] == "Einstieg"


def test_tool_spec_fn_is_callable():
    tool = sm.ToolSpec(name="echo_count", description="count words", parameters={"type": "object"},
                        fn=lambda text: {"words": len(text.split())})
    assert tool.fn("hello world") == {"words": 2}


def test_core_event_holds_kind_and_payload():
    event = sm.CoreEvent(kind="block", payload={"text": "hi"})
    assert event.kind == "block" and event.payload["text"] == "hi"
