#!/usr/bin/env python3
# CoThinker — check that every source module maps to exactly one target
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Checks `docs/architecture/blocks-inventory.md` against the code.

The inventory holds one fenced block per repository:

    ```inventory <repo>
    <path>  <target-id>
    ```

and one fenced ```targets block that defines every target id (a block, an
STT module part, a surface, core runtime, or `retire`).

For each repository this script lists the Python source files of the
scoped trees (tests excluded: any path with a `tests` segment, and
`conftest.py`), and reports

    <repo>@<ref>: <n> source files, <m> mapped, <u> unmapped, <s> stale, <x> unknown targets

`unmapped` = a source file with no inventory row, `stale` = an inventory
row whose file no longer exists, `unknown targets` = a row naming a target
id the targets block does not define. Every number but the first two must
be 0; the script then prints `OK` and exits 0, otherwise `FAIL` and the
offending paths, exit 1.

Usage (from the repo root):

    python3 docs/architecture/inventory_check.py
    python3 docs/architecture/inventory_check.py --table   # block table with line counts

Stdlib only; reads git objects, never the working tree, never writes.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
INVENTORY = HERE / "blocks-inventory.md"

# repo -> (ref, top-level directories in scope)
SCOPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "cothinker": ("HEAD", ("gateway", "stage", "shared", "stt", "graph", "modules",
                          "blocks", "templates", "surfaces")),
}

FENCE = re.compile(r"^```(inventory|targets)\s*([\w.-]*)\s*$")


def parse(text: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    targets: dict[str, str] = {}
    rows: dict[str, dict[str, str]] = defaultdict(dict)
    kind = repo = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = FENCE.match(line)
        if kind is None and m:
            kind, repo = m.group(1), m.group(2)
            continue
        if kind is not None and line.startswith("```"):
            kind = repo = None
            continue
        if kind is None or not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(None, 1)
        if kind == "targets":
            targets[parts[0]] = parts[1] if len(parts) > 1 else ""
        else:
            if len(parts) != 2:
                raise SystemExit(f"inventory {repo}: malformed row {line!r}")
            path, target = parts[0], parts[1].split()[0]
            if path in rows[repo]:
                raise SystemExit(f"inventory {repo}: {path} mapped twice")
            rows[repo][path] = target
    return targets, rows


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def sources(root: Path, ref: str, dirs: tuple[str, ...]) -> list[str]:
    # HEAD means "what would be committed next": read the index (which
    # `git add -N` registers a new path in) rather than the last commit,
    # so a newly added but uncommitted module is caught as unmapped too.
    if ref == "HEAD":
        out = git(root, "ls-files", "--", *dirs)
    else:
        out = git(root, "ls-tree", "-r", "--name-only", ref, "--", *dirs)
    keep = []
    for p in out.splitlines():
        segs = p.split("/")
        if p.endswith(".py") and "tests" not in segs[:-1] and segs[-1] != "conftest.py":
            keep.append(p)
    return sorted(keep)


def line_count(root: Path, ref: str, path: str) -> int:
    blob = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"],
                          check=True, capture_output=True).stdout
    return blob.count(b"\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", action="store_true",
                    help="print per-target file lists with line counts instead of checking")
    a = ap.parse_args()

    root = Path(git(HERE, "rev-parse", "--show-toplevel").strip())
    targets, rows = parse(INVENTORY.read_text(encoding="utf-8"))
    ok = True
    per_target: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for name, (ref, dirs) in SCOPES.items():
        listed = sources(root, ref, dirs)
        mapped = rows.get(name, {})
        unmapped = [p for p in listed if p not in mapped]
        stale = [p for p in mapped if p not in set(listed)]
        unknown = sorted({t for t in mapped.values() if t not in targets})
        if a.table:
            for p in listed:
                if p in mapped:
                    per_target[mapped[p]].append((name, p, line_count(root, ref, p)))
            continue
        print(f"{name}@{ref}: {len(listed)} source files, {len(listed) - len(unmapped)} mapped, "
              f"{len(unmapped)} unmapped, {len(stale)} stale, {len(unknown)} unknown targets")
        for label, items in (("unmapped", unmapped), ("stale", stale), ("unknown target", unknown)):
            for item in items:
                print(f"  {label}: {item}")
        ok = ok and not (unmapped or stale or unknown)
    if a.table:
        for tid in targets:
            files = per_target.get(tid, [])
            total = sum(n for _, _, n in files)
            cell = ", ".join(f"`{p}` {n}" for _, p, n in files)
            print(f"{tid}\t{total}\t{cell}")
        return 0
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
