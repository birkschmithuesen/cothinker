# CoThinker — design reference-image gate: Design-Signoff guard (issue #61)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The coder must never update a reference PNG themselves -- only a commit
with its own Design-Signoff trailer may. `violating_commit()` is the pure
check for one commit's own (changed_paths, message) against the manifest's
protected paths; `scan_commits_since()` wraps it with real `git` subprocess
calls over a commit range (e.g. `main..HEAD`).

The trailer must be its own line, `Design-Signoff: #<issue>` (e.g.
`Design-Signoff: #61`) -- a mention of the words in prose text does not
count.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from . import reference_diff

REPO = Path(__file__).resolve().parents[2]
TRAILER_RE = re.compile(r"^Design-Signoff:\s*\S+", re.MULTILINE)


def has_signoff_trailer(message: str) -> bool:
    return bool(TRAILER_RE.search(message))


def violating_commit(changed_paths: set[str], message: str, protected: set[str]) -> Optional[set[str]]:
    """None if the commit is fine; otherwise the subset of `protected` it
    touched without the trailer."""
    touched = set(changed_paths) & set(protected)
    if not touched or has_signoff_trailer(message):
        return None
    return touched


def manifest_reference_paths(entries: list[reference_diff.ReferenceEntry], *,
                             repo: Path = REPO) -> set[str]:
    return {str(e.reference.relative_to(repo)) for e in entries}


def _resolve_ref(base_ref: str, *, repo: Path = REPO) -> str:
    """Resolve `base_ref` to a ref git can actually use.

    A shallow CI checkout (`actions/checkout`'s default: depth 1, only the
    PR branch ref fetched -- the `suite` job in tests.yml, unlike
    `dco.yml`/`publish-check.yml`/`adr-immutable.yml`, does not pass
    `fetch-depth: 0`) has no local `main` branch AND no `origin/main`
    remote-tracking ref either. Try the ref as given, then
    `origin/<base_ref>`, and only then fall back to fetching it from
    `origin` directly (a shallow `--depth 1` fetch, same as git itself
    would need to compute `main..HEAD` at all) -- that needs network
    access to `origin`, which a CI runner always has. Raise a clear error
    only if none of the three resolve.
    """
    for candidate in (base_ref, f"origin/{base_ref}"):
        probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet", candidate],
                               cwd=repo, capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    fetch = subprocess.run(["git", "fetch", "--depth", "1", "origin", base_ref],
                           cwd=repo, capture_output=True, text=True)
    if fetch.returncode == 0:
        probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "FETCH_HEAD"],
                               cwd=repo, capture_output=True, text=True)
        if probe.returncode == 0:
            return "FETCH_HEAD"
    raise ValueError(
        f"cannot resolve base ref {base_ref!r} (tried {base_ref!r}, "
        f"'origin/{base_ref}', and fetching it from origin); "
        f"fetch error: {fetch.stderr.strip()}")


def _commits_since(base_ref: str, *, repo: Path = REPO) -> list[str]:
    resolved = _resolve_ref(base_ref, repo=repo)
    out = subprocess.run(["git", "rev-list", f"{resolved}..HEAD"],
                         cwd=repo, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def _changed_paths(sha: str, *, repo: Path = REPO) -> set[str]:
    out = subprocess.run(["git", "show", "--name-only", "--pretty=format:", sha],
                         cwd=repo, capture_output=True, text=True, check=True)
    return {line for line in out.stdout.splitlines() if line.strip()}


def _commit_message(sha: str, *, repo: Path = REPO) -> str:
    out = subprocess.run(["git", "log", "-1", "--pretty=format:%B", sha],
                         cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout


def scan_commits_since(base_ref: str, protected: set[str], *, repo: Path = REPO) -> list[dict]:
    """Every commit in `base_ref..HEAD` that touches a protected path without
    the Design-Signoff trailer, as [{"sha": ..., "paths": {...}}, ...]."""
    violations = []
    for sha in _commits_since(base_ref, repo=repo):
        changed = _changed_paths(sha, repo=repo)
        touched = violating_commit(changed, _commit_message(sha, repo=repo), protected)
        if touched:
            violations.append({"sha": sha, "paths": touched})
    return violations
