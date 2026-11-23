# CoThinker — design reference-image gate: pixel-diff engine (issue #61)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure PNG pixel-diff engine for the design reference-image gate: a
deterministic PASS/FAIL per reference image.

    reference_diff.diff_images(reference_png, live_png,
                               max_diff_pixel_ratio=0.01, aa_tolerance=32,
                               masks=[...], diff_image_path=...)

`aa_tolerance` is a per-pixel colour-distance tolerance (sum of the three
channel deltas, max 765) that absorbs anti-aliasing noise on otherwise
identical pixels -- below it a pixel never counts as a diff, no matter how
small the maxDiffPixelRatio is set. `masks` are manifest-declared
rectangles (dynamic regions) excluded from both the diff count and the
pixel total -- declared in a surface's reference/manifest.toml, never in
code.

Each surface under surfaces/<name>/ declares its valid state names in its
own states.toml; surfaces/<name>/reference/manifest.toml binds a reference
PNG to one of those declared states directly. load_manifest() raises
UnknownState the moment an entry's state isn't declared -- a reference
naming an undeclared state must fail loading, never pass silently. A
declared state with no manifest entry is design debt, reported by
design_debt_report() below.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PIL import Image

DEFAULT_MAX_DIFF_PIXEL_RATIO = 0.01
DEFAULT_AA_TOLERANCE = 32
DIFF_OVERLAY_COLOUR = (255, 0, 0)


class SizeMismatch(ValueError):
    """Raised when a reference and its live counterpart are not the same size --
    a FAIL in its own right (pixel-for-pixel), never a silent resize."""


class UnknownState(ValueError):
    """Raised when a manifest entry's state is not declared in the surface's
    own states.toml -- a reference naming an undeclared state must fail the
    suite, it never passes silently."""


@dataclass(frozen=True)
class MaskBox:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class ReferenceEntry:
    id: str
    state: str
    device: str
    reference: Path
    design_source_commit: str
    masks: tuple[MaskBox, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class DiffResult:
    width: int
    height: int
    compared_pixels: int
    diff_pixels: int
    ratio: float
    passed: bool
    diff_image_path: Optional[Path] = None


def load_states(path: Path) -> frozenset[str]:
    """A surface's declared state names, from its own states.toml."""
    doc = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return frozenset(doc.get("states", []))


def _states_path_for_manifest(manifest_path: Path) -> Path:
    # surfaces/<name>/reference/manifest.toml -> surfaces/<name>/states.toml
    return manifest_path.resolve().parent.parent / "states.toml"


def load_manifest(path: Path, *, states: Optional[frozenset[str]] = None) -> list[ReferenceEntry]:
    path = Path(path)
    if states is None:
        states = load_states(_states_path_for_manifest(path))
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    entries: list[ReferenceEntry] = []
    for row in doc.get("reference", []):
        state = row["state"]
        if state not in states:
            raise UnknownState(
                f"{row.get('id', state)!r}: state {state!r} is not declared in "
                "this surface's states.toml (a reference without a declared "
                "state must fail, never pass silently)")
        masks = tuple(MaskBox(x=m["x"], y=m["y"], w=m["w"], h=m["h"]) for m in row.get("masks", []))
        entries.append(ReferenceEntry(
            id=row["id"], state=state, device=row["device"], reference=Path(row["reference"]),
            design_source_commit=row["design_source_commit"], masks=masks, note=row.get("note", "")))
    return entries


def _mask_array(shape: tuple[int, int], masks: Sequence[MaskBox]) -> np.ndarray:
    h, w = shape
    excluded = np.zeros((h, w), dtype=bool)
    for m in masks:
        x0, y0 = max(0, m.x), max(0, m.y)
        x1, y1 = min(w, m.x + m.w), min(h, m.y + m.h)
        if x1 > x0 and y1 > y0:
            excluded[y0:y1, x0:x1] = True
    return excluded


def diff_images(reference_path: Path, live_path: Path, *,
                max_diff_pixel_ratio: float = DEFAULT_MAX_DIFF_PIXEL_RATIO,
                aa_tolerance: int = DEFAULT_AA_TOLERANCE,
                masks: Sequence[MaskBox] = (),
                diff_image_path: Optional[Path] = None) -> DiffResult:
    ref = Image.open(reference_path).convert("RGB")
    live = Image.open(live_path).convert("RGB")
    if ref.size != live.size:
        raise SizeMismatch(f"reference {ref.size} != live {live.size} ({reference_path} vs {live_path})")

    ref_arr = np.asarray(ref, dtype=np.int16)
    live_arr = np.asarray(live, dtype=np.int16)
    delta = np.abs(ref_arr - live_arr).sum(axis=2)
    diff_mask = delta > aa_tolerance

    excluded = _mask_array(diff_mask.shape, masks)
    diff_mask = diff_mask & ~excluded
    compared_pixels = diff_mask.size - int(excluded.sum())
    diff_pixels = int(diff_mask.sum())
    ratio = (diff_pixels / compared_pixels) if compared_pixels else 0.0
    passed = ratio <= max_diff_pixel_ratio

    written = None
    if diff_image_path is not None:
        overlay = np.array(live, dtype=np.uint8)
        overlay[diff_mask] = DIFF_OVERLAY_COLOUR
        diff_image_path = Path(diff_image_path)
        diff_image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(overlay, "RGB").save(diff_image_path)
        written = diff_image_path

    return DiffResult(width=ref.width, height=ref.height, compared_pixels=compared_pixels,
                      diff_pixels=diff_pixels, ratio=ratio, passed=passed, diff_image_path=written)


def design_debt_report(entries: list[ReferenceEntry], *, states: frozenset[str]) -> dict:
    """A declared state without a manifest entry is design debt -- all_states
    is the surface's own states.toml, never a separately invented list."""
    covered = {e.state for e in entries}
    without = sorted(states - covered)
    with_state = sorted(covered & states)
    return {"with_reference": len(with_state), "without_reference": len(without),
            "without_reference_list": without}
