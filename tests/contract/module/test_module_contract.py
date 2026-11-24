"""Module API contract (ADR 0022 level 2; format: docs/module-format.md, today's
`api_version = 1`; target: docs/architecture/target.md §2.4).

A subject is a module directory (the one holding `module.toml`). Every
assertion message starts with a rule id (MOD-... or STATE-...), so a red run
names the rule that broke. Outside modules: `COTHINKER_CONTRACT_MODULE=
<path to the module directory>` (docs/contract-tests.md).

Not here yet: `provides` and `api_version = 2` (target.md §2.4) -- they follow
with Module API v2; this contract tests the format as it is declared today.
"""
from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from shared.instance import load as load_instance
from shared.modules import (CORE_API_VERSION, CoreEvent, ModuleContext, ModuleError, StateStore,
                            load_module)

CONTRACT_API = "module"
REPO = Path(__file__).resolve().parents[3]
BUILTIN_SUBJECTS = {"example_echo": REPO / "modules" / "example_echo"}
EVENT_KINDS = ("utterance", "block", "phase", "cue")


def resolve_outside(value: str) -> Path:
    return Path(value)


def _manifest(module_dir: Path) -> dict:
    return tomllib.loads((module_dir / "module.toml").read_text(encoding="utf-8"))


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


# ---------------------------------------------------------------- contract tests
def test_the_manifest_declares_the_required_keys(subject, tmp_path):
    m = _manifest(subject).get("module", {})
    assert isinstance(m.get("name"), str) and m["name"], "MOD-MANIFEST: [module].name missing"
    assert isinstance(m.get("api_version"), int), "MOD-MANIFEST: [module].api_version not an int"
    assert re.fullmatch(r"[A-Za-z_][\w]*:[A-Za-z_]\w*", str(m.get("entry", ""))), (
        "MOD-MANIFEST: [module].entry must be 'file:Class'")
    files = _manifest(subject).get("state", {}).get("files", [])
    assert all(isinstance(f, str) and f and "/" not in f and f not in (".", "..")
               for f in files), f"MOD-MANIFEST: [state].files must be plain file names: {files}"


def test_it_loads_with_the_core_api_version(subject, tmp_path):
    mod = load_module(subject)
    assert mod.name == _manifest(subject)["module"]["name"], "MOD-LOAD: name differs from manifest"
    assert mod.api_version == CORE_API_VERSION, "MOD-LOAD: api_version differs from the core's"


def test_a_copy_with_another_api_version_is_refused_at_load(subject, tmp_path):
    copy = tmp_path / subject.name
    shutil.copytree(subject, copy, ignore=shutil.ignore_patterns("__pycache__"))
    text = (copy / "module.toml").read_text(encoding="utf-8")
    text, n = re.subn(r"(?m)^api_version\s*=.*$", f"api_version = {CORE_API_VERSION + 1}", text)
    assert n == 1, "MOD-MANIFEST: exactly one api_version line expected"
    (copy / "module.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ModuleError, match="api_version"):
        load_module(copy)
        raise AssertionError("MOD-API-VERSION: a mismatching api_version was loaded")


def test_its_tools_are_valid_tool_specs(subject, tmp_path):
    tools = load_module(subject).tools()
    assert isinstance(tools, list), "MOD-TOOLS: tools() must return a list"
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), f"MOD-TOOLS: duplicate tool names {names}"
    for t in tools:
        assert isinstance(t.name, str) and t.name, "MOD-TOOLS: a tool without a name"
        assert isinstance(t.description, str) and t.description, f"MOD-TOOLS: {t.name} has no description"
        assert callable(t.fn), f"MOD-TOOLS: {t.name}.fn is not callable"
        try:
            Draft202012Validator.check_schema(t.parameters)
        except SchemaError as e:
            raise AssertionError(f"MOD-TOOLS: {t.name}.parameters is no JSON schema: {e.message}")


def test_its_phases_have_an_id_and_titles(subject, tmp_path):
    phases = load_module(subject).phases()
    assert isinstance(phases, list), "MOD-PHASES: phases() must return a list"
    for p in phases:
        assert isinstance(p.id, str) and p.id, "MOD-PHASES: a phase without an id"
        assert p.titles and all(isinstance(k, str) and isinstance(v, str) and v
                                for k, v in p.titles.items()), f"MOD-PHASES: {p.id} has no titles"


def test_its_profile_dir_exists(subject, tmp_path):
    profile = _manifest(subject)["module"].get("profile", "profile")
    got = load_module(subject).profile_dir()
    assert Path(got) == subject / profile, f"MOD-PROFILE: profile_dir() is {got}, not {subject / profile}"
    assert Path(got).is_dir(), f"MOD-PROFILE: profile directory {got} missing"


def test_it_writes_only_its_declared_state_files_into_its_own_root(subject, tmp_path):
    declared = set(_manifest(subject).get("state", {}).get("files", []))
    before = _files(subject)
    mod = load_module(subject)
    instance = load_instance(REPO / "instances" / "example")
    own = tmp_path / "own"
    ctx = ModuleContext(instance=instance, state=StateStore(own))
    for kind in EVENT_KINDS:
        mod.on_event(CoreEvent(kind=kind, payload={"text": "eins zwei drei"}), ctx)
    written = {name for name in _files(own) if not name.startswith(".")}
    assert written <= declared, f"MOD-STATE: wrote {sorted(written - declared)}, not in [state].files"
    outside = [name for name in _files(tmp_path) if not name.startswith("own/")]
    assert outside == [], f"MOD-ISOLATION: wrote outside its own state root: {outside}"
    assert _files(subject) == before, "MOD-ISOLATION: wrote into its own module directory"


