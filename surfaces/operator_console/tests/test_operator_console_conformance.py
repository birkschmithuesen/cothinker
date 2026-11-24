"""Issue #173 acceptance: the operator console passes the operator
conformance check (issue #159) against its fixture backend."""
from __future__ import annotations

from pathlib import Path

from surfaces.operator.check import run_check

INDEX = Path(__file__).resolve().parents[1] / "index.html"


def test_operator_console_passes_the_operator_check(tmp_path):
    report = run_check(str(INDEX), out=tmp_path)
    failed = [check.to_dict() for check in report.checks if not check.passed]
    assert not failed, failed
