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


def test_bind_preflight(tmp_path):
    pre = report.Report("pre1", fingerprint={"digest": "d1", "parts": {}})
    pre.write(tmp_path / "pre")
    r = report.Report("run1")
    report.bind_preflight(r, tmp_path / "pre" / "report.json")
    assert (r.preflight_run_id, r.fingerprint["digest"]) == ("pre1", "d1")
    r2 = report.Report("run2")
    report.bind_preflight(r2, None)
    assert r2.checks[0].id == "fingerprint.bound" and r2.checks[0].status == "SKIPPED"


def test_bind_preflight_without_fingerprint_is_a_fail_not_a_crash(tmp_path):
    for fp in (None, {}, {"digest": None}):
        pre = report.Report("pre1", fingerprint=fp)
        pre.write(tmp_path / "pre")
        r = report.Report("run1")
        report.bind_preflight(r, tmp_path / "pre" / "report.json")
        assert r.preflight_run_id is None and r.fingerprint is None
        assert [(c.id, c.status) for c in r.checks] == [("fingerprint.bound", "FAIL")]


def test_tool_is_written_to_the_report(tmp_path):
    assert report.Report("r", tool="e2e").to_dict()["tool"] == "e2e"
