"""Guard: every commit carries a DCO `Signed-off-by:` trailer (D1, CONTRIBUTING.md §2).

The CLA (CLA.md) and the DCO are both required for a contribution; the CLA is a one-time
text requirement checked by a human reviewer, the DCO trailer is checked here, on every
commit of a pull request, automatically. A mention of the words "Signed-off-by:" in prose
text does not count -- only a line of the exact trailer form does.

Usage: python scripts/check_dco.py [--base origin/main]
Exit 1 with one line per commit missing the trailer.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

SIGNOFF_RE = re.compile(r"^Signed-off-by: .+ <\S+@\S+>$", re.MULTILINE)


def has_signoff(message: str) -> bool:
    """True if `message` has a `Signed-off-by: Name <email>` trailer on its own line."""
    return bool(SIGNOFF_RE.search(message))


def violations(commits: list[tuple[str, str]]) -> list[str]:
    """`commits` is a list of (sha, message). Return one line per commit missing the trailer."""
    found = []
    for sha, message in commits:
        if not has_signoff(message):
            subject = message.splitlines()[0] if message.splitlines() else ""
            found.append(f"{sha}: missing Signed-off-by: trailer ({subject})")
    return found


def _commits_since(base: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", f"{base}..HEAD", "--format=%H%x00%B%x01"],
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


def main(argv: list[str] | None = None) -> int:
    description = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--base", default="origin/main", help="base revision (default: origin/main)")
    args = parser.parse_args(argv)
    found = violations(_commits_since(args.base))
    for line in found:
        print(line)
    if found:
        print("Add a Signed-off-by: trailer with `git commit -s` (CONTRIBUTING.md).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
