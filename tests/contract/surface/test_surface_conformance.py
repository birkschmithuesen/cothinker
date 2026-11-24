"""Surface API contract (ADR 0022 level 2): the surface conformance check of
ADR 0019 point 4 (issue #142); behaviour: docs/architecture/target.md §2.3 and
docs/surface-contract.md.

A subject is a surface, given as a path to its `index.html` (the same value
`python -m surfaces.contract.check --surface` takes). The tiny good reference
surface must pass every check, and a deliberately broken one must fail with
the right reason -- the mutation proof the acceptance criteria ask for.
Outside surfaces: `COTHINKER_CONTRACT_SURFACE=<path to index.html>`
(docs/contract-tests.md).
"""
from __future__ import annotations

from pathlib import Path

from surfaces.contract.check import run_check

CONTRACT_API = "surface"
REPO_ROOT = Path(__file__).resolve().parents[3]
MINIMAL_SURFACE = REPO_ROOT / "surfaces" / "contract" / "examples" / "minimal" / "index.html"
BROKEN_SURFACE = Path(__file__).resolve().parent / "broken_surface" / "index.html"
BUILTIN_SUBJECTS = {"minimal": MINIMAL_SURFACE}


def resolve_outside(value: str) -> str:
    return value


def test_the_surface_passes_every_check(subject, tmp_path):
    report = run_check(str(subject), scenario="meeting", out=tmp_path)

    failed = [check for check in report.checks if not check.passed]
    assert not failed, f"unexpected failures: {failed}"
    assert report.passed

    report_path = tmp_path / "report.json"
    assert report_path.exists()
    screenshots = list(tmp_path.glob("*.png"))
    assert screenshots, "expected at least one per-intent screenshot"


def test_broken_example_fails_with_the_extra_host_reason(tmp_path):
    report = run_check(str(BROKEN_SURFACE), scenario="meeting", out=tmp_path)

    assert not report.passed
    network_failures = [
        check for check in report.checks
        if not check.passed and "example.invalid" in check.reason
    ]
    assert network_failures, [c.to_dict() for c in report.checks]


def test_broken_example_fails_with_the_outside_contract_path_reason(tmp_path):
    report = run_check(str(BROKEN_SURFACE), scenario="meeting", out=tmp_path)

    assert not report.passed
    path_failures = [
        check for check in report.checks
        if not check.passed and "redoc" in check.reason
    ]
    assert path_failures, [c.to_dict() for c in report.checks]
