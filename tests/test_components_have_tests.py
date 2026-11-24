# CoThinker — ADR 0022 level 1: every component carries its own tests
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Every directory directly under `blocks/`, `modules/`, `templates/` and
`surfaces/` is a component; ADR 0022 level 1 says its tests live with its
code, in a `<component>/tests/` directory holding at least one
`test_*.py` file. `ALLOWLIST` below is the explicit, shrinking exception
list for components that do not have one yet -- each entry names the
issue that will add it. Remove an entry once that component gets a real
`tests/` directory with a real test in it.

python3 -m pytest -q tests/test_components_have_tests.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

COMPONENT_ROOTS = ("blocks", "modules", "templates", "surfaces")

# issue #153 (ADR 0022 level 4 / overall tracking issue) covers adding
# tests/ to every entry below.
ALLOWLIST = frozenset({
    "templates/reflect",   # issue #153
    "templates/inscribe",  # issue #153
    "surfaces/contract",   # issue #153
    "surfaces/operator",   # issue #153 -- landed (issue #159) before ADR 0022
                            # level 1 (issue #155); its tests live in the
                            # repo-level tests/test_operator_*.py layout.
})


def find_untested_components(root: Path, component_roots=COMPONENT_ROOTS, allowlist=ALLOWLIST) -> list[str]:
    """Components (immediate subdirectories of each root) whose `tests/`
    directory is missing or holds no `test_*.py` file, skipping anything
    on `allowlist`. Returns rootdir-relative POSIX paths, sorted."""
    offenders = []
    for root_name in component_roots:
        base = root / root_name
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "__")):
                continue
            rel = child.relative_to(root).as_posix()
            if rel in allowlist:
                continue
            tests_dir = child / "tests"
            if not tests_dir.is_dir() or not any(tests_dir.glob("test_*.py")):
                offenders.append(rel)
    return sorted(offenders)


def test_every_component_has_a_non_empty_tests_dir():
    offenders = find_untested_components(REPO)
    assert not offenders, f"components without a tests/test_*.py: {offenders}"


def test_scanner_flags_a_component_whose_tests_dir_is_removed(tmp_path):
    """Mutation check: builds a fake block with a real tests/ directory,
    confirms the scanner is happy, then removes that directory and
    confirms the scanner reports it -- proving the check above would
    actually fail, not just that it passes today."""
    block_dir = tmp_path / "blocks" / "demo"
    tests_dir = block_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_demo.py").write_text("", encoding="utf-8")

    assert find_untested_components(tmp_path, component_roots=("blocks",), allowlist=frozenset()) == []

    shutil.rmtree(tests_dir)

    assert find_untested_components(tmp_path, component_roots=("blocks",), allowlist=frozenset()) == ["blocks/demo"]
