"""Go/no-go (ADR 0025 rule 6): GO only if the newest level-4 report carrying the
running deployment's fingerprint, bound to a preflight with the same digest and
newer than the last `demo.sh start`, has no FAIL that a human did not accept
for this fingerprint (rule 7).

Read-only: this module never writes a file and never touches a unit. It says
which acceptance verification it did and never claims more."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from demo.acceptance import acceptances, fingerprint, report  # noqa: E402

_VERIFICATION_TEXT = {
    "author-checked": ("the acceptance commit's git author and committer are not a known agent "
                       "identity; git metadata is forgeable on a single-user host, so this does "
                       "not prove a human wrote it"),
    "signature-checked": ("the acceptance commit has a valid SSH signature from the allowed-signers "
                          "file; this proves possession of that key, not that a human used it"),
}


def _num(x):
    """A finite real number, else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return x


def _reports(reports_dir: Path):
    """-> (reports newest first, problems). An unloadable file is a problem, never skipped."""
    out, problems = [], []
    for p in sorted(Path(reports_dir).glob("*/report.json")):
        label = f"{p.parent.name}/{p.name}"
        try:
            r = report.load(p)
        except (OSError, ValueError) as exc:
            problems.append(f"report {label} unreadable ({type(exc).__name__})")
            continue
        if not isinstance(r, dict) or not isinstance(r.get("run_id"), str) or not r["run_id"]:
            problems.append(f"report {label} has no run_id")
            continue
        out.append(r)
    out.sort(key=lambda r: _num(r.get("started_at")) if _num(r.get("started_at")) is not None
             else float("-inf"), reverse=True)
    return out, problems


def _check_problem(run, pre):
    """Reason the matched run cannot decide, or None."""
    rid = run["run_id"]
    if run["preflight_run_id"] == rid:
        return f"{rid} names itself as its preflight"
    if pre.get("preflight_run_id"):
        return f"preflight {pre['run_id']} is itself a level-4 run"
    pchecks = pre.get("checks")
    if not isinstance(pchecks, list) or not any(
            isinstance(c, dict) and c.get("id") == "preflight.fingerprint" and c.get("status") == "PASS"
            for c in pchecks):
        return f"preflight {pre['run_id']} has no PASS preflight.fingerprint check"
    checks = run.get("checks")
    if not isinstance(checks, list):
        return f"{rid} has missing or non-list checks"
    for c in checks:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            return f"{rid} has a malformed check"
        if c.get("status") not in report.STATUSES:
            return f"{rid} check {c['id']} has missing or unknown status"
    if not any(c["id"] == "fingerprint.bound" and c["status"] == "PASS" for c in checks):
        return f"level-4 report not bound to a preflight ({rid})"
    if not any(c["status"] == "PASS" and c["id"] != "fingerprint.bound" for c in checks):
        return f"level-4 report exercised nothing ({rid})"
    return None


def _digest(r) -> str | None:
    fp = (r or {}).get("fingerprint")
    return fp.get("digest") if isinstance(fp, dict) else None


def _changed_since(current, r) -> list:
    fp = r["fingerprint"]
    old = fingerprint.Fingerprint(fp.get("digest", ""), fp.get("parts") or {})
    return old.changed(current)


