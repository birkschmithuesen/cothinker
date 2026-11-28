"""Human acceptances of a FAIL (ADR 0025 rule 7). Read-only: this module never writes files.

An entry counts only if (a) the file is committed and clean, (b) every line of
the entry is blamed to ONE commit whose (author name, email) is not an agent
identity and whose lines are consecutive there (a deletion-only commit cannot splice
surviving lines of two entries; as a consequence an entry a human edited across two
commits is rejected, and the human re-commits the whole entry), (c) with an allowed-signers file, each of those commits has a valid
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
    """Run a command; stdout is returned as raw bytes (no newline or encoding folding)."""
    p = subprocess.run(argv, cwd=cwd, capture_output=True, check=False)
    return p.returncode, p.stdout


def _b(out) -> bytes:
    return out.encode("utf-8") if isinstance(out, str) else out


def _lines(data: bytes) -> list:
    """Split on b"\n" only, as git numbers lines."""
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    return parts


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


_HEADER = re.compile(r"^\s*\[\[\s*accept\s*\]\]\s*(#.*)?$")
_ZERO = "0" * 40
_REQUIRED = ("check_id", "fingerprint", "accepted_by")


def _entry_ranges(lines):
    starts = [i for i, ln in enumerate(lines) if _HEADER.match(ln)]
    return [(s + 1, (starts[k + 1] if k + 1 < len(starts) else len(lines))) for k, s in enumerate(starts)]


def _blame_lines(name: str, cwd, sh):
    """Blame HEAD (never the working tree); returns {final_line: (sha, orig_line)} or None.
    Repo-configured ignore-revs are switched off so no commit can be skipped over."""
    rc, out = sh(["git", "-c", "blame.ignoreRevsFile=", "blame", "--line-porcelain", "HEAD", "--", name], cwd)
    if rc != 0:
        return None
    by_line = {}
    for ln in _b(out).split(b"\n"):
        m = re.fullmatch(rb"([0-9a-f]{40}) (\d+) (\d+)(?: \d+)?", ln)
        if m:
            by_line[int(m.group(3))] = (m.group(1).decode("ascii"), int(m.group(2)))
    return by_line


def _raw_people(sha: str, cwd, sh):
    """Raw {"author": (name, email), "committer": (name, email)} from the commit object
    (mailmap never applies), or None when either is unreadable."""
    rc, out = sh(["git", "cat-file", "commit", sha], cwd)
    if rc != 0:
        return None
    people = {}
    for raw in _b(out).split(b"\n"):
        if not raw:
            break
        try:
            ln = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        m = re.fullmatch(r"(author|committer) (.*?) <([^>]*)> .*", ln)
        if m and m.group(1) not in people:
            people[m.group(1)] = (m.group(2), m.group(3))
    return people if len(people) == 2 else None


def load_verified(path: Path, *, agent_ids: set, allowed_signers: Path | None = None, sh=run_sh_in):
    try:
        return _load_verified(Path(path), agent_ids, allowed_signers, sh)
    except FileNotFoundError as exc:
        return [], [f"{Path(path).name}: git could not be run ({exc}); no entry counts"]
    except OSError as exc:
        return [], [f"{Path(path).name}: I/O error ({exc}); no entry counts"]


def _load_verified(path: Path, agent_ids: set, allowed_signers, sh):
    cwd, name = path.parent, path.name
    if not agent_ids:
        return [], [f"{name}: no agent identities known; no entry counts"]
    rc, dirty = sh(["git", "status", "--porcelain", "--", name], cwd)
    if rc != 0:
        return [], [f"{name}: not in a git repository; no entry counts"]
    if _b(dirty).strip():
        return [], [f"{name}: uncommitted changes; no entry counts until committed"]
    rc, raw = sh(["git", "show", f"HEAD:./{name}"], cwd)
    if rc != 0:
        return [], [f"{name}: not committed at HEAD; no entry counts"]
    raw = _b(raw)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], [f"{name}: not valid UTF-8; no entry counts"]
    try:
        entries = tomllib.loads(text).get("accept", [])
    except tomllib.TOMLDecodeError as exc:
        return [], [f"{name}: invalid TOML ({exc}); no entry counts"]
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return [], [f"{name}: 'accept' must be a list of tables; no entry counts"]
    for e in entries:
        missing = [k for k in _REQUIRED if not isinstance(e.get(k), str)]
        if missing:
            return [], [f"{name}: entry missing required key(s) {', '.join(missing)}; no entry counts"]
    byte_lines = _lines(raw)
    try:
        lines = [ln.decode("utf-8") for ln in byte_lines]
    except UnicodeDecodeError:
        return [], [f"{name}: not valid UTF-8; no entry counts"]
    ranges = _entry_ranges(lines)
    if len(entries) != len(ranges):
        return [], [f"{name}: entries could not be matched to lines; no entry counts"]
    blame = _blame_lines(name, cwd, sh)
    if blame is None:
        return [], [f"{name}: git blame failed; no entry counts"]
    if sorted(blame) != list(range(1, len(lines) + 1)):
        return [], [f"{name}: line count differs from git blame; no entry counts"]
    agent_emails = {e.lower() for _, e in agent_ids}
    agent_names = {n.lower() for n, _ in agent_ids}
    accepted, rejected = [], []
    for entry, (a, b) in zip(entries, ranges):
        cid = entry["check_id"]
        got = [blame.get(n) for n in range(a, b + 1)]
        shas = {g[0] if g else None for g in got}
        if None in shas or _ZERO in shas:
            rejected.append(f"{cid}: lines not committed (blamed to no commit); entry does not count")
            continue
        people = {c: _raw_people(c, cwd, sh) for c in shas}
        if any(w is None for w in people.values()):
            rejected.append(f"{cid}: commit author could not be read; entry does not count")
            continue
        agents = [c for c, p in people.items()
                  if any(nm.lower() in agent_names or em.lower() in agent_emails for nm, em in p.values())]
        if agents:
            rejected.append(f"{cid}: written by an agent identity ({agents[0][:8]})")
            continue
        verification = "author-checked"
        if allowed_signers is not None:
            bad = [c for c in shas
                   if sh(["git", "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={allowed_signers}",
                          "verify-commit", c], cwd)[0] != 0]
            if bad:
                rejected.append(f"{cid}: commit {bad[0][:8]} has no valid allowed signature")
                continue
            verification = "signature-checked"
        origs = [g[1] for g in got]
        if len(shas) != 1 or any(y != x + 1 for x, y in zip(origs, origs[1:])):
            rejected.append(f"{cid}: entry lines not contiguous in one commit (lines deleted or merged); entry does not count")
            continue
        accepted.append(Acceptance(entry["check_id"], entry["fingerprint"], entry["accepted_by"],
                                   entry.get("reason", ""), verification))
    return accepted, rejected
