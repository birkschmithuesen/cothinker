#!/usr/bin/env python3
# CoThinker - zero-LLM lint over a project's wiki layer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Numbers only, never note names, never note text (AGENTS.md's data rule):
dead wikilinks, ambiguous ones, sessions without a backlink, topic pages
with fewer than two outgoing links.

    python3 scripts/wiki-lint.py <projektordner> [<projektordner> ...] [--json]

Reads the vault with `graph.vault_notes.scan_vault` -- the ONE wikilink and
frontmatter reader in this repo (see AGENTS.md, "One similarity system
only" / "reuse, don't duplicate"). No second parser here, and nothing
network- or model-related is imported: this is a static, offline check, run
by the round itself after a debrief (`docs/after-meeting-check.md`, section
E "Wiki-Rueckkopplung"), not by an agent.

Sessions (MM-G D4): each directory directly under `sessions/` or
`sitzungen/`, plus a flat `protokoll-<datum>.md` left over in the project
root from before the session-folder layout (AGENTS.md, "Every meeting
writes into its own folder"). A session's identifier is its folder name,
or -- for the flat file, which has none -- the date the file name carries;
the debrief backlink is exactly that text, `[[<datum>]]`
(`docs/after-meeting-check.md`, section E). A session HAS a backlink when
a note outside every session -- journal, task list, a moved wiki page --
links to it by exactly that identifier (any case; a link `[[<datum>]]`
counts as live even though no note is named that; it is not a dead link).
Per session, so the number proves the debrief line of THIS session: its
own links out (block titles, drafts in `entwuerfe/`), a link from another
session, or a link to one of its notes by name are not that line.

Pages: notes with frontmatter `type: topic` -- drafts in a session's
`entwuerfe/` and pages already moved into the project's wiki part alike.
Their count is of resolved outgoing wikilinks only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graph.vault_notes import scan_vault, _FRONTMATTER_RE  # noqa: E402

# Mirrors gateway/consumer.py SESSIONS_DIR / SESSIONS_DIR_ALT -- imported
# nowhere near this script's dependency tree (consumer.py pulls in the
# gateway package), so the two names are mirrored here instead, with this
# comment as the pointer to keep them in sync.
SESSIONS_DIR = "sessions"
SESSIONS_DIR_ALT = "sitzungen"

# Mirrors gateway/wiki.py MIN_PAGE_LINKS.
MIN_PAGE_LINKS = 2

_FLAT_PROTOCOL_RE = re.compile(r"^protokoll-(\d{4}-\d{2}-\d{2})\.md$")
_TYPE_RE = re.compile(r"^type\s*:\s*(\S+)\s*$", re.M)


def _find_sessions(project_dir: Path) -> list[dict]:
    """Every session in `project_dir`: `{"id": str, "owns": callable}`.

    Two shapes (see module docstring): a folder directly under `sessions/`
    or `sitzungen/`, and a flat `protokoll-<datum>.md` in the project root.
    Never raises -- a missing folder is simply not there.
    """
    gefunden: list[dict] = []
    for name in (SESSIONS_DIR, SESSIONS_DIR_ALT):
        basis = project_dir / name
        if not basis.is_dir():
            continue
        try:
            kinder = sorted(basis.iterdir())
        except OSError:
            continue
        for kind in kinder:
            if not kind.is_dir():
                continue
            praefix = f"{name}/{kind.name}/"
            gefunden.append({"id": kind.name,
                             "owns": lambda rel, p=praefix: rel.startswith(p)})
    try:
        eintraege = sorted(project_dir.iterdir())
    except OSError:
        eintraege = []
    for eintrag in eintraege:
        if not eintrag.is_file():
            continue
        treffer = _FLAT_PROTOCOL_RE.match(eintrag.name)
        if treffer:
            dateiname = eintrag.name
            gefunden.append({"id": treffer.group(1),
                             "owns": lambda rel, n=dateiname: rel == n})
    return gefunden


def _is_topic_page(note) -> bool:
    """Frontmatter `type: topic` -- reads the file itself, `Note` does not
    carry the field. Never raises."""
    try:
        text = note.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    kopf = _FRONTMATTER_RE.match(text)
    if not kopf:
        return False
    treffer = _TYPE_RE.search(kopf.group(1))
    if not treffer:
        return False
    return treffer.group(1).strip().strip("\"'").casefold() == "topic"


def lint_project(project_dir: Path) -> dict:
    """The seven numbers for one project folder. Never raises: a scan
    error (bad path, permissions) reads as an empty, tidy project."""
    index = scan_vault(project_dir)
    sessions = _find_sessions(project_dir)
    session_ids_cf = {s["id"].casefold() for s in sessions}

    dead_links = 0
    ambiguous_links = 0
    for note in index.notes.values():
        for ziel in note.links:
            if index.resolve(ziel) is not None:
                continue
            treffer = index.by_stem.get(ziel.casefold(), [])
            if len(treffer) > 1:
                ambiguous_links += 1
            elif ziel.casefold() in session_ids_cf:
                continue  # the debrief's `[[<datum>]]` backlink -- live
            else:
                dead_links += 1

    # The debrief backlinks actually written: `[[<session-id>]]` in a
    # project file that belongs to no session (journal, tasks, wiki pages).
    genannt: set[str] = set()
    for rel, note in index.notes.items():
        if any(s["owns"](rel) for s in sessions):
            continue
        genannt.update(ziel.casefold() for ziel in note.links)
    sessions_without_backlink = sum(
        1 for s in sessions if s["id"].casefold() not in genannt)

    pages = 0
    pages_under_2_links = 0
    for note in index.notes.values():
        if not _is_topic_page(note):
            continue
        pages += 1
        n = sum(1 for ziel in note.links if index.resolve(ziel) is not None)
        if n < MIN_PAGE_LINKS:
            pages_under_2_links += 1

    return {
        "notes": len(index.notes),
        "dead_links": dead_links,
        "ambiguous_links": ambiguous_links,
        "sessions": len(sessions),
        "sessions_without_backlink": sessions_without_backlink,
        "pages": pages,
        "pages_under_2_links": pages_under_2_links,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Zero-LLM lint over one or more project folders' wiki "
                     "layer -- numbers only.")
    parser.add_argument("projektordner", nargs="+",
                        help="one or more project folders to scan")
    parser.add_argument("--json", action="store_true",
                        help="one JSON object, keyed by folder name")
    args = parser.parse_args(argv)

    ergebnis: dict[str, dict] = {}
    for arg in args.projektordner:
        schluessel = Path(arg).name or arg
        if schluessel in ergebnis:
            schluessel = arg  # basename collision -- fall back to the path
        ergebnis[schluessel] = lint_project(Path(arg))

    if args.json:
        print(json.dumps(ergebnis))
    else:
        for schluessel, zahlen in ergebnis.items():
            zeile = " ".join(f"{k}={v}" for k, v in zahlen.items())
            print(f"{schluessel}: {zeile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
