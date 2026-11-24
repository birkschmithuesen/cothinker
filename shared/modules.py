# CoThinker — module seam: dataclasses, StateStore, load_module/load_modules
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Module seam (spec 2026-11-12-repo-split-design.md, plan Task 5.2).

A module is a directory with a `module.toml` manifest and a Python entry
point. `load_module` reads the manifest and imports the entry point;
`load_modules` resolves the names an instance lists (`InstanceConfig.modules`)
against a search path of module directories. See `docs/module-format.md`
for the manifest format and the contract an entry point class must meet.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from shared.instance import InstanceConfig

CORE_API_VERSION = 1


class ModuleError(Exception):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict           # JSON schema
    fn: Callable[..., dict]
    read_only: bool = False


@dataclass(frozen=True)
class Phase:
    id: str
    titles: Mapping[str, str]  # language -> title


@dataclass(frozen=True)
class CoreEvent:
    kind: str                  # "utterance" | "block" | "phase" | "cue"
    payload: Mapping[str, object]


class StateStore:
    """Reads and writes a module's own `<root>/<name>` JSON files.

    `write` is atomic: it writes to a temp file in `root` and `os.replace`s
    it onto the target, so a reader never observes a partial file and a
    crash never leaves a stray temp file next to it.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, name: str) -> dict:
        p = self.root / name
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def write(self, name: str, data: dict) -> None:
        fd, tmp_path = tempfile.mkstemp(dir=self.root, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.root / name)
        except Exception:
            os.unlink(tmp_path)
            raise

    def update(self, name: str, fn: "Callable[[dict], dict]") -> dict:
        """Read-modify-write under an exclusive lock on `<root>/.<name>.lock`.
        Closes gap G7 of the InScribe port draft (no lost update when two
        threads change the same file)."""
        lock_path = self.root / f".{name}.lock"
        with open(lock_path, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = fn(self.read(name))
                self.write(name, data)
                return data
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


@dataclass
class ModuleContext:
    instance: InstanceConfig
    state: StateStore
    agent: "shared.agent.AgentPort | None" = field(default=None)


class Module(Protocol):
    name: str
    api_version: int

    def tools(self) -> list[ToolSpec]: ...

    def phases(self) -> list[Phase]: ...

    def profile_dir(self) -> Path: ...

    def on_event(self, ev: CoreEvent, ctx: ModuleContext) -> None: ...


def load_module(path: Path) -> Module:
    """Reads `<path>/module.toml` and imports and instantiates its entry point."""
    path = Path(path)
    toml_path = path / "module.toml"
    try:
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ModuleError(f"{path}: module.toml missing")
    except tomllib.TOMLDecodeError as e:
        raise ModuleError(f"{path}: module.toml unreadable: {e}")
    try:
        m = raw["module"]
        name = m["name"]
        api_version = m["api_version"]
        entry = m["entry"]
    except KeyError as e:
        raise ModuleError(f"{path}: module.toml missing key {e}")
    profile = m.get("profile", "profile")
    tests = m.get("tests")
    if tests is not None and not (path / tests).is_dir():
        raise ModuleError(f"module {name!r} at {path}: declared tests dir {tests!r} missing")
    if api_version != CORE_API_VERSION:
        raise ModuleError(
            f"module {name!r} at {path}: api_version {api_version} != supported {CORE_API_VERSION}")
    try:
        module_file, class_name = entry.split(":")
    except ValueError:
        raise ModuleError(f"module {name!r} at {path}: entry {entry!r} must be 'module:Class'")
    module_path = path / f"{module_file}.py"
    if not module_path.exists():
        raise ModuleError(f"module {name!r} at {path}: entry file {module_path} missing")
    import_name = f"cothinker_module_{name}"
    spec = importlib.util.spec_from_file_location(import_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    # Registered before executing: dataclasses (and anything else resolving
    # cls.__module__) look the module up in sys.modules while it runs.
    sys.modules[import_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        sys.modules.pop(import_name, None)
        raise ModuleError(f"module {name!r} at {path}: importing {module_path} failed: {e!r}") from e
    try:
        cls = getattr(mod, class_name)
    except AttributeError:
        raise ModuleError(f"module {name!r} at {path}: entry class {class_name!r} not found in {module_path}")
    return cls(name=name, api_version=api_version, module_dir=path, profile_dir=path / profile)


def load_modules(cfg: InstanceConfig, search: Sequence[Path]) -> list[Module]:
    """Loads every module `cfg.modules` names, found in the first `search` dir that has it."""
    loaded = []
    for name in cfg.modules:
        found = None
        for base in search:
            candidate = Path(base) / name
            if (candidate / "module.toml").exists():
                found = candidate
                break
        if found is None:
            raise ModuleError(
                f"instance {cfg.name!r} lists unknown module {name!r} (not found in {list(search)})")
        mod = load_module(found)
        if mod.name != name:
            raise ModuleError(
                f"instance {cfg.name!r} requests module {name!r}, but {found}/module.toml declares {mod.name!r}")
        loaded.append(mod)
    return loaded
