#!/usr/bin/env python3
# CoThinker — conflict triage after a meeting
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Sort sync conflict fragments out of the way after a meeting.

`cothinker-start.sh --stop` calls this once the consumer is stopped
(feedback-2026-09-26 pt 9: eight `.conflictN` fragments
after three restarts had to be sorted by hand).

Rules (AGENTS.md, rule 3: never just delete conflict files):
  * fragments are MOVED into `<project>/konflikte-<YYYY-MM-DD>/`, never
    deleted; a name already taken there gets a unique suffix, nothing is
    overwritten;
  * the real file stays where it is -- no content is merged or judged, a
    human compares afterwards;
  * both the session folder(s) and the project folder are scanned (the
    round's own documents live in the project folder);
  * the move happens under the sync's flock, so bisync never sees a
    half-done move;
  * nothing found: say so, exit 0. A triage problem never fails `--stop`.

The detection patterns are shared with `consumer.py` (`conflict_files`).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_safe import SyncWindow  # noqa: E402

CONFLICT_GLOBS = ("*conflict*", "*Conflict*", "*konflikt*", "*Konflikt*",
                  "*.orig")
FOLDER_PREFIX = "konflikte-"
# What gets MOVED: only rclone bisync's own `<name>.conflictN` suffix
# (vault-instant-sync.sh uses --conflict-resolve newer, default suffix).
# The broad CONFLICT_GLOBS stay for the stage WARNING only: a round's
# document like "konfliktanalyse.md" must never be moved away.
FRAGMENT_RE = re.compile(r"\.conflict\d+$")


def find_conflicts(folders: Iterable[Path], *, movable_only: bool = False) -> list[Path]:
    """Conflict files directly inside each folder (not recursive).

    movable_only=False: everything that looks like a conflict (warning).
    movable_only=True: only unmistakable sync fragments (triage); files
    inside a `konflikte-*` folder are never returned either way."""
    hits: list[Path] = []
    for folder in folders:
        if Path(folder).name.startswith(FOLDER_PREFIX):
            continue
        for pattern in CONFLICT_GLOBS:
            try:
                hits.extend(p for p in Path(folder).glob(pattern) if p.is_file())
            except OSError:
                continue
    if movable_only:
        hits = [p for p in hits if FRAGMENT_RE.search(p.name)]
    return sorted(set(hits))


@dataclass
class TriageResult:
    target: Path | None = None
    moved: list[tuple[Path, Path]] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)

    def summary(self) -> str:
        if not self.moved and not self.failed:
            return "Konflikt-Sichtung: keine Konfliktdateien gefunden (no conflict files)."
        lines = [f"Konflikt-Sichtung: {len(self.moved)} Fragment(e) nach "
                 f"{self.target} verschoben (nichts geloescht)."]
        lines += [f"  {src.name} -> {dst.name}" for src, dst in self.moved]
        lines += [f"  NICHT verschoben: {src} ({why})" for src, why in self.failed]
        return "\n".join(lines)


def _unique(target_dir: Path, name: str) -> Path:
    cand = target_dir / name
    n = 1
    while cand.exists():
        cand = target_dir / f"{name}.dup{n}"
        n += 1
    return cand


def triage(folders: list[Path], *, day: str | None = None,
           target_root: Path | None = None,
           lock_path: str | None = None,
           window: SyncWindow | None = None) -> TriageResult:
    """Move conflict fragments found in `folders` into
    `<target_root>/konflikte-<day>/` (target_root defaults to the LAST
    folder given, i.e. the project folder).

    `window`: a SyncWindow the caller already holds (the consumer's
    shutdown). It is used as it is -- no second lock is taken and it is
    not released here."""
    folders = [Path(f) for f in folders if Path(f).is_dir()]
    res = TriageResult()
    found = find_conflicts(folders, movable_only=True)
    if not found:
        return res
    root = Path(target_root) if target_root else folders[-1]
    target = root / f"{FOLDER_PREFIX}{day or date.today().isoformat()}"
    res.target = target
    win = window
    if win is None:
        win = SyncWindow(root, lock_path=lock_path, wait_s=5.0)
        win.take_lock()  # busy lock: carry on, as the consumer does
    try:
        target.mkdir(parents=True, exist_ok=True)
        for src in found:
            # A fragment from a session folder keeps the folder name in
            # front, so the origin stays readable.
            name = src.name if src.parent == root else f"{src.parent.name}__{src.name}"
            dst = _unique(target, name)
            try:
                shutil.move(str(src), str(dst))
                res.moved.append((src, dst))
            except OSError as e:
                res.failed.append((src, repr(e)))
    finally:
        if window is None:
            win.release_lock()
    return res


def project_folders(project_dir: Path) -> list[Path]:
    """Session folders one level below `<project>/<sessions>/`, then the
    project folder itself (last = target root)."""
    sessions = project_dir / (os.environ.get("COTHINKER_SESSIONS_DIR", "").strip()
                              or "sessions")
    out: list[Path] = []
    for base in {sessions, project_dir / "sitzungen"}:
        if base.is_dir():
            out.append(base)
            out.extend(sorted(d for d in base.iterdir() if d.is_dir()))
    out.append(project_dir)
    return out


def resolve_project_dir(args: argparse.Namespace) -> Path | None:
    if args.project_dir:
        return Path(args.project_dir)
    override = os.environ.get("COTHINKER_PROJECT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    ent = os.environ.get("COTHINKER_ENTITIES", "").strip()
    if args.project and ent:
        hits = [d for d in Path(ent).glob(f"*/projekte/{args.project.strip('/')}") if d.is_dir()]
        if len(hits) == 1:
            return hits[0]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project-dir", help="project folder (session folders are found below it)")
    ap.add_argument("--project", help="project slug, resolved under $COTHINKER_ENTITIES")
    ap.add_argument("--day", help="date for the folder name (default: today)")
    args = ap.parse_args(argv)
    try:
        pdir = resolve_project_dir(args)
        if pdir is None or not pdir.is_dir():
            print("Konflikt-Sichtung uebersprungen: Projektordner unbekannt "
                  "(pass --project-dir or --project).")
            return 0
        print(triage(project_folders(pdir), day=args.day, target_root=pdir).summary())
    except Exception as e:  # never fail --stop over a triage problem
        print(f"Konflikt-Sichtung fehlgeschlagen: {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
