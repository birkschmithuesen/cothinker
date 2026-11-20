#!/usr/bin/env python3
"""Count pattern-class matches across commit history.

For every instance and operator class defined in rules.toml, counts how many
lines of `git log -p --no-color <ref>` match that class. Reuses classify.py's
rule loading and operator_hits() -- no pattern regex is duplicated here.

Usage:
    python3 history_scan.py --ref <ref> [--repo <path>] [--rules <rules.toml>]

Output: one line per class, `pattern<TAB>lines`, sorted by class name, to
stdout. A class with zero matching lines is still printed, with 0.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import classify as c  # noqa: E402


def class_names(rules: "c.Rules") -> list[str]:
    names = {name for name, _, _ in rules.instances}
    names |= {cid for cid, _ in rules.operator_classes}
    names |= {tid for tid, _ in rules.operator_tokens.values()}
    return sorted(names)


def count_lines(lines, rules: "c.Rules") -> dict[str, int]:
    counts = {name: 0 for name in class_names(rules)}
    for line in lines:
        for name, _, content_rx in rules.instances:
            if any(rx.search(line) for rx in content_rx):
                counts[name] += 1
        for cid in c.operator_hits(line, rules):
            if cid in counts:
                counts[cid] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", required=True, help="commit/ref whose history to scan")
    ap.add_argument("--repo", default=".", help="git repository (default .)")
    ap.add_argument("--rules", default=str(HERE / "rules.toml"))
    args = ap.parse_args(argv)

    rules = c.load_rules(args.rules)
    log = subprocess.run(
        ["git", "-C", args.repo, "log", "-p", "--no-color", args.ref],
        check=True, capture_output=True, text=True, errors="replace",
    ).stdout
    counts = count_lines(log.splitlines(), rules)
    for name in sorted(counts):
        print(f"{name}\t{counts[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
