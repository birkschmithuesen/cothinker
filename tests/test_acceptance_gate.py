import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.acceptance import gate, report  # noqa: E402
from demo.acceptance.acceptances import Acceptance  # noqa: E402
from demo.acceptance.fingerprint import Fingerprint  # noqa: E402

CUR = Fingerprint("d1", {"unit:stt:argv": "h1"})


def _write(tmp, rid, digest, checks, pre=None, parts=None):
    r = report.Report(rid, fingerprint={"digest": digest, "parts": parts or {"unit:stt:argv": "h1"}},
                      preflight_run_id=pre)
    for cid, st in checks:
        r.add(cid, st)
    r.write(tmp / rid)


def test_go_with_matching_fingerprint(tmp_path):
    _write(tmp_path, "pre1", "d1", [("preflight.fingerprint", "PASS")])
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1")
    assert gate.decide(CUR, tmp_path, []) == (True, ["GO: run1 (preflight pre1)"])


def test_no_go_names_changed_input(tmp_path):
    _write(tmp_path, "pre1", "d0", [], parts={"unit:stt:argv": "h0"})
    _write(tmp_path, "run1", "d0", [("scenario.a", "PASS")], pre="pre1", parts={"unit:stt:argv": "h0"})
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "unit:stt:argv" in why[0]


def test_no_go_without_any_bound_report(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "no level-4 report bound to a preflight" in why[0]


def test_fail_needs_human_acceptance_bound_to_fingerprint(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "run1", "d1", [("scenario.d.topic", "FAIL")], pre="pre1")
    assert gate.decide(CUR, tmp_path, [])[0] is False
    stale = Acceptance("scenario.d.topic", "d0", "A Human", "F2", "author-checked")
    assert gate.decide(CUR, tmp_path, [stale])[0] is False
    other = Acceptance("scenario.other", "d1", "A Human", "F2", "author-checked")
    assert gate.decide(CUR, tmp_path, [other])[0] is False
    fresh = Acceptance("scenario.d.topic", "d1", "A Human", "F2", "author-checked")
    ok, why = gate.decide(CUR, tmp_path, [fresh])
    assert ok and "accepted by A Human (author-checked)" in " ".join(why)


def test_verification_statement_is_made_and_claims_no_more(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "run1", "d1", [("scenario.d.topic", "FAIL")], pre="pre1")
    author = Acceptance("scenario.d.topic", "d1", "A Human", "F2", "author-checked")
    ok, why = gate.decide(CUR, tmp_path, [author])
    text = " ".join(why)
    assert ok and "acceptance verification: author-checked" in text
    assert "forgeable" in text and "signature" not in text.replace("signature-checked", "")
    signed = Acceptance("scenario.d.topic", "d1", "A Human", "F2", "signature-checked")
    ok, why = gate.decide(CUR, tmp_path, [signed])
    assert "acceptance verification: signature-checked" in " ".join(why)
    assert "not that a human" in " ".join(why)


def test_report_older_than_marker_is_no_go(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1")
    marker = tmp_path / "last-start.json"
    marker.write_text('{"at": 9999999999}')
    ok, why = gate.decide(CUR, tmp_path, [], marker)
    assert ok is False and "older than the last demo.sh start" in why[0]
    marker.write_text('{"at": 0}')
    assert gate.decide(CUR, tmp_path, [], marker)[0] is True


def test_unreadable_marker_fails_closed(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1")
    assert gate.decide(CUR, tmp_path, [], tmp_path / "missing.json")[0] is False
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert gate.decide(CUR, tmp_path, [], bad)[0] is False
    bad.write_text('{"at": "soon"}')
    assert gate.decide(CUR, tmp_path, [], bad)[0] is False


def _write_at(tmp, rid, checks, started_at):
    r = report.Report(rid, fingerprint={"digest": "d1", "parts": {"unit:stt:argv": "h1"}},
                      preflight_run_id="pre1", started_at=started_at)
    for cid, st in checks:
        r.add(cid, st)
    r.write(tmp / rid)


def test_newest_matching_report_decides(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write_at(tmp_path, "old", [("scenario.a", "FAIL")], started_at=100.0)
    _write_at(tmp_path, "new", [("scenario.a", "PASS")], started_at=200.0)
    assert gate.decide(CUR, tmp_path, [])[1][0].startswith("GO: new")
    _write_at(tmp_path, "newer", [("scenario.a", "FAIL")], started_at=300.0)
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "open FAIL scenario.a" in why[0]


def test_preflight_must_carry_the_same_digest(tmp_path):
    _write(tmp_path, "pre1", "d0", [], parts={"unit:stt:argv": "h0"})
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and why[0].startswith("NO-GO")


def test_gate_never_writes_or_names_a_writer_of_accepted_by():
    tree = ast.parse((ROOT / "demo" / "acceptance" / "gate.py").read_text(encoding="utf-8"))
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"write_text", "write_bytes", "write", "mkdir", "unlink"}
    assert "sudo" not in (ROOT / "demo" / "acceptance" / "gate.py").read_text(encoding="utf-8")
