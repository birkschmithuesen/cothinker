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
_TOP_KEYS = {"template", "blocks", "phases", "rules", "formats"}


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
    format: str | None = None
    formats: tuple = ()


def _entry_in_format(path: Path, kind: str, entry_id, entry_format: str | None,
                      format_ids: tuple, format: str | None) -> bool:
    if entry_format is not None and not format_ids:
        raise TemplateError(
            f"{path}: {kind} {entry_id!r} names format {entry_format!r} "
            f"but template has no [formats]"
        )
    if not format_ids:
        return True
    if kind == "phase" and entry_format is None:
        raise TemplateError(
            f"{path}: phase {entry_id!r} must name a format (template has formats {list(format_ids)})"
        )
    if entry_format is not None and entry_format not in format_ids:
        raise TemplateError(f"{path}: {kind} {entry_id!r} names unknown format {entry_format!r}")
    if entry_format is None:
        return True
    return entry_format == format


def load_template(path: Path, format: str | None = None) -> Template:
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

    formats_raw = raw.get("formats", {})
    format_ids = tuple(formats_raw)
    if format_ids and format is None:
        raise TemplateError(
            f"{path}: template has formats {list(format_ids)}; name one"
        )
    if format is not None and format not in format_ids:
        raise TemplateError(f"{path}: format {format!r} is unknown; known formats {list(format_ids)}")

    blocks = []
    for b in raw.get("blocks", []):
        b_format = b.get("format")
        if not _entry_in_format(path, "block", b["id"], b_format, format_ids, format):
            continue
        cfg = {k: v for k, v in b.items() if k not in ("id", "format")}
        blocks.append(BlockRef(b["id"], cfg))
    ids = [b.id for b in blocks]
    if len(ids) != len(set(ids)):
        raise TemplateError(f"{path}: duplicate block ids")

    phases = []
    for p in raw.get("phases", []):
        p_format = p.get("format")
        if not _entry_in_format(path, "phase", p["id"], p_format, format_ids, format):
            continue
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

    if format is not None:
        fmt = formats_raw[format]
        surfaces = tuple(fmt.get("surfaces", ()))
        optional_surfaces = tuple(fmt.get("optional_surfaces", ()))
    else:
        surfaces = tuple(t.get("surfaces", ()))
        optional_surfaces = tuple(t.get("optional_surfaces", ()))

    return Template(
        name=t["name"], api_version=t["api_version"], surfaces=surfaces,
        optional_surfaces=optional_surfaces, rules=rules,
        blocks=tuple(blocks), phases=tuple(phases),
        format=format, formats=format_ids,
    )


def merged_block_config(template: Template, instance) -> dict:
    out = {}
    for b in template.blocks:
        cfg = dict(b.config)
        if instance is not None:
            cfg.update(instance.block_config(b.id))
        out[b.id] = cfg
    return out
