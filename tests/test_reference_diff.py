# CoThinker — design reference-image gate: pixel-diff engine (issue #61)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_reference_diff.py"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools.design_gate import reference_diff


def _save(path: Path, size: tuple[int, int], fill: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, fill).save(path)
    return path


def _surface(tmp_path: Path, name: str, states: list[str]) -> Path:
    surface_dir = tmp_path / "surfaces" / name
    (surface_dir / "reference").mkdir(parents=True)
    states_str = ", ".join(f'"{s}"' for s in states)
    (surface_dir / "states.toml").write_text(f"states = [{states_str}]\n", encoding="utf-8")
    return surface_dir


def _write_manifest(path: Path, rows: list[dict]) -> None:
    lines = []
    for row in rows:
        lines.append("[[reference]]")
        for key, val in row.items():
            if key == "masks":
                lines.append("masks = []")
            else:
                lines.append(f'{key} = "{val}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_identical_images_pass(tmp_path):
    ref = _save(tmp_path / "ref1.png", (40, 20), (10, 20, 30))
    live = _save(tmp_path / "live1.png", (40, 20), (10, 20, 30))
    result = reference_diff.diff_images(ref, live)
    assert result.ratio == 0.0
    assert result.passed is True
    assert result.diff_pixels == 0
    assert result.compared_pixels == 40 * 20


def test_aa_tolerance_absorbs_small_noise(tmp_path):
    ref = _save(tmp_path / "ref2.png", (40, 20), (100, 100, 100))
    live = _save(tmp_path / "live2.png", (40, 20), (105, 102, 101))
    result = reference_diff.diff_images(ref, live, aa_tolerance=32)
    assert result.diff_pixels == 0
    assert result.passed is True


def test_big_block_change_fails_and_writes_diff_image(tmp_path):
    ref = Image.new("RGB", (100, 10), (0, 0, 0))
    live = ref.copy()
    for x in range(20):
        for y in range(10):
            live.putpixel((x, y), (255, 255, 255))
    ref_path, live_path = tmp_path / "ref3.png", tmp_path / "live3.png"
    ref.save(ref_path)
    live.save(live_path)
    diff_path = tmp_path / "diff3.png"
    result = reference_diff.diff_images(ref_path, live_path, max_diff_pixel_ratio=0.01,
                                        diff_image_path=diff_path)
    assert result.ratio > 0.01
    assert result.passed is False
    assert result.diff_pixels == 20 * 10
    assert diff_path.is_file()
    diff_img = Image.open(diff_path).convert("RGB")
    assert diff_img.getpixel((5, 5)) == (255, 0, 0)
    assert diff_img.getpixel((50, 5)) == (0, 0, 0)


def test_masked_region_is_excluded(tmp_path):
    ref = Image.new("RGB", (50, 10), (0, 0, 0))
    live = ref.copy()
    for x in range(10):
        for y in range(10):
            live.putpixel((x, y), (255, 255, 255))
    ref_path, live_path = tmp_path / "ref4.png", tmp_path / "live4.png"
    ref.save(ref_path)
    live.save(live_path)
    mask = reference_diff.MaskBox(x=0, y=0, w=10, h=10)
    result = reference_diff.diff_images(ref_path, live_path, masks=[mask])
    assert result.diff_pixels == 0
    assert result.compared_pixels == 50 * 10 - 10 * 10
    assert result.passed is True


def test_size_mismatch_raises(tmp_path):
    ref = _save(tmp_path / "ref5.png", (40, 20), (1, 1, 1))
    live = _save(tmp_path / "live5.png", (41, 20), (1, 1, 1))
    with pytest.raises(reference_diff.SizeMismatch):
        reference_diff.diff_images(ref, live)


def test_manifest_entry_naming_an_undeclared_state_fails_loading(tmp_path):
    surface_dir = _surface(tmp_path, "widget", ["default"])
    ref_png = _save(surface_dir / "reference" / "ghost.png", (10, 10), (1, 2, 3))
    manifest_path = surface_dir / "reference" / "manifest.toml"
    _write_manifest(manifest_path, [{
        "id": "widget-ghost",
        "state": "ghost",
        "device": "phone",
        "reference": str(ref_png),
        "design_source_commit": "0000000",
        "masks": [],
    }])
    with pytest.raises(reference_diff.UnknownState):
        reference_diff.load_manifest(manifest_path)


def test_design_debt_report_lists_states_without_a_reference(tmp_path):
    surface_dir = _surface(tmp_path, "widget", ["default", "hover", "empty"])
    ref_png = _save(surface_dir / "reference" / "default.png", (10, 10), (1, 2, 3))
    manifest_path = surface_dir / "reference" / "manifest.toml"
    _write_manifest(manifest_path, [{
        "id": "widget-default",
        "state": "default",
        "device": "phone",
        "reference": str(ref_png),
        "design_source_commit": "0000000",
        "masks": [],
    }])
    entries = reference_diff.load_manifest(manifest_path)
    assert len(entries) == 1
    assert entries[0].state == "default"
    assert entries[0].reference == ref_png

    states = reference_diff.load_states(surface_dir / "states.toml")
    report = reference_diff.design_debt_report(entries, states=states)
    assert report["with_reference"] == 1
    assert report["without_reference"] == 2
    assert report["without_reference_list"] == ["empty", "hover"]
