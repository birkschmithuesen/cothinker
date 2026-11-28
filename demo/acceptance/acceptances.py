"""Human acceptances of a FAIL (ADR 0025 rule 7). Read-only: this module never writes files.

An entry counts only if (a) the file is committed and clean, (b) every line of
the entry is blamed to a commit whose (author name, email) is not an agent
identity, (c) with an allowed-signers file, each of those commits has a valid
SSH signature. On a single-user host an agent can forge (b) and, with an
unlocked key, (c); `verification` says what was checked, never more."""
from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml


def run_sh_in(argv, cwd):
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout


@dataclass(frozen=True)
class Acceptance:
    check_id: str
    fingerprint: str
    accepted_by: str
    reason: str
    verification: str


def agent_identities(factory_yaml: Path, extra) -> set:
    ids = set()
    ci = (yaml.safe_load(Path(factory_yaml).read_text(encoding="utf-8")) or {}).get("commit_identity") or {}
    if ci.get("name") and ci.get("email"):
        ids.add((ci["name"], ci["email"]))
    for s in extra:
        m = re.fullmatch(r"\s*(.+?)\s*<([^>]+)>\s*", s)
        if m:
            ids.add((m.group(1), m.group(2)))
    return ids


def _entry_ranges(lines):
    starts = [i for i, ln in enumerate(lines) if ln.strip() == "[[accept]]"]
    return [(s + 1, (starts[k + 1] if k + 1 < len(starts) else len(lines))) for k, s in enumerate(starts)]


def _blame(path: Path, a: int, b: int, sh):
    rc, out = sh(["git", "blame", "--line-porcelain", "-L", f"{a},{b}", "--", path.name], path.parent)
    if rc != 0:
        return None
    commits, cur = {}, None
    for ln in out.splitlines():
        if re.fullmatch(r"[0-9a-f]{40} .*", ln):
            cur = ln.split()[0]
            commits.setdefault(cur, ["", ""])
        elif cur and ln.startswith("author "):
            commits[cur][0] = ln[7:]
        elif cur and ln.startswith("author-mail "):
            commits[cur][1] = ln[12:].strip("<>")
    return {c: tuple(v) for c, v in commits.items()}


def load_verified(path: Path, *, agent_ids: set, allowed_signers: Path | None = None, sh=run_sh_in):
    path = Path(path)
    rc, dirty = sh(["git", "status", "--porcelain", "--", path.name], path.parent)
    if rc != 0:
        return [], [f"{path.name}: not in a git repository; no entry counts"]
    if dirty.strip():
        return [], [f"{path.name}: uncommitted changes; no entry counts until committed"]
    text = path.read_text(encoding="utf-8")
    entries = tomllib.loads(text).get("accept", [])
    ranges = _entry_ranges(text.splitlines())
    if len(entries) != len(ranges):
        return [], [f"{path.name}: entries could not be matched to lines; no entry counts"]
    accepted, rejected = [], []
    for entry, (a, b) in zip(entries, ranges):
        cid = entry.get("check_id")
        commits = _blame(path, a, b, sh)
        if not commits:
            rejected.append(f"{cid}: git blame failed; entry does not count")
            continue
        agents = [c for c, who in commits.items() if who in agent_ids]
        if agents:
            rejected.append(f"{cid}: written by an agent identity ({agents[0][:8]})")
            continue
        verification = "author-checked"
        if allowed_signers is not None:
            bad = [c for c in commits
                   if sh(["git", "-c", f"gpg.ssh.allowedSignersFile={allowed_signers}", "verify-commit", c],
                         path.parent)[0] != 0]
            if bad:
                rejected.append(f"{cid}: commit {bad[0][:8]} has no valid allowed signature")
                continue
            verification = "signature-checked"
        accepted.append(Acceptance(entry["check_id"], entry["fingerprint"], entry["accepted_by"],
                                   entry.get("reason", ""), verification))
    return accepted, rejected
