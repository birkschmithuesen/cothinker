#!/usr/bin/env python3
# CoThinker — publish gate
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Check tool run before every push to a public remote (repo split 6.3,
port of the Kapsel room's K7, `oeffentlich_pruefen.py`).

Scans the FULL history (`git log --all -p`) and the commit metadata
(author, committer, message) against
  1. `publish/denylist-public.txt` (generic pattern classes, public-safe),
  2. private lists (plaintext, local only: `--private-list GLOB`),
  3. `--hmac FILE` with the key from `COTHINKER_DENYLIST_KEY` (CI; 1-3 token
     windows), and
scans the tracked files with `detect-secrets-hook` against `.secrets.baseline`.

Never prints the matched text. Exit: 0 clean, 1 hit, 3 self-test/tool
failed, 4 no private list.

    python3 tools/publish_check.py --repo <dir> [--private-list GLOB] [--hmac FILE]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import hmac
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
PUBLIC_DENYLIST = HERE / "publish" / "denylist-public.txt"
ALLOWLIST = HERE / "publish" / "allow-public.txt"
DEFAULT_PRIVATE_LIST_GLOB = str(pathlib.Path.home() / ".config" / "cothinker" / "denylist.d" / "*.txt")
CANARY = "cothinker" + "canary"
TOKEN_RE = re.compile(r"[^a-z0-9]+")
COMMIT_MARK = "\x01COTHINKER-COMMIT "


class PatternList:
    def __init__(self, ident, literals=(), regexes=(), hmacs=None, key=None, allow=frozenset()):
        self.ident = ident
        self.literals = list(literals)
        self.regexes = list(regexes)
        self.hmacs = hmacs
        self.key = key
        self.allow = allow

    def is_empty(self):
        return not (self.literals or self.regexes or self.hmacs)

    def hit(self, line):
        lower = line.lower()
        for lit in self.literals:
            if lit in lower:
                return True
        for rx in self.regexes:
            m = rx.search(line)
            if m and m.group(0).lower() not in self.allow:
                return True
        if self.hmacs:
            tokens = [t for t in TOKEN_RE.split(lower) if t]
            for n in (1, 2, 3):
                for i in range(len(tokens) - n + 1):
                    if _hmac(self.key, " ".join(tokens[i:i + n])) in self.hmacs:
                        return True
        return False


def _hmac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def load_list(path, ident, allow=frozenset()):
    literals, regexes = [], []
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            regexes.append(re.compile(line[3:]))
        else:
            literals.append(line.lower())
    return PatternList(ident, literals, regexes, allow=allow)


class AllowList:
    """Values (literal or `re:` class) that an otherwise-matching regex in
    the same denylist must not flag. Checked with `in`, same as a
    frozenset, against the regex's own matched substring (group 0), not
    the whole scanned line. See publish/allow-public.txt."""

    def __init__(self, literals=frozenset(), regexes=()):
        self.literals = literals
        self.regexes = tuple(regexes)

    def __contains__(self, value):
        return value in self.literals or any(rx.fullmatch(value) for rx in self.regexes)


def load_allow(path):
    path = pathlib.Path(path)
    if not path.exists():
        return AllowList()
    literals, regexes = set(), []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            regexes.append(re.compile(line[3:], re.IGNORECASE))
        else:
            literals.add(line.lower())
    return AllowList(frozenset(literals), regexes)


def load_hmac_list(path, key):
    values = {z.strip() for z in pathlib.Path(path).read_text().splitlines()
              if z.strip() and not z.startswith("#")}
    return PatternList("hmac", hmacs=values, key=key)


def history_lines(repo):
    """(commit12, path|META, line) across every ref, including author, committer, message."""
    fmt = COMMIT_MARK + "%H%n%an <%ae>%n%cn <%ce>%n%B"
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "-p", "--no-color", "--format=" + fmt],
        capture_output=True, check=True).stdout.decode("utf-8", "replace")
    commit, path = "?", "META"
    for line in out.splitlines():
        if line.startswith(COMMIT_MARK):
            commit, path = line[len(COMMIT_MARK):][:12], "META"
            continue
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            continue
        yield commit, path, line


def tree_lines(tree_dir):
    for p in sorted(pathlib.Path(tree_dir).rglob("*")):
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    yield "TREE", str(p), line
            except UnicodeDecodeError:
                continue


def scan(source, lists):
    hits = set()
    for commit, path, line in source:
        for pl in lists:
            if pl.hit(line):
                hits.add((pl.ident, commit, path))
    return sorted(hits)


