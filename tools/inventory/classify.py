#!/usr/bin/env python3
"""Classify every tracked path of a commit as GENERIC / INSTANCE / OPERATOR / MIXED.

Inventory tool for the repo-split spec (docs/superpowers/specs/2026-11-12-repo-split-design.md).
Read-only: it reads blobs through `git cat-file --batch` at a given commit and never
looks at the working tree, so the result is reproducible from the commit alone.

Output: one TSV line per path, no header, four columns

    path <TAB> class <TAB> scope <TAB> reason

- class  GENERIC | INSTANCE | OPERATOR | MIXED
- scope  instance name(s) joined by `+`, or `-`
- reason rule ids / pattern-class ids that fired, never the matched text

The rules live in rules.toml next to this file. Literal operator values never go
into that file; `--private-values` loads extra literals from a private, gitignored
list at run time (one value per line, `#` comments) and reports them as class
`private-value`.

Usage:
    python3 classify.py --ref <commit> --out inventory.tsv --summary
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLASSES = ("GENERIC", "INSTANCE", "OPERATOR", "MIXED")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
APOSTROPHES = ("'", "’")


@dataclass
class Rules:
    salt: str
    binary_probe_bytes: int
    instances: list[tuple[str, list[re.Pattern], list[re.Pattern]]]
    instance_paths: list[tuple[str, str, re.Pattern]]
    operator_paths: list[tuple[str, re.Pattern]]
    exclude_lines: re.Pattern | None
    operator_classes: list[tuple[str, re.Pattern]]
    operator_tokens: dict[str, tuple[str, bool]]  # sha256 -> (id, strict_apostrophe)
    private_values: list[tuple[str, re.Pattern]] = field(default_factory=list)


def load_rules(path: Path | str = HERE / "rules.toml") -> Rules:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    meta = raw.get("meta", {})
    instances = [
        (
            inst["name"],
            [re.compile(p) for p in inst.get("path", [])],
            [re.compile(p) for p in inst.get("content", [])],
        )
        for inst in raw.get("instance", [])
    ]
    known = {name for name, _, _ in instances}
    instance_paths = []
    for rule in raw.get("instance_path", []):
        if rule["instance"] not in known:
            raise ValueError(f"instance_path {rule['id']}: unknown instance {rule['instance']}")
        instance_paths.append((rule["id"], rule["instance"], re.compile(rule["pattern"])))
    excl = raw.get("operator_content", {}).get("exclude_lines")
    return Rules(
        salt=meta.get("token_salt", ""),
        binary_probe_bytes=int(meta.get("binary_probe_bytes", 8000)),
        instances=instances,
        instance_paths=instance_paths,
        operator_paths=[(r["id"], re.compile(r["pattern"])) for r in raw.get("operator_path", [])],
        exclude_lines=re.compile(excl) if excl else None,
        operator_classes=[(r["id"], re.compile(r["pattern"])) for r in raw.get("operator_class", [])],
        operator_tokens={
            r["sha256"]: (r["id"], bool(r.get("strict_apostrophe", False)))
            for r in raw.get("operator_token", [])
        },
    )


def load_private_values(patterns: list[str]) -> list[tuple[str, re.Pattern]]:
    """Read literal values from private list files (globs, `~` expanded).

    Each non-empty, non-comment line is one literal, matched case-insensitively.
    Missing files are not an error here (the inventory runs without them); the
    caller sees the count and decides.
    """
    values: list[tuple[str, re.Pattern]] = []
    for pattern in patterns:
        for name in sorted(glob.glob(os.path.expanduser(pattern))):
            with open(name, encoding="utf-8") as fh:
                for line in fh:
                    value = line.strip()
                    if value and not value.startswith("#"):
                        values.append(("private-value", re.compile(re.escape(value), re.IGNORECASE)))
    return values


def token_hash(salt: str, token: str) -> str:
    return hashlib.sha256((salt + token.lower()).encode("utf-8")).hexdigest()


def is_binary(data: bytes, probe: int) -> bool:
    return b"\0" in data[:probe]


def operator_hits(text: str, rules: Rules) -> set[str]:
    """Pattern-class ids of operator identifiers in `text` (authorship lines excluded)."""
    if rules.exclude_lines is not None:
        text = "\n".join(l for l in text.splitlines() if not rules.exclude_lines.search(l))
    hits = {cid for cid, rx in rules.operator_classes if rx.search(text)}
    hits |= {cid for cid, rx in rules.private_values if rx.search(text)}
    if rules.operator_tokens:
        for m in TOKEN_RE.finditer(text):
            entry = rules.operator_tokens.get(token_hash(rules.salt, m.group(0)))
            if entry is None or entry[0] in hits:
                continue
            tid, strict = entry
            if strict and text[m.end():m.end() + 1] in APOSTROPHES:
                continue
            hits.add(tid)
    return hits


def classify(path: str, data: bytes | None, rules: Rules) -> tuple[str, str, str]:
    """Return (class, scope, reason) for one path. `data` None means "not scanned"."""
    for rid, inst, rx in rules.instance_paths:
        if rx.search(path):
            return "INSTANCE", inst, f"path:{rid}"
    for name, path_rx, _ in rules.instances:
        if any(rx.search(path) for rx in path_rx):
            return "INSTANCE", name, "path:instance-name"
    for rid, rx in rules.operator_paths:
        if rx.search(path):
            return "OPERATOR", "-", f"path:{rid}"
    if data is None or is_binary(data, rules.binary_probe_bytes):
        return "GENERIC", "-", "binary-unscanned"
    text = data.decode("utf-8", errors="replace")
    insts = [name for name, _, content_rx in rules.instances if any(rx.search(text) for rx in content_rx)]
    ops = sorted(operator_hits(text, rules))
    if insts:
        reason = "content:instance"
        if ops:
            reason += ";operator:" + ",".join(ops)
        return "MIXED", "+".join(insts), reason
    if ops:
        return "MIXED", "-", "operator:" + ",".join(ops)
    return "GENERIC", "-", "clean"


def tracked_blobs(ref: str, repo: str = "."):
    """Yield (path, bytes) for every blob in the tree of `ref` (symlinks yield their target)."""
    listing = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "-z", "--full-tree", ref],
        check=True, capture_output=True,
    ).stdout
    entries = []
    for rec in listing.split(b"\0"):
        if not rec:
            continue
        meta, _, name = rec.partition(b"\t")
        mode, otype, sha = meta.split()
        if otype != b"blob":  # submodule commits have no content here
            entries.append((name.decode("utf-8"), None))
            continue
        entries.append((name.decode("utf-8"), sha.decode()))
    proc = subprocess.Popen(
        ["git", "-C", repo, "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    try:
        for name, sha in entries:
            if sha is None:
                yield name, None
                continue
            proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            size = int(header[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)  # trailing LF
            yield name, data
    finally:
        proc.stdin.close()
        proc.wait()


def summarize(rows: list[tuple[str, str, str, str]]) -> str:
    by_class = collections.Counter(r[1] for r in rows)
    lines = [f"total\t{len(rows)}"]
    lines += [f"{c}\t{by_class.get(c, 0)}" for c in CLASSES]
    inst = collections.Counter(r[2] for r in rows if r[1] == "INSTANCE")
    lines += [f"INSTANCE:{k}\t{v}" for k, v in sorted(inst.items())]
    mixed_inst = sum(1 for r in rows if r[1] == "MIXED" and r[2] != "-")
    lines.append(f"MIXED:instance-strings\t{mixed_inst}")
    lines.append(f"MIXED:operator-only\t{by_class.get('MIXED', 0) - mixed_inst}")
    top = collections.defaultdict(collections.Counter)
    for path, cls, _, _ in rows:
        top[path.split("/", 1)[0] if "/" in path else "(root)"][cls] += 1
    lines.append("top\t" + "\t".join(CLASSES) + "\ttotal")
    for folder in sorted(top):
        c = top[folder]
        lines.append(folder + "\t" + "\t".join(str(c.get(k, 0)) for k in CLASSES) + f"\t{sum(c.values())}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="HEAD", help="commit to classify (default HEAD)")
    ap.add_argument("--repo", default=".", help="git repository (default .)")
    ap.add_argument("--rules", default=str(HERE / "rules.toml"))
    ap.add_argument("--private-values", action="append", default=[],
                    help="glob of private literal-value lists (repeatable); never commit these")
    ap.add_argument("--out", help="write the TSV here instead of stdout")
    ap.add_argument("--summary", action="store_true", help="print counts to stderr")
    args = ap.parse_args(argv)

    rules = load_rules(args.rules)
    rules.private_values = load_private_values(args.private_values)
    if args.private_values:
        print(f"private values loaded: {len(rules.private_values)}", file=sys.stderr)
    rows = []
    for path, data in tracked_blobs(args.ref, args.repo):
        cls, scope, reason = classify(path, data, rules)
        rows.append((path, cls, scope, reason))
    rows.sort(key=lambda r: r[0])
    text = "".join("\t".join(r) + "\n" for r in rows)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.summary:
        print(summarize(rows), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
