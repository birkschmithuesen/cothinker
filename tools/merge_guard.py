#!/usr/bin/env python3
# CoThinker — merge-time publish gate
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Guard a merge of a candidate branch into a base branch (normally `main`).

Refuses the merge (non-zero exit, no merge performed) if either:

  1. `tools/publish_check.py` finds a hit on the candidate's tree/history, or
  2. any commit in `<base>..<candidate>` carries an internal work-item id
     (the `card-id` pattern from `publish/denylist-public.txt`, reused here
     rather than re-typed) in its commit message.

Only on a clean candidate does this script perform the merge (checkout
`<base>`, `git merge --no-ff --no-edit <candidate>`).

Never prints a matched denylist value, only counts and commit SHAs.

    python3 tools/merge_guard.py --repo <dir> --base main --candidate <branch>
    [--private-list GLOB]

Exit: 0 merged, 1 refused.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
PUBLISH_CHECK = HERE / "tools" / "publish_check.py"
PUBLIC_DENYLIST = HERE / "publish" / "denylist-public.txt"


def card_id_regex(denylist_path=PUBLIC_DENYLIST):
    """Return the `card-id` regex from publish/denylist-public.txt (not re-typed here).

    Classes in that file are blank-line-separated blocks of comments followed
    by one or more `re:`/literal lines; find the block whose comment names
    `card-id` and take its first `re:` line.
    """
    lines = pathlib.Path(denylist_path).read_text(encoding="utf-8").splitlines()
    start = next(i for i, raw in enumerate(lines) if raw.strip().startswith("# card-id"))
    for raw in lines[start:]:
        line = raw.strip()
        if not line:
            break
        if line.startswith("re:"):
            return re.compile(line[3:])
    raise RuntimeError("card-id pattern not found in publish/denylist-public.txt")


def new_commits(repo, base, candidate):
    """(short_sha, message) for every commit in `base..candidate`."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", f"{base}..{candidate}", "--format=%H%x00%B%x01"],
        check=True, capture_output=True, text=True,
    ).stdout
    commits = []
    for entry in out.split("\x01"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, _, message = entry.partition("\x00")
        commits.append((sha[:12], message))
    return commits


def card_id_violations(repo, base, candidate, pattern):
    return [sha for sha, message in new_commits(repo, base, candidate) if pattern.search(message)]


def run_publish_check(repo, private_lists):
    cmd = [sys.executable, str(PUBLISH_CHECK), "--repo", str(repo), "--skip-detect-secrets"]
    if private_lists:
        for p in private_lists:
            cmd += ["--private-list", p]
    else:
        cmd.append("--public-only")
    return subprocess.run(cmd, capture_output=True, text=True)


def perform_merge(repo, base, candidate):
    subprocess.run(["git", "-C", str(repo), "checkout", base], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", candidate],
                    check=True, capture_output=True)


def check(repo, base, candidate, private_lists=()):
    """Return (ok, message). `message` never contains a matched denylist value."""
    violations = card_id_violations(repo, base, candidate, card_id_regex())
    if violations:
        return False, (f"refused: {len(violations)} new commit(s) on {candidate!r} carry an "
                        f"internal work-item id: {', '.join(violations)}")

    result = run_publish_check(repo, private_lists)
    if result.returncode != 0:
        return False, f"refused: tools/publish_check.py found hits on {candidate!r} (exit {result.returncode})"

    return True, f"ok: {candidate!r} is clean to merge into {base!r}"


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--repo", default=".")
    a.add_argument("--base", default="main")
    a.add_argument("--candidate", required=True)
    a.add_argument("--private-list", action="append", default=[])
    o = a.parse_args(argv)

    ok, message = check(o.repo, o.base, o.candidate, o.private_list)
    if ok:
        perform_merge(o.repo, o.base, o.candidate)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
