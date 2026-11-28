import ast
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from demo.acceptance import gate, report  # noqa: E402
from demo.acceptance.acceptances import Acceptance  # noqa: E402
from demo.acceptance.fingerprint import Fingerprint  # noqa: E402

CUR = Fingerprint("d1", {"unit:stt:argv": "h1"})


def _write(tmp, rid, digest, checks, pre=None, parts=None, raw=False, started_at=None, tool="e2e"):
    r = report.Report(rid, fingerprint={"digest": digest, "parts": parts or {"unit:stt:argv": "h1"}},
                      preflight_run_id=pre, tool=tool if pre else "")
    if started_at is not None:
        r.started_at = started_at
    if not raw:
        checks = [("fingerprint.bound", "PASS") if pre else ("preflight.fingerprint", "PASS")] + list(checks)
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
    _write(tmp_path, "run1", "d1", [("scenario.ok", "PASS"), ("scenario.d.topic", "FAIL")], pre="pre1")
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
    _write(tmp_path, "run1", "d1", [("scenario.ok", "PASS"), ("scenario.d.topic", "FAIL")], pre="pre1")
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
    _write(tmp, rid, "d1", checks, pre="pre1", started_at=started_at)


def test_newest_matching_report_decides(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write_at(tmp_path, "old", [("scenario.ok", "PASS"), ("scenario.a", "FAIL")], started_at=100.0)
    _write_at(tmp_path, "new", [("scenario.a", "PASS")], started_at=200.0)
    assert gate.decide(CUR, tmp_path, [])[1][0].startswith("GO: new")
    _write_at(tmp_path, "newer", [("scenario.ok", "PASS"), ("scenario.a", "FAIL")], started_at=300.0)
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "open FAIL scenario.a" in why[0]


def test_other_tool_pass_cannot_hide_a_failing_run(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "e2e1", "d1", [("scenario.ok", "PASS"), ("e2e.x", "FAIL")], pre="pre1",
           started_at=100.0, tool="e2e")
    _write(tmp_path, "sc1", "d1", [("scenario.ok", "PASS")], pre="pre1", started_at=200.0, tool="scenarios")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "open FAIL e2e.x" in why[0]


def test_matching_report_without_tool_is_no_go(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1", tool="")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "has no tool" in why[0]


def test_preflight_fail_is_no_go_unless_accepted(tmp_path):
    _write(tmp_path, "pre1", "d1", [("preflight.lane-profile.call", "FAIL")])
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "open FAIL preflight.lane-profile.call" in why[0]
    acc = Acceptance("preflight.lane-profile.call", "d1", "A Human", "F2", "author-checked")
    ok, why = gate.decide(CUR, tmp_path, [acc])
    assert ok and "accepted by A Human" in " ".join(why)


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


def _mutate(tmp, rid, fn):
    p = tmp / rid / "report.json"
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d))


def _pair(tmp, checks, **kw):
    _write(tmp, "pre1", "d1", [])
    _write(tmp, "run1", "d1", checks, pre="pre1", **kw)


def test_empty_checks_is_no_go(tmp_path):
    _pair(tmp_path, [], raw=True)
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "not bound to a preflight" in why[0]


def test_skipped_only_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "SKIPPED")], raw=True)
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "not bound to a preflight" in why[0]
    _pair(tmp_path, [("fingerprint.bound", "SKIPPED"), ("scenario.a", "PASS")], raw=True)
    assert not gate.decide(CUR, tmp_path, [])[0]


def test_missing_or_non_list_checks_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "PASS")])
    _mutate(tmp_path, "run1", lambda d: d.pop("checks"))
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "checks" in why[0]
    _mutate(tmp_path, "run1", lambda d: d.update(checks="PASS"))
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "checks" in why[0]


def test_unknown_or_missing_status_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "PASS")])
    _mutate(tmp_path, "run1", lambda d: d["checks"].append({"id": "scenario.b", "status": "MAYBE"}))
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "scenario.b" in why[0] and "status" in why[0]
    _mutate(tmp_path, "run1", lambda d: d["checks"][-1].pop("status"))
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "status" in why[0]


def test_run_without_pass_bound_check_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "PASS")], raw=True)
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "not bound to a preflight" in why[0]


