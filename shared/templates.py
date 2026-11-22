# CoThinker — shared/templates.py: template format, blocks + phases + rules
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Template format (ADR 0009): blocks + phases + rules, no code."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from shared.phases import PhaseSpec

TEMPLATE_API_VERSION = 1
CORE_RULES = ("grounding", "add-dont-restructure")
_TOP_KEYS = {"template", "blocks", "phases", "rules"}


class TemplateError(Exception):
    pass


@dataclass(frozen=True)
class BlockRef:
    id: str
    config: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Template:
    name: str
    api_version: int
    surfaces: tuple
    optional_surfaces: tuple
    rules: tuple
    blocks: tuple  # BlockRef
    phases: tuple  # PhaseSpec


def load_template(path: Path) -> Template:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise TemplateError(f"{path}: {e}")
    extra = set(raw) - _TOP_KEYS
    if extra:
        raise TemplateError(f"{path}: unknown keys {sorted(extra)}")
    t = raw.get("template", {})
    if "name" not in t:
        raise TemplateError(f"{path}: [template] name missing")
    if t.get("api_version") != TEMPLATE_API_VERSION:
        raise TemplateError(f"{path}: api_version {t.get('api_version')} != {TEMPLATE_API_VERSION}")
    rules = tuple(t.get("rules", ()))
    missing = [r for r in CORE_RULES if r not in rules]
    if missing:
        raise TemplateError(f"{path}: core rules missing {missing}")
    blocks = []
    for b in raw.get("blocks", []):
        cfg = {k: v for k, v in b.items() if k != "id"}
        blocks.append(BlockRef(b["id"], cfg))
    ids = [b.id for b in blocks]
    if len(ids) != len(set(ids)):
        raise TemplateError(f"{path}: duplicate block ids")
    phases = []
    for p in raw.get("phases", []):
        unknown = [b for b in p.get("blocks", []) if b not in ids]
        if unknown:
            raise TemplateError(f"{path}: phase {p['id']!r} names unknown blocks {unknown}")
        phases.append(PhaseSpec(
            id=p["id"], titles=p.get("titles", {}), blocks=tuple(p.get("blocks", ())),
            surfaces=tuple(p.get("surfaces", ())), goal=p.get("goal", {}),
            enter_when=tuple(p.get("enter_when", ())), exit_when=tuple(p.get("exit_when", ())),
        ))
    if not phases:
        raise TemplateError(f"{path}: no phases")
    return Template(
        name=t["name"], api_version=t["api_version"], surfaces=tuple(t.get("surfaces", ())),
        optional_surfaces=tuple(t.get("optional_surfaces", ())), rules=rules,
        blocks=tuple(blocks), phases=tuple(phases),
    )


def merged_block_config(template: Template, instance) -> dict:
    out = {}
    for b in template.blocks:
        cfg = dict(b.config)
        if instance is not None:
            cfg.update(instance.block_config(b.id))
        out[b.id] = cfg
    return out
