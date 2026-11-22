# CoThinker — hygiene check for imported prompts and catalogues (repo split E.0.8)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Every imported prompt, profile or catalogue file under templates/ and
blocks/ must be listed in the nearest PROVENANCE.toml with its current
sha256 and `reviewed = true`. Those trees, plus surfaces/ and
instances/example/, must stay free of the public denylist classes, and no
prompt may tell the agent it cannot write into the script.

python3 -m pytest -q tests/test_public_text_hygiene.py
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO))

import tools.publish_check as publish_check  # noqa: E402

# Globs that define "imported prompt, profile or catalogue file" (issue #62
# / task E.0.8). Each glob's first two path parts (templates/<name> or
# blocks/<pkg>) are the package root that must hold a PROVENANCE.toml.
IMPORTED_TEXT_GLOBS = [
    "templates/*/prompts/**/*.md",
    "templates/*/prompts/**/*.toml",
    "templates/*/profile/**/*.md",
    "templates/*/profile/**/*.toml",
    "blocks/*/prompts/**/*.md",
    "blocks/*/prompts/**/*.toml",
    "blocks/*/catalogue/**/*.md",
    "blocks/*/catalogue/**/*.toml",
]

DENYLIST_SCAN_ROOTS = ["blocks", "templates", "surfaces", "instances/example"]

CAPABILITY_DENIALS = [
    "cannot write into the script",
    "can't write into the script",
    "kannst nicht ins script",
]


def imported_text_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in IMPORTED_TEXT_GLOBS:
        files.update(p for p in REPO.glob(pattern) if p.is_file())
    return sorted(files)


def package_root(path: Path) -> Path:
    rel = path.relative_to(REPO)
    return REPO / rel.parts[0] / rel.parts[1]


def load_provenance(provenance_path: Path) -> dict[str, dict]:
    data = tomllib.loads(provenance_path.read_text(encoding="utf-8"))
    return {entry["path"]: entry for entry in data.get("file", [])}


def all_provenance_files() -> list[Path]:
    found: set[Path] = set()
    for root in ("templates", "blocks"):
        if (REPO / root).is_dir():
            found.update((REPO / root).glob("*/PROVENANCE.toml"))
    return sorted(found)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_imported_text_file_is_listed_with_current_hash():
    for path in imported_text_files():
        provenance_path = package_root(path) / "PROVENANCE.toml"
        assert provenance_path.is_file(), (
            f"missing {provenance_path.relative_to(REPO)} for {path.relative_to(REPO)}"
        )
        entries = load_provenance(provenance_path)
        rel = str(path.relative_to(REPO))
        assert rel in entries, f"{rel} not listed in {provenance_path.relative_to(REPO)}"
        entry = entries[rel]
        assert entry.get("sha256") == sha256_of(path), f"stale sha256 for {rel}"
        assert entry.get("reviewed") is True, f"{rel} not marked reviewed"


def test_listed_files_exist():
    for provenance_path in all_provenance_files():
        for rel, entry in load_provenance(provenance_path).items():
            assert (REPO / rel).is_file(), (
                f"{rel} listed in {provenance_path.relative_to(REPO)} does not exist"
            )


def test_no_publish_denylist_class_in_ported_trees():
    pattern_list = publish_check.load_list(publish_check.PUBLIC_DENYLIST, "public")
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--"] + DENYLIST_SCAN_ROOTS,
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    hits = []
    for rel in tracked:
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern_list.hit(line):
                hits.append(f"{rel}:{lineno}")
    assert hits == []


def test_no_capability_denial_in_prompts():
    hits = []
    for path in imported_text_files():
        lowered = path.read_text(encoding="utf-8").lower()
        for needle in CAPABILITY_DENIALS:
            if needle in lowered:
                hits.append((str(path.relative_to(REPO)), needle))
    assert hits == []