def test_run_exercising_nothing_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "SKIPPED")])
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "exercised nothing" in why[0]


def test_unparseable_report_file_is_no_go_naming_it(tmp_path):
    _write(tmp_path, "pre1", "d1", [])
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1", started_at=100.0)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "report.json").write_text("{not json")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "broken" in why[0]


def test_non_numeric_or_non_finite_started_at_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "PASS")])
    for bad in ("soon", None, "NaN"):
        _mutate(tmp_path, "run1", lambda d, bad=bad: d.update(started_at=bad))
        ok, why = gate.decide(CUR, tmp_path, [])
        assert not ok and "started_at" in why[0]
    (tmp_path / "run1" / "report.json").write_text(
        (tmp_path / "run1" / "report.json").read_text().replace('"started_at": "NaN"', '"started_at": NaN'))
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "started_at" in why[0]


def test_non_finite_marker_is_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "PASS")])
    marker = tmp_path / "m.json"
    for bad in ("NaN", "Infinity", "-Infinity"):
        marker.write_text('{"at": %s}' % bad)
        ok, why = gate.decide(CUR, tmp_path, [], marker)
        assert not ok and "start marker" in why[0]


def test_self_referencing_run_is_no_go(tmp_path):
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="run1")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "itself" in why[0]


def test_preflight_must_be_a_real_passing_preflight(tmp_path):
    _write(tmp_path, "pre1", "d1", [("scenario.a", "PASS")], raw=True)
    _write(tmp_path, "run1", "d1", [("scenario.a", "PASS")], pre="pre1")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "preflight.fingerprint" in why[0]
    _write(tmp_path, "pre1", "d1", [("preflight.fingerprint", "FAIL")], raw=True)
    assert not gate.decide(CUR, tmp_path, [])[0]
    _write(tmp_path, "pre1", "d1", [("preflight.fingerprint", "PASS")], raw=True, pre="pre0")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "level-4 run" in why[0]


def test_duplicate_run_ids_are_no_go(tmp_path):
    _pair(tmp_path, [("scenario.a", "PASS")])
    shutil.copytree(tmp_path / "run1", tmp_path / "run1-copy")
    ok, why = gate.decide(CUR, tmp_path, [])
    assert not ok and "duplicate run id" in why[0]


def _factory_main(tmp_path, monkeypatch, capsys, factory_text, extra=()):
    from demo.acceptance import preflight
    monkeypatch.setattr(preflight, "current_fingerprint", lambda **kw: CUR)
    _pair(tmp_path, [("scenario.a", "PASS")])
    fac = tmp_path / "FACTORY.yaml"
    fac.write_text(factory_text)
    acc = tmp_path / "acc.toml"
    acc.write_text("")
    if "--marker" not in extra:
        marker = tmp_path / "default-marker.json"
        marker.write_text('{"at": 0}')
        extra = [*extra, "--marker", str(marker)]
    rc = gate.main(["--reports", str(tmp_path), "--acceptances", str(acc), "--factory", str(fac),
                    "--consumer-unit", "u", *extra])
    return rc, capsys.readouterr().out


def test_malformed_factory_yaml_fails_closed_with_note(tmp_path, monkeypatch, capsys):
    for text in ("a: [unclosed", "- just\n- a list\n", "just a string"):
        rc, out = _factory_main(tmp_path, monkeypatch, capsys, text)
        assert "no acceptance can count" in out, text
        assert rc == 0 and "GO: run1" in out


def test_marker_is_required(tmp_path):
    with pytest.raises(SystemExit):
        gate.main(["--reports", str(tmp_path), "--acceptances", str(tmp_path), "--consumer-unit", "u"])


def test_main_missing_marker_file_is_no_go(tmp_path, monkeypatch, capsys):
    rc, out = _factory_main(tmp_path, monkeypatch, capsys, "commit_identity: {}\n",
                            ["--marker", str(tmp_path / "absent.json")])
    assert rc == 1 and "NO-GO" in out


def test_consumer_unit_is_required(tmp_path):
    with pytest.raises(SystemExit):
        gate.main(["--reports", str(tmp_path), "--acceptances", str(tmp_path), "--consumer-pid", "5"])
