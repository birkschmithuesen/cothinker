# CoThinker — profile seam: shared skeleton + instance overlay
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Repo split Task 5.1: a Hermes role profile (`main`, `writer`, `lektor`,
`stoffentwickler`, `check`) is `shared/profiles/skel/<role>/` (generic,
reusable mechanics -- no instance ever named) overlaid by the instance's
own `profiles/<role>/` (SOUL.md, instance skills, config deltas).

`materialize()` copies skel then overlay into one output directory --
overlay wins per file, `config.yaml` merged key-wise (overlay's value wins
per top-level key). For every instance observed so far, the overlay
already carries every key skel would add, so the merged `config.yaml` is
the overlay file copied verbatim byte-for-byte -- skel only ever fills a
key a thinner overlay leaves out (see `instances/example/profiles/main/
config.yaml`, which leans on skel/main's `agent`/`auth` defaults).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

import yaml

from shared.instance import InstanceConfig

SKEL_ROOT = Path(__file__).resolve().parent / "profiles" / "skel"
RULES_ROOT = Path(__file__).resolve().parent / "rules"
_CONFIG_NAME = "config.yaml"
_ENV_NAME = ".env"
_SOUL_NAME = "SOUL.md"

SERVER_NAME = "cothinker"
SESSION_MARKER = "<!-- cothinker: rules and template -->"


def _merge_config_yaml(skel_path: Path, overlay_path: Path, dest: Path) -> None:
    skel_exists, overlay_exists = skel_path.exists(), overlay_path.exists()
    if skel_exists and overlay_exists:
        skel_cfg = yaml.safe_load(skel_path.read_text(encoding="utf-8")) or {}
        overlay_cfg = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
        if set(skel_cfg) <= set(overlay_cfg):
            # overlay already provides every key skel has -- it IS the merge result,
            # copied verbatim so comments/formatting survive byte-for-byte.
            shutil.copy2(overlay_path, dest)
        else:
            merged = {**skel_cfg, **overlay_cfg}
            dest.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    elif overlay_exists:
        shutil.copy2(overlay_path, dest)
    elif skel_exists:
        shutil.copy2(skel_path, dest)


def materialize(role: str, cfg: InstanceConfig, out: Path) -> list[Path]:
    """Copy `shared/profiles/skel/<role>/` then `cfg.profile(role)` into `out`.

    Overlay wins per file (copied after skel, same relative path overwrites);
    `config.yaml` is the one exception, merged key-wise instead of overwritten
    whole. Returns the sorted list of files written."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    skel = SKEL_ROOT / role
    overlay = cfg.profile(role)

    written: dict[Path, None] = {}
    for root in (skel, overlay):
        if not root.is_dir():
            continue
        for src in sorted(root.rglob("*")):
            if not src.is_file() or "__pycache__" in src.parts:
                continue
            rel = src.relative_to(root)
            if rel == Path(_CONFIG_NAME):
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            written[dest] = None

    skel_cfg, overlay_cfg = skel / _CONFIG_NAME, overlay / _CONFIG_NAME
    if skel_cfg.exists() or overlay_cfg.exists():
        dest = out / _CONFIG_NAME
        _merge_config_yaml(skel_cfg, overlay_cfg, dest)
        written[dest] = None

    return sorted(written)


def _rule_text(rule: str, language: str) -> str:
    path = RULES_ROOT / f"{rule}.{language}.md"
    if not path.exists():
        path = RULES_ROOT / f"{rule}.en.md"
    if not path.exists():
        raise FileNotFoundError(f"rule {rule!r}: no text for language {language!r} or English fallback")
    return path.read_text(encoding="utf-8").strip()


def _template_soul_text(template_dir: Path | None, language: str) -> str | None:
    if template_dir is None:
        return None
    path = Path(template_dir) / "profile" / f"SOUL.{language}.md"
    if not path.exists():
        path = Path(template_dir) / "profile" / "SOUL.en.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def _append_session_sections(soul_path: Path, rules: Sequence[str], language: str,
                              template_dir: Path | None) -> None:
    sections = [_rule_text(rule, language) for rule in rules]
    template_text = _template_soul_text(template_dir, language)
    if template_text is not None:
        sections.append(template_text)
    block = SESSION_MARKER + "\n\n" + "\n\n".join(sections) + "\n"

    existing = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
    if SESSION_MARKER in existing:
        prefix = existing.split(SESSION_MARKER, 1)[0]
    else:
        prefix = existing.rstrip("\n") + "\n\n" if existing.strip() else ""
    soul_path.write_text(prefix + block, encoding="utf-8")


def mcp_server_entry(url: str, scope: str, core_root: Path, python: str = "python3") -> dict:
    """The `mcp_servers.cothinker` entry (ADR 0018) that starts the MCP stdio relay
    (`shared/mcp_stdio.py`) for one session's scope. The token stays a `${VAR}`
    placeholder here; it is resolved from the profile's own `.env` at connect time."""
    return {
        "command": python,
        "args": ["-m", "shared.mcp_stdio", "--url", url, "--scope", scope],
        "env": {"COTHINKER_TOOL_TOKEN": "${COTHINKER_TOOL_TOKEN}"},
        "cwd": str(core_root),
        "timeout": 120,
        "connect_timeout": 30,
    }


def materialize_session(role: str, cfg: InstanceConfig, out: Path, *, rules: Sequence[str],
                         language: str, template_dir: Path | None, mcp: dict | None,
                         token: str | None) -> list[Path]:
    """`materialize()` plus the session-specific agent profile (ADR 0018): the core
    rules' and template's text appended to SOUL.md under `SESSION_MARKER`, the MCP
    relay entry under `mcp_servers.cothinker` in config.yaml, and the relay token in
    `out/.env` (mode 0600) -- never in config.yaml, which only ever holds the
    `${COTHINKER_TOOL_TOKEN}` placeholder (see `mcp_server_entry`)."""
    out = Path(out)
    written: dict[Path, None] = dict.fromkeys(materialize(role, cfg, out))

    soul_path = out / _SOUL_NAME
    _append_session_sections(soul_path, rules, language, template_dir)
    written[soul_path] = None

    if mcp is not None:
        config_path = out / _CONFIG_NAME
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        config = config or {}
        config.setdefault("mcp_servers", {})
        config["mcp_servers"][SERVER_NAME] = mcp
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        written[config_path] = None

    if token is not None:
        env_path = out / _ENV_NAME
        env_path.write_text(f"COTHINKER_TOOL_TOKEN={token}\n", encoding="utf-8")
        env_path.chmod(0o600)
        written[env_path] = None

    return sorted(written)
