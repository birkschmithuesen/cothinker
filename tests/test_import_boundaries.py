# CoThinker — repo-split import boundary checks
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`gateway/` and `stt/` must not import `workshop/` -- that package is not
part of this repo. The private deployment's identity re-export checks
(`workshop.agent_api`, `workshop.agent_port`, `workshop.utterance`) are out
of scope here for the same reason.

A block imports only `shared/` and its own package; nothing under
`blocks/`, `templates/`, `surfaces/` imports `gateway`, `workshop`, `stage`,
or another block. Those three directories may not exist yet in this repo --
that is not a violation, there is simply nothing to check.

`shared/` and `scripts/` carry legacy `workshop` imports that predate this
boundary; those are pinned to an allow-list below instead of being forbidden
outright.

python3 -m pytest -q tests/test_import_boundaries.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Files under shared/ and scripts/ that import `workshop` as of the last
# measurement. This set may only shrink (a fixed file falls out of it); a
# newly introduced, unlisted import is a violation.
WORKSHOP_IMPORT_ALLOWLIST = frozenset({
    "shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py",
    "shared/profiles/skel/main/skills/prolog/scripts/pick_questions.py",
    "scripts/build-wall-demo.py",
    "scripts/measure-echo-rolling.py",
    "scripts/measure-own-question.py",
    "scripts/measure_hermes/api_client.py",
    "scripts/own-question-turn-dump.py",
    "scripts/render-floor-spot-png.py",
})


def git_grep_import(module: str, path: str) -> str:
    """Empty string means no match (`git grep` exits 1 on no match, which
    is not an error here). A pathspec for a directory that does not exist
    in the tree also just yields no match. `--untracked` also covers new,
    not-yet-added files (still honouring .gitignore); `-I` skips binaries
    such as stray bytecode."""
    r = subprocess.run(
        ["git", "grep", "--untracked", "-I", "-nE",
         rf"^\s*(from|import) {module}", "--", path],
        cwd=REPO, capture_output=True, text=True)
    assert r.returncode in (0, 1), r.stderr
    return r.stdout


def git_grep_workshop(path: str) -> str:
    return git_grep_import("workshop", path)


@pytest.mark.parametrize("path", ["gateway", "stt"])
def test_no_workshop_import_under(path):
    out = git_grep_workshop(path)
    assert out == "", out


def test_workshop_imports_under_shared_and_scripts_are_pinned():
    found = set()
    for path in ("shared", "scripts"):
        for line in git_grep_workshop(path).splitlines():
            found.add(line.split(":", 1)[0])
    unlisted = found - WORKSHOP_IMPORT_ALLOWLIST
    assert not unlisted, unlisted


@pytest.mark.parametrize("path", ["blocks", "templates", "surfaces"])
@pytest.mark.parametrize("forbidden", ["gateway", "workshop", "stage"])
def test_no_forbidden_import_under_new_packages(path, forbidden):
    out = git_grep_import(forbidden, path)
    assert out == "", out


def _owning_block(file_path: str) -> str | None:
    parts = Path(file_path).parts
    if len(parts) > 1 and parts[0] == "blocks":
        return parts[1]
    return None


def test_no_cross_block_imports_under_blocks_templates_surfaces():
    """A block may import its own package (`blocks.<name>`); templates/
    and surfaces/ have no "own" block, so any `blocks.*` import there is a
    violation too."""
    violations = []
    for path in ("blocks", "templates", "surfaces"):
        for line in git_grep_import(r"blocks\.[A-Za-z0-9_]+", path).splitlines():
            file_path, _, content = line.split(":", 2)
            imported = re.search(r"blocks\.([A-Za-z0-9_]+)", content).group(1)
            if imported != _owning_block(file_path):
                violations.append(line)
    assert not violations, "\n".join(violations)
