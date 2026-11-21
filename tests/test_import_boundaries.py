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

python3 -m pytest -q tests/test_import_boundaries.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def git_grep_workshop(path: str) -> str:
    """Empty string means no match (`git grep` exits 1 on no match, which
    is not an error here)."""
    r = subprocess.run(
        ["git", "grep", "-nE", r"^\s*(from|import) workshop", "--", path],
        cwd=REPO, capture_output=True, text=True)
    return r.stdout


@pytest.mark.parametrize("path", ["gateway", "stt"])
def test_no_workshop_import_under(path):
    out = git_grep_workshop(path)
    assert out == "", out