def _marker_at(marker: Path):
    """-> (at, None) or (None, reason). A marker that was asked for must be readable."""
    try:
        at = json.loads(Path(marker).read_text(encoding="utf-8"))["at"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"start marker {Path(marker).name} unreadable ({type(exc).__name__})"
    if _num(at) is None:
        return None, f"start marker {Path(marker).name} has no finite numeric 'at'"
    return at, None


def decide(current, reports_dir, accepted, marker=None):
    all_reports, problems = _reports(reports_dir)
    if problems:
        return False, ["NO-GO: " + "; ".join(problems)]
    ids = [r["run_id"] for r in all_reports]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        return False, ["NO-GO: duplicate run id " + ", ".join(dups)]
    by_id = {r["run_id"]: r for r in all_reports}
    outside = [r for r in all_reports if r.get("preflight_run_id")]
    match = [r for r in outside
             if _digest(r) == current.digest
             and _digest(by_id.get(r["preflight_run_id"])) == current.digest]
    if not match:
        if outside and isinstance(outside[0].get("fingerprint"), dict):
            changed = _changed_since(current, outside[0])
            return False, [f"NO-GO: deployment changed since {outside[0]['run_id']}: "
                           + (", ".join(changed) or "fingerprint differs")]
        return False, ["NO-GO: no level-4 report bound to a preflight"]
    latest = {}  # newest matching report per tool: a rerun of one tool may clear its own FAIL
    for r in match:  # match is newest first
        tool = r.get("tool")
        if not isinstance(tool, str) or not tool:
            return False, [f"NO-GO: report {r['run_id']} has no tool"]
        latest.setdefault(tool, r)
    runs = list(latest.values())
    at = None
    if marker is not None:
        at, why = _marker_at(Path(marker))
        if why:
            return False, [f"NO-GO: {why}"]
    pres = {}
    for run in runs:
        why = _check_problem(run, by_id[run["preflight_run_id"]])
        if why:
            return False, [f"NO-GO: {why}"]
        started = _num(run.get("started_at"))
        if started is None:
            return False, [f"NO-GO: {run['run_id']} has no finite numeric started_at"]
        if at is not None and started < at:
            return False, [f"NO-GO: {run['run_id']} is older than the last demo.sh start"]
        pres[run["preflight_run_id"]] = by_id[run["preflight_run_id"]]
    bound = {a.check_id: a for a in accepted if a.fingerprint == current.digest}
    notes, open_fails, used = [], [], []
    for owner in [*pres.values(), *runs]:
        for c in owner["checks"]:
            if c.get("status") != "FAIL":
                continue
            a = bound.get(c["id"])
            if a:
                used.append(a)
                notes.append(f"FAIL {c['id']} accepted by {a.accepted_by} ({a.verification})")
            else:
                open_fails.append(c["id"])
    if used:
        for v in sorted({a.verification for a in used}):
            notes.append(f"acceptance verification: {v}: "
                         + _VERIFICATION_TEXT.get(v, "unknown verification level; treat as unchecked"))
    if open_fails:
        return False, ["NO-GO: open FAIL " + ", ".join(open_fails)] + notes
    head = ", ".join(f"{r['run_id']} (preflight {r['preflight_run_id']})" for r in runs)
    return True, [f"GO: {head}"] + notes


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="python -m demo.acceptance.gate")
    ap.add_argument("--reports", required=True)
    ap.add_argument("--acceptances", required=True)
    ap.add_argument("--agent-identity", action="append", default=[])
    ap.add_argument("--allowed-signers")
    ap.add_argument("--marker", required=True,
                    help="start marker file written by demo.sh start")
    ap.add_argument("--factory", default=str(_REPO_ROOT / "FACTORY.yaml"))
    ap.add_argument("--consumer-unit", required=True, help="consumer unit whose WorkingDirectory is the deployed repo")
    ap.add_argument("--consumer-pid", type=int)
    ap.add_argument("--proc-root", default="/proc")
    ap.add_argument("--template")
    ap.add_argument("--home", default=str(Path.home()))
    a = ap.parse_args(argv)
    from demo.acceptance import preflight  # host-side discovery lives there
    try:
        current = preflight.current_fingerprint(
            consumer_unit=a.consumer_unit, consumer_pid=a.consumer_pid,
            proc_root=Path(a.proc_root), template=a.template, home=Path(a.home))
    except RuntimeError as exc:
        print(f"NO-GO: running deployment fingerprint not computable: {exc}")
        return 1
    try:
        agent_ids = acceptances.agent_identities(Path(a.factory), a.agent_identity)
    except Exception as exc:  # any unreadable or malformed FACTORY.yaml
        agent_ids = set()  # fail closed: with no known agent identity no acceptance counts
        print(f"note: {Path(a.factory).name} unreadable ({type(exc).__name__}); no acceptance can count")
    accepted, rejected = acceptances.load_verified(
        Path(a.acceptances), agent_ids=agent_ids,
        allowed_signers=Path(a.allowed_signers) if a.allowed_signers else None)
    ok, lines = decide(current, Path(a.reports), accepted, Path(a.marker))
    for ln in lines + [f"ignored acceptance: {r}" for r in rejected]:
        print(ln)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