def self_test(lists):
    """Positive control: every list must find its canary (or pattern) in a temp repo.

    GIT_AUTHOR_NAME/EMAIL and GIT_COMMITTER_NAME/EMAIL in the process
    environment outrank "-c user.name=..." in git -- every Hermes agent
    shell sets GIT_AUTHOR_NAME, and that string can itself be a private-list
    literal. Left alone, the canary commit's author/committer identity would
    leak through and let a list "pass" on that leak instead of on a real
    canary-text match. Pin all four explicitly so the check means what it
    says regardless of the calling environment.
    """
    canary_env = dict(os.environ)
    canary_env.update({
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    })
    with tempfile.TemporaryDirectory() as tmp:
        g = ["git", "-C", tmp, "-c", "user.email=t@example.org", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", tmp], check=True, env=canary_env)
        pathlib.Path(tmp, "canary.txt").write_text(CANARY + "\n-----BEGIN " + "PRIVATE KEY-----\n")
        subprocess.run(g + ["add", "."], check=True, env=canary_env)
        subprocess.run(g + ["commit", "-qm", "canary"], check=True, env=canary_env)
        found = {t[0] for t in scan(history_lines(tmp), lists)}
    return all(pl.ident in found for pl in lists)


def detect_secrets(repo):
    hook = shutil.which("detect-secrets-hook") or str(pathlib.Path.home() / ".local/bin/detect-secrets-hook")
    if not os.path.exists(hook):
        print("ERROR detect-secrets-hook not found", file=sys.stderr)
        return 3
    files = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True,
                            text=True, check=True).stdout.split("\0")
    r = subprocess.run([hook, "--baseline", ".secrets.baseline", *filter(None, files)],
                        cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        print("HIT detect-secrets (details: run detect-secrets-hook locally)")
        return 1
    return 0


def hmac_generate(denylist):
    key = os.environ.get("COTHINKER_DENYLIST_KEY", "").encode()
    if len(key) < 32:
        print("ERROR COTHINKER_DENYLIST_KEY missing or < 32 characters", file=sys.stderr)
        return 3
    pl = load_list(denylist, "x")
    print("# HMAC-SHA256 of the private patterns. Worthless without the key.")
    for lit in pl.literals:
        tokens = [t for t in TOKEN_RE.split(lit) if t]
        if 1 <= len(tokens) <= 3:
            print(_hmac(key, " ".join(tokens)))
        else:
            print(f"WARN pattern with {len(tokens)} tokens not HMAC-capable (local hook only)",
                  file=sys.stderr)
    if pl.regexes:
        print(f"WARN {len(pl.regexes)} regex patterns only in the local hook", file=sys.stderr)
    return 0


def _expand_private_list(patterns):
    paths = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        paths.extend(matches if matches else [pattern])
    return paths


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--repo", default=".")
    a.add_argument("--private-list", action="append", default=[])
    a.add_argument("--hmac")
    a.add_argument("--hmac-generate")
    a.add_argument("--skip-detect-secrets", action="store_true")
    a.add_argument("--tree")
    a.add_argument("--public-only", action="store_true")
    o = a.parse_args(argv)
    if o.hmac_generate:
        return hmac_generate(o.hmac_generate)

    lists = [load_list(PUBLIC_DENYLIST, "public", allow=load_allow(ALLOWLIST))]
    private_paths = _expand_private_list(o.private_list) if o.private_list else \
        sorted(glob.glob(DEFAULT_PRIVATE_LIST_GLOB))
    for i, p in enumerate(private_paths):
        lists.append(load_list(p, f"private{i + 1}"))
    if o.hmac:
        key = os.environ.get("COTHINKER_DENYLIST_KEY", "").encode()
        if len(key) < 32:
            print("ERROR COTHINKER_DENYLIST_KEY missing", file=sys.stderr)
            return 4
        lists.append(load_hmac_list(o.hmac, key))
    if len(lists) == 1 and not o.public_only:
        print("ERROR no private list loaded (fail-closed)", file=sys.stderr)
        return 4
    if any(pl.is_empty() for pl in lists) or not self_test(lists):
        print("ERROR self-test failed - check tool is not running blind", file=sys.stderr)
        return 3

    source = tree_lines(o.tree) if o.tree else history_lines(o.repo)
    hits = scan(source, lists)
    for ident, commit, path in hits:
        # CI logs of public repos are public: in HMAC mode, only a path hash.
        if o.hmac and path != "META":
            path = "path#" + hashlib.sha256(path.encode()).hexdigest()[:12]
        print(f"{ident}\t{commit}\t{path}")
    rc = 1 if hits else 0
    if not o.skip_detect_secrets and not o.tree:
        rc = max(rc, detect_secrets(o.repo))
    print(f"{'CLEAN' if rc == 0 else 'DIRTY'}: {len(hits)} hits, {len(lists)} lists")
    return rc


if __name__ == "__main__":
    sys.exit(main())
