#!/usr/bin/env python3
"""Acceptance loop CLI. `run` executes one scenario (level a: real consumer + stage, fakes around).

Exit 0 = green, 1 = red, 2 = refused or usage error.
"""
from __future__ import annotations
import argparse, json, os, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from acceptance import catalogue as cat_mod, compare, counters, metrics, paths, runner, safety, scenario, stt_stack, verlauf   # noqa: E402


def cmd_run(a) -> int:
    paths.ensure_paths()
    sc = next((s for s in scenario.catalogue() if s.id == a.scenario), None)
    if sc is None:
        print(f"unknown scenario: {a.scenario}", file=sys.stderr)
        return 2
    root = Path(a.root) if a.root else runner.default_root(a.level, a.dry_run)
    try:
        res = runner.run_scenario(sc, runner.RunConfig(
            level=a.level, sut=Path(a.sut or paths.REPO), root=root,
            mutation=a.mutation, keep=a.keep, browser=a.browser,
            dry_run=a.dry_run, session_index=a.session))
    except (safety.LiveMeeting, safety.UnsafePath, safety.PaidRefused) as e:   # Task 20's SpendGate must subclass PaidRefused
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except (runner.UsageError, stt_stack.StackError) as e:
        print(f"usage: {e}", file=sys.stderr)
        return 2
    if isinstance(res, dict):                      # --dry-run plan of level b/c
        if not a.root and runner._removable(root):
            shutil.rmtree(root, ignore_errors=True)
        print(json.dumps(res, ensure_ascii=False, default=str) if a.json else runner.render_plan(res))
        return 0
    if a.record:
        record(res, a, cat_mod.classify(res, sc))
    if a.doc:
        write_doc(a.doc, a.set or f"level-{a.level}", [res], level=a.level)
    print(json.dumps(runner.summary(res), ensure_ascii=False) if a.json else runner.render(res))
    return 0 if res.ok else 1


