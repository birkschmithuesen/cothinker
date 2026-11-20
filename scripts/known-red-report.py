#!/usr/bin/env python3
# CoThinker — known_red.json quarantine report
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Prints a human-readable summary of `tests/known_red.json`'s current
state: total entry count, the oldest entry, every expired entry, the count
per owner and its expiry clusters. Read-only, no CLI flags.

    python3 scripts/known-red-report.py

Expiry clusters (see the quarantine spec, GATE Q9): every `expires` date
holding more than 25% of all entries is a cliff -- on that one day every
one of them becomes a new failure at once. Flags it, does not fix it;
`scripts/known-red-expiry.py`'s rule B is the fix, applied only once Q9 is
answered (never silently from this report).
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

KNOWN_RED_PATH = Path(__file__).resolve().parent.parent / "tests" / "known_red.json"
CLUSTER_THRESHOLD_PCT = 25


def expiry_cliff_lines(entries: list[dict]) -> list[str]:
    total = len(entries)
    if not total:
        return []
    counts = Counter(e["expires"] for e in entries)
    lines = []
    for expires, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = 100 * n / total
        if pct > CLUSTER_THRESHOLD_PCT:
            pct_str = f"{pct:.0f}" if pct == int(pct) else f"{pct:.1f}"
            lines.append(f"expiry cliff: {expires} holds {n}/{total} ({pct_str} %)")
    return lines


def main() -> int:
    entries = json.loads(KNOWN_RED_PATH.read_text(encoding="utf-8"))
    today = date.today()

    print(f"{len(entries)} entries in {KNOWN_RED_PATH}")

    oldest = min(entries, key=lambda e: e["added"])
    print(f"\noldest: {oldest['added']}  {oldest['file']} :: {oldest['check']}")

    expired = sorted(
        (e for e in entries if e["expires"] < today.isoformat()),
        key=lambda e: e["expires"])
    print(f"\nexpired ({len(expired)}):")
    for e in expired:
        print(f"  {e['expires']}  {e['file']} :: {e['check']}")

    per_owner: dict[str, int] = {}
    for e in entries:
        per_owner[e["owner"]] = per_owner.get(e["owner"], 0) + 1
    print(f"\nby owner ({len(per_owner)}):")
    for owner, count in sorted(per_owner.items(), key=lambda kv: -kv[1]):
        print(f"  {owner}: {count}")

    cliffs = expiry_cliff_lines(entries)
    print(f"\nexpiry clusters ({len(cliffs)}):")
    for line in cliffs:
        print(f"  {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