SUBJECT_TESTS = [
    test_the_manifest_declares_the_required_keys,
    test_it_loads_with_the_core_api_version,
    test_a_copy_with_another_api_version_is_refused_at_load,
    test_its_tools_are_valid_tool_specs,
    test_its_phases_have_an_id_and_titles,
    test_its_profile_dir_exists,
    test_it_writes_only_its_declared_state_files_into_its_own_root,
]


# ------------------------------------------- the StateStore the core hands out
def test_state_stores_on_two_roots_never_see_each_others_files(tmp_path):
    a, b = StateStore(tmp_path / "a"), StateStore(tmp_path / "b")
    a.write("x.json", {"v": 1})
    assert b.read("x.json") == {}, "STATE-ISOLATION: a second root sees the first one's file"
    assert a.read("x.json") == {"v": 1}, "STATE-ISOLATION: a root lost its own file"


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    store.write("x.json", {"v": 1})

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(RuntimeError):
        store.write("x.json", {"v": 2})
    monkeypatch.undo()
    assert store.read("x.json") == {"v": 1}, "STATE-ATOMIC: a failed write damaged the old file"
    assert _files(tmp_path) == ["x.json"], f"STATE-ATOMIC: left {_files(tmp_path)}"


# ------------------------------------------------- red proof: the broken fake
BROKEN_TOML = """\
[module]
name = "broken_no_profile"
version = "0.0.1"
api_version = {api}
entry = "module:NoProfileModule"
"""

BROKEN_PY = """\
class NoProfileModule:
    \"\"\"Breaks exactly one rule, MOD-PROFILE: it ships no profile directory.\"\"\"

    def __init__(self, *, name, api_version, module_dir, profile_dir):
        self.name, self.api_version, self._profile_dir = name, api_version, profile_dir

    def tools(self):
        return []

    def phases(self):
        return []

    def profile_dir(self):
        return self._profile_dir

    def on_event(self, ev, ctx):
        pass
"""


def _failed_rules(module_dir: Path, tmp_path: Path) -> set[str]:
    """Runs every subject test against `module_dir`; returns the rule ids that failed."""
    failed = set()
    for i, test in enumerate(SUBJECT_TESTS):
        run = tmp_path / f"run{i}"
        run.mkdir()
        try:
            test(module_dir, run)
        except AssertionError as e:
            failed.add(str(e).split(":", 1)[0])
    return failed


def test_the_broken_fake_fails_the_contract_only_for_the_profile_rule(tmp_path):
    broken = tmp_path / "broken_no_profile"
    broken.mkdir()
    (broken / "module.toml").write_text(BROKEN_TOML.format(api=CORE_API_VERSION), encoding="utf-8")
    (broken / "module.py").write_text(BROKEN_PY, encoding="utf-8")
    assert _failed_rules(broken, tmp_path) == {"MOD-PROFILE"}


# ------------------- one violating module per state rule (same subject tests)
VIOLATION_TOML = """\
[module]
name = "{name}"
version = "0.0.1"
api_version = {api}
entry = "module:ViolatingModule"
tests = "tests"

[state]
files = ["declared.json"]
"""

VIOLATION_PY = """\
from pathlib import Path

from shared.modules import StateStore


class ViolatingModule:
    def __init__(self, *, name, api_version, module_dir, profile_dir):
        self.name, self.api_version = name, api_version
        self._module_dir, self._profile_dir = Path(module_dir), profile_dir

    def tools(self):
        return []

    def phases(self):
        return []

    def profile_dir(self):
        return self._profile_dir

    def on_event(self, ev, ctx):
{on_event}
"""

MODULE_VIOLATIONS = {
    "undeclared-file-on-the-last-event": (
        "MOD-STATE",
        '        if ev.kind == "cue":\n'
        '            ctx.state.write("undeclared.json", {})'),
    "write-into-its-own-directory": (
        "MOD-ISOLATION",
        '        (self._module_dir / "leak.json").write_text("{}", encoding="utf-8")'),
    "write-into-a-sibling-state-root": (
        "MOD-ISOLATION",
        '        StateStore(ctx.state.root.parent / "sibling").write("declared.json", {})'),
}


@pytest.mark.parametrize("case", sorted(MODULE_VIOLATIONS))
def test_every_state_rule_rejects_its_violating_module(case, tmp_path):
    rule, on_event = MODULE_VIOLATIONS[case]
    name = case.replace("-", "_")
    module_dir = tmp_path / name
    (module_dir / "profile").mkdir(parents=True)
    (module_dir / "tests").mkdir()
    (module_dir / "tests" / f"test_{name}.py").write_text("", encoding="utf-8")
    (module_dir / "module.toml").write_text(
        VIOLATION_TOML.format(name=name, api=CORE_API_VERSION), encoding="utf-8")
    (module_dir / "module.py").write_text(VIOLATION_PY.format(on_event=on_event), encoding="utf-8")
    assert _failed_rules(module_dir, tmp_path) == {rule}
