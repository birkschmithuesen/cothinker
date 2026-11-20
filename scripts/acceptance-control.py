#!/usr/bin/env python3
"""Positive control: every live-finding scenario is red on `<fix>^`, green on main,
and red again on main with the finding's mutation re-applied.

    acceptance-control.py [--fix SHA | --all] [--keep] [--json]

The parent tree is a throwaway `git worktree add --detach` under a
`cothinker-accept-ctl-*` temp root, always removed in `finally`. Nothing here
switches a branch, touches the live tree or stashes.

Exit 0 = every verdict holds, 1 = at least one is broken, 2 = refused or usage.
"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from acceptance import catalogue as cat_mod, components, mutation, paths, runner, safety, scenario   # noqa: E402


def _leg(sc, sut: Path, root: Path, mutation_name: str | None):
    """-> (state, note). state: "red" | "green" | "error". `error` is not `red`:
    the tree could not run the scenario at all (or the check was unverifiable)."""
    try:
        sut = safety.check_sut(sut)
        safety.check_out_root(root)
    except safety.UnsafePath:
        raise
    if sc.driver == "component" and (sc.component or {}).get("name") in components.SOURCING:
        script = Path(sut) / components.START_SCRIPT
        if not components.source_guard_present(script):
            return "error", "unverifiable: no source guard (script not sourced)"
    try:
        res = runner.run_scenario(sc, runner.RunConfig(
            level="a", sut=sut, root=root, mutation=mutation_name))
    except (safety.LiveMeeting, safety.UnsafePath):
        raise
    except (runner.UsageError, RuntimeError, OSError, mutation.MutationError) as e:
        return "error", f"{type(e).__name__}: {e}"[:300]
    return judge(res, sc)


def judge(res, sc):
    """Map one Result to a leg state through the catalogue's own bookkeeping."""
    for line in res.obs.log_lines or []:
        if "unrecognized arguments" in line:
            return "error", "consumer rejected a flag: " + line.strip()[:200]
    v = cat_mod.classify(res, sc)
    real = v["real"]
    hard = [f for f in real if not f.unverifiable]
    if hard:
        return "red", f"{hard[0].checkpoint_id}: {hard[0].kind}: {hard[0].detail}"[:300]
    if real:                      # only unverifiable failures: not a verdict
        return "error", f"unverifiable: {real[0].kind}: {real[0].detail}"[:300]
    if v["stale"]:                # an expectation that did not happen is red, as in the catalogue
        return "red", "stale expectation: " + ", ".join(v["stale"])
    exp = [f"{f.kind} ({k})" for k, f, _ in v["expected"]]
    return "green", ("expected: " + ", ".join(exp)) if exp else ""


def _remove(base: Path, wt: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                   cwd=paths.REPO, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=paths.REPO, capture_output=True)
    shutil.rmtree(base, ignore_errors=True)


def control(m, sc, keep: bool = False) -> dict:
    base = Path(tempfile.mkdtemp(prefix="cothinker-accept-ctl-"))
    wt = base / f"ctl-{m.fix[:12]}"
    notes: dict = {}
    try:
        safety.check_out_root(base)
        add = subprocess.run(["git", "worktree", "add", "--detach", str(wt), f"{m.fix}^"],
                             cwd=paths.REPO, capture_output=True, text=True)
        if add.returncode != 0:
            parent, notes["parent"] = "error", "git worktree add failed: " + add.stderr.strip()[:200]
        else:
            parent, notes["parent"] = _leg(sc, wt, base / "parent", None)
        main, notes["main"] = _leg(sc, paths.REPO, base / "main", None)
        mutated, notes["main_mutated"] = _leg(sc, paths.REPO, base / "mutated", m.name)
    finally:
        if keep:
            print(f"kept {base}", file=sys.stderr)
        else:
            _remove(base, wt)
    holds = parent == "red" and main == "green" and mutated == "red"
    return {"finding": m.finding, "fix": m.fix, "scenario": sc.id, "parent": parent,
            "main": main, "main_mutated": mutated,
            "verdict": "holds" if holds else "broken",
            "notes": {k: v for k, v in notes.items() if v}}


def render(r: dict) -> str:
    line = (f"finding {r['finding']} fix {r['fix']}: parent {r['parent']} · main {r['main']} · "
            f"main+mutation {r['main_mutated']} → {r['verdict']}")
    for k, v in r["notes"].items():
        if r["verdict"] != "holds" or "expected" in v:
            line += f"\n    {k}: {v}"
    return line


def select(fix: str | None, all_: bool) -> list:
    ms = sorted(mutation.REGISTRY.values(), key=lambda m: m.finding)
    if all_:
        return ms
    return [m for m in ms if m.fix.startswith(fix) or fix.startswith(m.fix)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fix", help="fix commit (every finding fixed by it)")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the worktree and run roots")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    paths.ensure_paths()
    ms = select(a.fix, a.all)
    if not ms:
        print(f"no mutation for fix {a.fix}", file=sys.stderr)
        return 2
    cat = {s.id: s for s in scenario.catalogue()}
    code = 0
    for m in ms:
        sc = cat.get(m.scenario)
        if sc is None:
            print(f"scenario {m.scenario} not in the catalogue", file=sys.stderr)
            return 2
        try:
            r = control(m, sc, a.keep)
        except (safety.LiveMeeting, safety.UnsafePath) as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        print(json.dumps(r, ensure_ascii=False) if a.json else render(r), flush=True)
        if r["verdict"] != "holds":
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
