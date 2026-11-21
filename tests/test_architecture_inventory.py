# CoThinker — docs/architecture/inventory_check.py: block inventory check
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_architecture_inventory.py"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "docs" / "architecture" / "inventory_check.py"

SUMMARY_RE = re.compile(
    r"^cothinker@HEAD: (\d+) source files, (\d+) mapped, "
    r"(\d+) unmapped, (\d+) stale, (\d+) unknown targets$")


def _run():
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=REPO,
                          capture_output=True, text=True)


def test_inventory_check_exits_zero():
    r = _run()
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_unmapped_stale_or_unknown_targets():
    r = _run()
    lines = r.stdout.strip().splitlines()
    m = SUMMARY_RE.match(lines[0])
    assert m, r.stdout
    total, mapped, unmapped, stale, unknown = (int(g) for g in m.groups())
    assert total == mapped, r.stdout
    assert (unmapped, stale, unknown) == (0, 0, 0), r.stdout


def test_last_line_is_ok():
    r = _run()
    lines = r.stdout.strip().splitlines()
    assert lines[-1] == "OK", r.stdout
