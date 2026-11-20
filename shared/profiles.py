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

import yaml

from shared.instance import InstanceConfig

SKEL_ROOT = Path(__file__).resolve().parent / "profiles" / "skel"
_CONFIG_NAME = "config.yaml"


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
