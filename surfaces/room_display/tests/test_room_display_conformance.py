"""Issue #173 acceptance: the room display passes the surface conformance
check (ADR 0019 point 4) against the "meeting" fixture scenario."""
from __future__ import annotations

from pathlib import Path

from surfaces.contract.check import run_check

INDEX = Path(__file__).resolve().parents[1] / "index.html"


def test_room_display_passes_the_conformance_check(tmp_path):
    report = run_check(str(INDEX), scenario="meeting", out=tmp_path)
    failed = [check.to_dict() for check in report.checks if not check.passed]
    assert not failed, failed
