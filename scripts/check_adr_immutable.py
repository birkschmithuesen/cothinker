"""Guard: an accepted ADR is never rewritten, only superseded (docs/adr/README.md).

Compares every ADR under docs/adr/ against a base revision (default: origin/main) and reports
changes to ADRs that were already accepted at the base. Allowed changes:

- the status line (`- **Status:** ...`), e.g. "superseded by ADR 0024" or "amended by ADR 0020";
- added lines starting with `Clarification:` or `Consequence (added ` (the GDS-Way exception for
  clarifications and newly discovered consequences);
- anything in an ADR whose status at the base is still `proposed`;
- new ADR files.

Deleting an accepted ADR is a violation.

Usage: python scripts/check_adr_immutable.py [--base origin/main]
Exit 1 with one line per violation (file, line, reason).
"""
from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from pathlib import Path

ADR_DIR = "docs/adr"
STATUS_PREFIX = "- **Status:**"
ALLOWED_ADDED_PREFIXES = ("Clarification:", "Consequence (added ")


def is_accepted(text: str) -> bool:
    """True if the ADR's status line says anything other than 'proposed'."""
    for line in text.splitlines():
        if line.startswith(STATUS_PREFIX):
            return "proposed" not in line.split(STATUS_PREFIX, 1)[1].lower()
    return True  # no status line: treat as accepted (safe default)


def violations(name: str, base_text: str, new_text: str | None) -> list[str]:
    """Return violation messages for one ADR file. new_text None = file deleted."""
    if not is_accepted(base_text):
        return []
    if new_text is None:
        return [f"{name}: accepted ADR deleted; mark it superseded instead"]
    found = []
    diff = difflib.unified_diff(base_text.splitlines(), new_text.splitlines(), lineterm="", n=0)
    for line in diff:
        if line.startswith(("---", "+++", "@@")):
            continue
        body = line[1:]
        if body.startswith(STATUS_PREFIX):
            continue
        if line.startswith("+") and (body.strip() == "" or body.lstrip("> ").startswith(ALLOWED_ADDED_PREFIXES)):
            continue
        if line.startswith("-") and body.strip() == "":
            continue
        kind = "added" if line.startswith("+") else "removed"
        found.append(f"{name}: {kind} line in accepted ADR: {body.strip()[:80]!r}")
    return found


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def check_repo(base: str) -> list[str]:
    merge_base = _git("merge-base", base, "HEAD").strip()
    try:
        listing = _git("ls-tree", "--name-only", f"{merge_base}:{ADR_DIR}")
    except subprocess.CalledProcessError:
        # ADR_DIR did not exist yet at the base revision: nothing to protect.
        listing = ""
    base_files = [f for f in listing.split() if f.endswith(".md")]
    found = []
    for fname in base_files:
        if fname in ("README.md", "0000-template.md"):
            continue
        base_text = _git("show", f"{merge_base}:{ADR_DIR}/{fname}")
        path = Path(ADR_DIR) / fname
        new_text = path.read_text(encoding="utf-8") if path.exists() else None
        found += violations(str(path), base_text, new_text)
    return found


def main(argv: list[str] | None = None) -> int:
    description = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--base", default="origin/main", help="base revision (default: origin/main)")
    args = parser.parse_args(argv)
    found = check_repo(args.base)
    for line in found:
        print(line)
    if found:
        print("Write a new ADR that supersedes the old one (docs/adr/README.md).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
