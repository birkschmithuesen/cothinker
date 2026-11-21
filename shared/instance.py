# CoThinker — instance loader: one directory per instance, read through one module
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Instance API (docs/architecture/target.md §2.5, ADR 0001).

Generic code never names an instance. It asks `load()` (or `for_repo()` in
code that already takes a `repo` argument) for an `InstanceConfig`.

Resolution: explicit path > COTHINKER_INSTANCE_DIR > InstanceError.
No instance means no start; there is no default instance.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

ENV_DIR = "COTHINKER_INSTANCE_DIR"
REQUIRED = ("name", "timezone", "languages", "app_format", "poster_format")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class InstanceError(Exception):
    pass


@dataclass(frozen=True)
class InstanceConfig:
    name: str
    root: Path
    timezone: str
    languages: tuple[str, ...]
    app_format: str
    poster_format: str
    expires: date | None = None
    secret_env: Mapping[str, str] = field(default_factory=dict)
    hosts: Mapping[str, str] = field(default_factory=dict)
    modules: tuple[str, ...] = ()

    def _existing(self, p: Path, what: str) -> Path:
        if not p.exists():
            raise InstanceError(f"instance {self.name}: {what} missing")
        return p

    def content(self, rel: str) -> Path:
        return self._existing(self.root / "content" / rel, f"content {rel}")

    def profile(self, role: str) -> Path:
        return self._existing(self.root / "profiles" / role, f"profile {role}")

    def acceptance(self, rel: str) -> Path:
        return self._existing(self.root / "acceptance" / rel, f"acceptance {rel}")


def validate(path: Path) -> list[str]:
    try:
        raw = tomllib.loads((Path(path) / "instance.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ["instance.toml missing"]
    except tomllib.TOMLDecodeError as e:
        return [f"instance.toml unreadable: {e}"]
    errs = [f"[instance] {k} missing" for k in REQUIRED if k not in raw.get("instance", {})]
    errs += [f"[secrets] {k} must name an env variable, not hold a value"
             for k, v in raw.get("secrets", {}).items() if not ENV_NAME.match(str(v))]
    return errs


def _from_dir(path: Path) -> InstanceConfig:
    path = Path(path)
    errs = validate(path)
    if errs:
        raise InstanceError(f"{path}: " + "; ".join(errs))
    raw = tomllib.loads((path / "instance.toml").read_text(encoding="utf-8"))
    i = raw["instance"]
    return InstanceConfig(
        name=i["name"], root=path, timezone=i["timezone"], languages=tuple(i["languages"]),
        app_format=i["app_format"], poster_format=i["poster_format"], expires=i.get("expires"),
        secret_env=dict(raw.get("secrets", {})), hosts=dict(raw.get("hosts", {})),
        modules=tuple(raw.get("modules", {}).get("enabled", [])),
    )


def load(path: Path | str | None = None) -> InstanceConfig:
    if path is not None:
        return _from_dir(Path(path))
    env = os.environ.get(ENV_DIR)
    if env:
        return _from_dir(Path(env))
    raise InstanceError(f"no instance: pass --instance DIR or set {ENV_DIR}")


def for_repo(repo: Path | None) -> InstanceConfig:
    """Kept for callers that pass a repo root. The repo never selects an
    instance: resolution is the same as `load()`."""
    return load()


def env_view(cfg: InstanceConfig) -> dict[str, str]:
    out = {"COTHINKER_INSTANCE_NAME": cfg.name, "COTHINKER_INSTANCE_DIR": str(cfg.root),
           "COTHINKER_TZ": cfg.timezone, "COTHINKER_LANGUAGES": " ".join(cfg.languages)}
    out.update({f"COTHINKER_HOST_{k.upper()}": v for k, v in cfg.hosts.items()})
    return out


def getenv(name: str, default: str | None = None) -> str | None:
    """COTHINKER_<name>."""
    return os.environ.get(f"COTHINKER_{name}", default)


if __name__ == "__main__":  # python3 -m shared.instance env|check <dir>
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "env":
        for k, v in env_view(load(sys.argv[2])).items():
            print(f"{k}={v}")
    elif len(sys.argv) == 3 and sys.argv[1] == "check":
        errors = validate(Path(sys.argv[2]))
        print("\n".join(errors) or "ok")
        sys.exit(1 if errors else 0)
    else:
        print("usage: python3 -m shared.instance env|check <instance-dir>")
        sys.exit(2)
