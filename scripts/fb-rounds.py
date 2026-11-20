#!/usr/bin/env python3
# CoThinker — fb-round counter
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Count, per fb<number> tag, how many commits reference it and flag the
ones that needed several rounds. Deterministic: `git log` plus a regex,
no LLM involved.

    python3 scripts/fb-rounds.py
    python3 scripts/fb-rounds.py --repo /path/to/other/repo --json

A commit counts toward an fb-number when "fb<digits>" appears in its
subject or body, UNLESS the commit itself is git-sync noise or a
WIP/index autocommit (AUTOCOMMIT_MARKERS below) — those are dropped
before counting, never averaged in.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROUND_THRESHOLD = 3  # >=3 commits on one fb-number means it needed several rounds

# Commit subjects starting with any of these (case-insensitive) are
# git-sync noise / WIP / index autocommits, not real work on the fb-number
# they happen to mention. Extend this list rather than special-casing.
AUTOCOMMIT_MARKERS = ("wip", "autocommit", "sync:", "index sync")

FB_TAG_RE = re.compile(r"\bfb(\d+)\b", re.IGNORECASE)

FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"
LOG_FORMAT = f"%H{FIELD_SEP}%ad{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}"


def is_autocommit(subject: str) -> bool:
    lowered = subject.strip().lower()
    return any(lowered.startswith(marker) for marker in AUTOCOMMIT_MARKERS)


def log_commits(repo: Path) -> list[tuple[str, str, str, str]]:
    """Returns (sha, date, subject, body) for every commit reachable from HEAD."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", f"--format={LOG_FORMAT}", "--date=short"],
        capture_output=True, text=True, check=True).stdout
    commits = []
    for record in out.split(RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, date, subject, body = record.split(FIELD_SEP, 3)
        commits.append((sha, date, subject, body))
    return commits


def build_rows(commits: list[tuple[str, str, str, str]]) -> list[dict]:
    by_fb: dict[str, list[tuple[str, str]]] = {}
    for sha, date, subject, body in commits:
        if is_autocommit(subject):
            continue
        tags = {f"fb{m.group(1)}" for m in FB_TAG_RE.finditer(subject + "\n" + body)}
        for tag in tags:
            by_fb.setdefault(tag, []).append((date, sha))

    rows = []
    for tag, entries in by_fb.items():
        if len(entries) < ROUND_THRESHOLD:
            continue
        entries.sort(key=lambda e: e[0])
        rows.append({
            "fb": tag,
            "commits": len(entries),
            "first": entries[0][0],
            "last": entries[-1][0],
            "shas": [sha[:7] for _, sha in entries],
        })
    rows.sort(key=lambda r: int(r["fb"][2:]))
    return rows


def render_table(rows: list[dict]) -> str:
    if not rows:
        return "no fb-number reached >=3 commits"
    lines = [f"{'fb#':<10} {'commits':<8} {'first':<12} {'last':<12} shas"]
    for r in rows:
        lines.append(f"{r['fb']:<10} {r['commits']:<8} {r['first']:<12} {r['last']:<12} "
                      f"{', '.join(r['shas'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=str(ROOT), help="git repo to scan (default: this repo)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    a = ap.parse_args(argv)

    rows = build_rows(log_commits(Path(a.repo)))
    print(json.dumps(rows, ensure_ascii=False) if a.json else render_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
