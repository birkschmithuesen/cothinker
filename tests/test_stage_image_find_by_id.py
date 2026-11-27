"""Sibling variant of issue #205's root cause: `stage_image.find_by_id` is a
second reader of the same COTHINKER_EXTRA_DIRS shape `lane_integration.
_extra_dir_files` reads ("a folder of symlinks into the vault" --
`stage_image.py`'s own docstring). Its walk explicitly did not follow
directory symlinks either, so an image behind a symlinked vault
subdirectory of an extra dir could not be re-found by id once the
in-memory `_image_cache` in `stage/stage.py::image_file()` is empty (e.g.
after a stage restart) -- the same "not found" symptom, one layer down.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))

import stage_image  # noqa: E402


def _make_vault_fixture(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    bilder = vault / "20-Bilder"
    bilder.mkdir(parents=True)
    image = bilder / "diagramm.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    extra_dir = tmp_path / "extra_dir"
    extra_dir.mkdir()
    (extra_dir / "20-Bilder").symlink_to(bilder, target_is_directory=True)
    return extra_dir, image


def test_find_by_id_finds_image_behind_symlinked_directory(tmp_path):
    extra_dir, image = _make_vault_fixture(tmp_path)
    dirs = stage_image.allowed_dirs(tmp_path / "project", [str(extra_dir)])
    wanted = stage_image.image_id(extra_dir / "20-Bilder" / "diagramm.png")

    found = stage_image.find_by_id(wanted, dirs)

    assert found is not None
    assert found.name == "diagramm.png"


def test_find_by_id_handles_symlink_cycle(tmp_path):
    extra_dir = tmp_path / "extra_dir"
    extra_dir.mkdir()
    (extra_dir / "loop").symlink_to(extra_dir, target_is_directory=True)
    dirs = stage_image.allowed_dirs(tmp_path / "project", [str(extra_dir)])

    assert stage_image.find_by_id("0" * 16, dirs) is None
