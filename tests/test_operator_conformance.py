"""The operator conformance check (issue #159, ADR 0019 point 4 applied
to the operator audience, mirroring tests/test_surface_conformance.py): a
minimal good reference surface must pass every check, and a deliberately
broken one must fail with the right reason.
"""
from __future__ import annotations

from pathlib import Path

from surfaces.operator.check import run_check

REPO_ROOT = Path(__file__).resolve().parent.parent
MINIMAL_SURFACE = REPO_ROOT / "surfaces" / "operator" / "examples" / "minimal" / "index.html"
BROKEN_SURFACE = REPO_ROOT / "tests" / "fixtures" / "broken_operator_surface" / "index.html"


def test_minimal_example_passes_every_check(tmp_path):
    report = run_check(str(MINIMAL_SURFACE), out=tmp_path)

    failed = [check for check in report.checks if not check.passed]
    assert not failed, f"unexpected failures: {failed}"
    assert report.passed

    report_path = tmp_path / "report.json"
    assert report_path.exists()


def test_broken_example_fails_with_the_extra_host_reason(tmp_path):
    report = run_check(str(BROKEN_SURFACE), out=tmp_path)

    assert not report.passed
    network_failures = [
        check for check in report.checks
        if not check.passed and "example.invalid" in check.reason
    ]
    assert network_failures, [c.to_dict() for c in report.checks]


def test_broken_example_fails_with_the_outside_contract_path_reason(tmp_path):
    report = run_check(str(BROKEN_SURFACE), out=tmp_path)

    assert not report.passed
    path_failures = [
        check for check in report.checks
        if not check.passed and "redoc" in check.reason
    ]
    assert path_failures, [c.to_dict() for c in report.checks]


def test_minimal_example_sends_the_token_as_a_bearer_header_not_a_query_param(tmp_path):
    report = run_check(str(MINIMAL_SURFACE), out=tmp_path)
    carries_header = next(c for c in report.checks if c.name == "sends the operator token as a Bearer header")
    assert carries_header.passed, carries_header.reason
