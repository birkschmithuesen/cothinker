#!/usr/bin/env python3
"""Count lines per path attributed to authors outside a private allow-list.

Allow-list: every non-empty, non-comment line of every
~/.config/cothinker/authors.d/*.txt file (one author name or email per
line). Never created or committed by this tool -- a missing directory
means an empty allow-list, i.e. every author counts as foreign.

Usage:
    python3 blame_scan.py --paths-from <file> [--repo <path>] [--ref <ref>]

Output: one line per path with at least one foreign-authored line,
`path<TAB>foreign_lines`, to stdout. A path where every line is
allow-listed is omitted entirely.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

ALLOWLIST_GLOB = "~/.config/cothinker/authors.d/*.txt"


def load_allowlist(pattern: str = ALLOWLIST_GLOB) -> set[str]:
    authors: set[str] = set()
    for name in sorted(glob.glob(os.path.expanduser(pattern))):
        with open(name, encoding="utf-8") as fh:
            for line in fh:
                value = line.strip()
                if value and not value.startswith("#"):
                    authors.add(value)
    return authors


def foreign_line_count(path: str, allowlist: set[str], repo: str, ref: str) -> int:
    proc = subprocess.run(
        ["git", "-C", repo, "blame", "--line-porcelain", ref, "--", path],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0:
        return 0
    foreign = 0
    author = mail = None
    for line in proc.stdout.splitlines():
        if line.startswith("author "):
            author = line[len("author "):]
        elif line.startswith("author-mail "):
            mail = line[len("author-mail "):].strip("<>")
        elif line.startswith("\t"):
            if author not in allowlist and mail not in allowlist:
                foreign += 1
            author = mail = None
    return foreign


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paths-from", required=True, help="newline-separated list of paths")
    ap.add_argument("--repo", default=".", help="git repository (default .)")
    ap.add_argument("--ref", default="HEAD", help="commit/ref to blame (default HEAD)")
    ap.add_argument("--allowlist", default=ALLOWLIST_GLOB)
    args = ap.parse_args(argv)

    allowlist = load_allowlist(args.allowlist)
    with open(args.paths_from, encoding="utf-8") as fh:
        paths = [line.strip() for line in fh if line.strip()]

    for path in paths:
        n = foreign_line_count(path, allowlist, args.repo, args.ref)
        if n:
            print(f"{path}\t{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
