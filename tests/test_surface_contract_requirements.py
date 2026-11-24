"""Dependency hygiene for the surface contract (issue #147 point 1):
`httpx2` is an unrelated PyPI package, never imported by this app; it was
added to the runtime `requirements.txt` only to make `fastapi.testclient`
importable in tests. Test-only dependencies belong in their own file, and
that file must name the package `fastapi.testclient.TestClient` actually
imports, not the look-alike.
"""
from __future__ import annotations

from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "surfaces" / "contract"


def test_runtime_requirements_do_not_pin_the_test_only_http_client():
    text = (CONTRACT_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx2" not in text, "httpx2 is test-only (fastapi.testclient), not a runtime dependency"


def test_test_requirements_file_pins_the_package_testclient_actually_imports():
    import httpx  # noqa: F401  (import error would mean the premise is wrong)

    path = CONTRACT_DIR / "requirements-test.txt"
    assert path.exists(), "surfaces/contract/requirements-test.txt is missing"
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.strip().startswith("#")]
    assert lines == ["httpx"]
