"""Fixture-project reproduction for issue #205: the call lane told the agent
to use `read_file` on the names in `CALL_EXTRA_FILES_LINE` because its
`search_files` tool cannot see symlinks (`gateway/call_lane.py`'s
`CALL_EXTRA_FILES_LINE`). `_extra_dir_files()` built that name list by
looking at the extra dir's TOP level only -- but the real extra dir is "a
folder of symlinks into the vault" (`gateway/stage_image.py`), and the
vault's own collection lives behind directory symlinks, not file symlinks
(`graph/vault_notes.py`'s SYMLINK TRAP, measured: 4 notes without following
directory symlinks, 869 with). A note one directory-symlink below the extra
dir was therefore in neither tool's reach: `search_files` cannot see the
link, and the hint line never named it either -- the agent's first (and
second) answer was "not found".

This module doubles as the level-4 catalogue entry the issue asked for: one
fixture project (`_make_vault_fixture`) that stands in for a real meeting's
`COTHINKER_EXTRA_DIRS` entry, and the scenarios below that must stay green
against it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))

import call_lane as li  # noqa: E402


def _make_vault_fixture(tmp_path: Path) -> Path:
    """A fixture project: a meeting's reference folder (`extra_dir`) that
    holds one real top-level file plus a symlink to a vault subdirectory --
    the same shape `stage_image.py` documents ("a folder of symlinks into
    the vault"). `einkaufsliste.md` is the file the live trial could not
    find on the first request: it lives inside the symlinked directory,
    not directly under `extra_dir`.
    """
    vault = tmp_path / "vault"
    wissen = vault / "10-Wissen"
    wissen.mkdir(parents=True)
    (wissen / "einkaufsliste.md").write_text("# Einkaufsliste\n- Milch\n", encoding="utf-8")

    extra_dir = tmp_path / "extra_dir"
    extra_dir.mkdir()
    (extra_dir / "README.md").write_text("# Reference\n", encoding="utf-8")
    (extra_dir / "10-Wissen").symlink_to(wissen, target_is_directory=True)
    return extra_dir


def test_extra_dir_files_finds_note_behind_symlinked_directory(tmp_path):
    extra_dir = _make_vault_fixture(tmp_path)

    names = li._extra_dir_files(extra_dir)

    assert "10-Wissen/einkaufsliste.md" in names


def test_call_paths_text_hints_the_agent_at_the_nested_file(tmp_path):
    extra_dir = _make_vault_fixture(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    protocol = folder / "protokoll.md"
    protocol.write_text("", encoding="utf-8")

    text = li.call_paths_text(folder, protocol, extra_dirs=[str(extra_dir)])

    assert "einkaufsliste.md" in text


def test_extra_dir_files_plain_folder_stays_empty(tmp_path):
    """No symlink anywhere: `search_files` already covers it, no hint needed."""
    extra_dir = tmp_path / "extra_dir"
    sub = extra_dir / "sub"
    sub.mkdir(parents=True)
    (sub / "notiz.md").write_text("x", encoding="utf-8")

    assert li._extra_dir_files(extra_dir) == []


def test_extra_dir_files_handles_symlink_cycle(tmp_path):
    """A directory symlink that loops back on itself must not hang the scan."""
    extra_dir = tmp_path / "extra_dir"
    extra_dir.mkdir()
    (extra_dir / "loop").symlink_to(extra_dir, target_is_directory=True)

    assert li._extra_dir_files(extra_dir) == []
