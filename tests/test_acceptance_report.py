import json
from pathlib import Path

import pytest

from demo.acceptance import report


def test_report_roundtrip_and_redaction(tmp_path):
    r = report.Report(report.new_run_id(), base_url="https://example.org", token="s3cret",
                      fingerprint={"digest": "ab", "parts": {"x": "cd"}}, preflight_run_id="p1")
    r.add("preflight.stt.eou", "FAIL", "found eou_silence=0.5 token=s3cret")
    r.add("scenario.h", "SKIPPED", "judge disabled")
    r.write(tmp_path)
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["fingerprint"]["digest"] == "ab"
    assert data["preflight_run_id"] == "p1"
    assert data["checks"][0] == {"id": "preflight.stt.eou", "status": "FAIL",
                                 "evidence": "found eou_silence=0.5 token=<redacted>"}
    assert "s3cret" not in (tmp_path / "report.md").read_text()
    assert r.passed is False


def test_check_aliases_keep_pr174_shape():
    c = report.Check("wake", "PASS", "ok")
    assert (c.name, c.detail) == ("wake", "ok")


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        report.Report("r").add("x", "OK")


def test_no_tool_writes_accepted_by():
    # ADR 0025 rule 7: only gate.py/acceptances.py may mention the key, and they never write files.
    root = Path(__file__).resolve().parents[1] / "demo" / "acceptance"
    for py in root.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if py.name in ("gate.py", "acceptances.py"):
            assert not any(w in text for w in ("write_text", "write_bytes", "open(")), py.name
        else:
            assert "accepted_by" not in text, py.name