def cmd_demo_screens(a) -> int:
    """Shoot the synthetic demo stage (scripts/demo-stage.py, token `demo`) in both viewports."""
    if a.profile == "workshop":
        return _demo_screens_workshop(a)
    paths.ensure_paths()
    from acceptance import browser, repo_safe
    import run_alpha
    why = browser.unavailable_reason()
    if why:
        print(f"nicht_pruefbar: {why}", file=sys.stderr)
        return 2
    out = Path(a.out)
    port = safety.pick_safe_port(18760)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen([sys.executable, str(paths.REPO / "scripts" / "demo-stage.py"), "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        url = f"http://127.0.0.1:{port}/?t=demo"
        if not run_alpha.wait_http(url, 30):
            print("demo stage did not come up", file=sys.stderr)
            return 1
        counters_out = {}
        with browser.Session(url, out) as sess:
            time.sleep(1.5)                                # one poll fills the zones the first paint leaves empty
            for vp in ("desktop", "phone"):
                print(sess.shoot("demo", vp))
            for vp in ("desktop", "phone"):
                counters_out[vp] = sess.counters(vp)
            blocked = list(sess.blocked)
        counters_out["blocked_requests"] = blocked
        text = json.dumps(counters_out, ensure_ascii=False, indent=2)
        repo_safe.assert_repo_safe(text, "counters.json")
        (out / "counters.json").write_text(text + "\n", encoding="utf-8")
        ph = counters_out["phone"]
        print(f"counters: horizontal_scroll={ph['horizontal_scroll']} "
              f"small_tap_targets={ph['small_tap_targets']} dead_buttons={ph['dead_buttons']}")
        return 0
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass


def _demo_screens_workshop(a) -> int:
    """S8: the table phones in 27 demo states (fake table hub, invented data), 390x844 only."""
    paths.ensure_paths()
    from acceptance import browser, repo_safe, workshop_demo
    out = Path(a.out)
    try:
        res = workshop_demo.run(out)
    except browser.BrowserUnavailable as e:
        print(f"nicht_pruefbar: {e}", file=sys.stderr)
        return 2
    text = json.dumps(res["counters"], ensure_ascii=False, indent=2)
    repo_safe.assert_repo_safe(text, "counters.json")
    (out / "counters.json").write_text(text + "\n", encoding="utf-8")
    for line in res["lines"]:
        print(line)
    for p in res["problems"]:
        print(f"FAIL {p}", file=sys.stderr)
    return 1 if res["problems"] else 0


DOC_FORMAT = "cothinker-acceptance-compare/1"


def write_doc(path, set_name: str, results, *, sut=None, level: str = "a") -> dict:
    """Merge one measured set into a before/after document for `compare` (numbers only).
    A catalogue run (`sut` given) also scores the regression corpus on that tree; a failing
    scorer leaves no false-trigger value, which the gate reads as unverifiable."""
    from acceptance import corpus_control, sets as sets_mod
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        doc = {}
    c = counters.compute(results)
    levels = sorted({*(doc.get("levels") or ([doc["level"]] if doc.get("level") else [])), level})
    doc.update({"format": DOC_FORMAT, "levels": levels, "level": levels[-1]})   # order-independent: highest level wins
    doc.setdefault("sets", {})[set_name] = {m: c[m] for m in counters.DIRECTION if c.get(m) is not None}
    if sut is not None:
        try:
            doc["regression_corpus"] = corpus_control.score(Path(sut))
            if doc["regression_corpus"].get("negatives") == 0:      # no negatives: the rate is unverifiable
                doc["regression_corpus"] = {"error": "regression corpus has no negative cases"}
        except Exception as e:
            doc["regression_corpus"] = {"error": str(e)[:120]}
        try:
            doc["heldout"] = list(sets_mod.load().heldout)
        except ValueError:
            doc["heldout"] = []
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def cmd_catalogue(a) -> int:
    """Run every scenario defined for the level; exit 0 iff nothing is red beyond the known expectations."""
    paths.ensure_paths()
    scs = [s for s in scenario.catalogue() if a.level in s.levels]
    rows, results, code = [], [], 0
    for sc in scs:
        root = Path(tempfile.mkdtemp(prefix="cothinker-accept-"))
        res = None
        for attempt in (1, 2):                         # a service that does not come up gets one more try
            try:
                res = runner.run_scenario(sc, runner.RunConfig(
                    level=a.level, sut=Path(a.sut or paths.REPO), root=root))
                break
            except (safety.LiveMeeting, safety.UnsafePath) as e:
                print(f"refused: {e}", file=sys.stderr)
                return 2
            except runner.UsageError as e:
                print(f"usage: {e}", file=sys.stderr)
                return 2
            except RuntimeError as e:                  # setup failure
                print(f"{sc.id:34} setup error (attempt {attempt}): {e}", file=sys.stderr, flush=True)
                if "did not come up" not in str(e):    # only the known flake is retried
                    break
                root = Path(tempfile.mkdtemp(prefix="cothinker-accept-"))
                time.sleep(3)
        if res is None:                                # red, keep going
            code = 1
            continue
        print(f"... {sc.id} done ({res.metrics.get('duration_s')}s)", file=sys.stderr, flush=True)
        results.append(res)
        v = cat_mod.classify(res, sc)
        rows.append((sc, res, v))
        if not cat_mod.passes(v["status"]):
            code = 1
    if a.record and results:
        record_all(results, a, [v for _, _, v in rows])
    if a.doc and results:
        write_doc(a.doc, a.set or "synthetic", results, sut=Path(a.sut or paths.REPO), level=a.level)
    if a.json:
        print(json.dumps({"level": a.level, "ok": code == 0, "scenarios": [
            {"scenario": sc.id, "status": v["status"], "triage": sc.triage,
             "failures": [vars(f) for f in v["real"]],
             "expected": [{"kind": k, "checkpoint": f.checkpoint_id, "assertion": f.kind, "reason": why}
                          for k, f, why in v["expected"]],
             "stale": v["stale"], "duration_s": res.metrics.get("duration_s")}
            for sc, res, v in rows]}, ensure_ascii=False))
    else:
        for sc, res, v in rows:
            label = {"GREEN": "GREEN",
                     "RED-EXPECTED": "RED (expected: " + ", ".join(
                         f"{v['scenario']}/{f.checkpoint_id}" for k, f, _ in v["expected"] if k == "red")
                         + f", triage {sc.triage})",
                     "UNVERIFIABLE-EXPECTED": "UNVERIFIABLE (expected: " + ", ".join(
                         f.kind for _, f, _ in v["expected"]) + ")",
                     "RED": "RED"}[v["status"]]
            print(f"{sc.id:34} {label}")
            for f in v["real"]:
                print(f"    - {f.checkpoint_id}: {f.kind}: {f.detail}")
            for s in v["stale"]:
                print(f"    - stale expectation, it did not happen: {s}")
        green = sum(1 for _, _, v in rows if v["status"] == "GREEN")
        print(f"{len(rows)} scenarios, {green} GREEN, exit {code}")
    return code


def cmd_compare(a) -> int:
    docs = []
    for name in (a.before, a.after):
        try:
            docs.append(json.loads(Path(name).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"cannot read {name}: {e}", file=sys.stderr)
            return 2
    before, after = docs
    heldout = set(a.heldout or after.get("heldout") or before.get("heldout") or [])
    code, msgs = compare.gate(before, after, verlauf.load(verlauf.host_home() / "verlauf.jsonl"), heldout)
    for m in msgs:
        print(m)
    if code == 0:
        print("held-out compared, no regression; no regression-corpus false trigger")
    return code


def cmd_sync(a) -> int:
    print(f"synced {verlauf.sync()} line(s) into {verlauf.REPO_PATH.relative_to(paths.REPO)}")
    return 0


def record(res, a, verdict=None) -> None:
    c = counters.compute([res])
    # level a is the synthetic fake run; b/c default to a host-only label that sync never commits
    set_name = a.set or ("synthetic" if a.level == "a" else f"level-{a.level}")
    line = metrics.run_line([res], c, level=a.level, set_name=set_name, head=runner._git_head(paths.REPO),
                            sut_head=res.sut_head, judge=None, cost={},
                            verdicts=[verdict] if verdict else None)
    verlauf.append(line, verlauf.host_home() / "verlauf.jsonl")


def record_all(results, a, verdicts=None) -> None:
    c = counters.compute(results)
    set_name = a.set or ("synthetic" if a.level == "a" else f"level-{a.level}")
    line = metrics.run_line(results, c, level=a.level, set_name=set_name, head=runner._git_head(paths.REPO),
                            sut_head=results[0].sut_head, judge=None, cost={}, verdicts=verdicts)
    verlauf.append(line, verlauf.host_home() / "verlauf.jsonl")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one scenario")
    r.add_argument("--scenario", required=True)
    r.add_argument("--level", default="a", choices=("a", "b", "c"))
    r.add_argument("--sut", default="", help="CoThinker tree under test (default: this repo)")
    r.add_argument("--mutation", default=None)
    r.add_argument("--root", default="", help="output root (default: a fresh $TMPDIR/cothinker-accept-* for level a, $COTHINKER_ACCEPT_HOME/runs/<date>/ for b/c)")
    r.add_argument("--keep", action="store_true")
    r.add_argument("--dry-run", action="store_true",
                   help="levels b/c: print the plan (commands, ports, env names, minutes, EUR), start nothing")
    r.add_argument("--session", type=int, default=0, help="level c: index into $COTHINKER_ACCEPT_HOME/sessions.json")
    r.add_argument("--json", action="store_true")
    r.add_argument("--browser", action="store_true",
                   help="own headless Chromium on the stage: screenshots per checkpoint, DOM asserts, watcher")
    r.add_argument("--set", default="", help="curve set label (default: synthetic for level a, level-b/level-c otherwise)")
    r.add_argument("--record", action="store_true", help="append the run line to the host curve")
    r.add_argument("--doc", default="", help="merge this run's numbers as set --set into a compare document")
    r.set_defaults(fn=cmd_run)
    k = sub.add_parser("catalogue", help="run every scenario of a level (known expectations from acceptance/catalogue.py)")
    k.add_argument("--level", default="a", choices=("a",))
    k.add_argument("--sut", default="", help="CoThinker tree under test (default: this repo)")
    k.add_argument("--json", action="store_true")
    k.add_argument("--set", default="")
    k.add_argument("--record", action="store_true", help="append one run line to the host curve")
    k.add_argument("--doc", default="", help="write a compare document (set numbers + regression corpus score)")
    k.set_defaults(fn=cmd_catalogue)
    c = sub.add_parser("compare", help="before/after gate: exit 1 on held-out regression or corpus FP")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--heldout", nargs="*", default=None, help="held-out set ids (default: from the files)")
    c.set_defaults(fn=cmd_compare)
    d = sub.add_parser("demo-screens", help="screenshots + page counters of the synthetic demo stage, "
                                            "or the table phones (--profile workshop)")
    d.add_argument("--out", required=True, help="output directory (synthetic content only)")
    d.add_argument("--profile", choices=("meeting", "workshop"), default="meeting",
                   help="meeting: the demo stage, both viewports (default); workshop: the table phones at 390x844")
    d.set_defaults(fn=cmd_demo_screens)
    sy = sub.add_parser("sync-verlauf", help="copy synthetic/numbers-only host lines into the repo curve")
    sy.set_defaults(fn=cmd_sync)
    a = ap.parse_args(argv)
    # Python's default for SIGTERM is to die on the spot -- no `finally`
    # runs at all, so runner.py's own cleanup (svc.stop_all(), the real
    # STT stack's --stop) never fires. Turned into a KeyboardInterrupt
    # (tests/alpha/run_alpha.py's main() does the same), it unwinds
    # through every `finally` on its way up instead.
    saved = {s: signal.signal(s, _interrupt)
             for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        print("interrupted -- services stopped", file=sys.stderr)
        return 130
    finally:
        for s, h in saved.items():
            signal.signal(s, h)


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


if __name__ == "__main__":
    sys.exit(main())
