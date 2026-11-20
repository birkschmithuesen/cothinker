#!/usr/bin/env python3
# CoThinker - the vault as notes, links and tags
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Reading the vault. The ONLY module in this package that touches disk.

THE SYMLINK TRAP, measured on the real vault (architect, 2026-09-15):

    Path.rglob('*.md')             ->   4
    os.walk(followlinks=False)     ->   4
    os.walk(followlinks=True)      -> 869

Five directory symlinks (`00-Inbox`, `10-Wissen`, `30-Entities`,
`35-Tools`, `90-Meta`) carry the whole collection. A scan without
`followlinks=True` reports success on four notes -- which is why
`stage.py::list_files()` cannot serve vault notes either (see the
separate read path there).

THE DEDUPE is not optional, although today it finds nothing: the same
measurement counted 869 raw and 869 unique realpaths, 0 reached twice.
The five symlinks point at disjoint trees TODAY. One added later could
build a cycle, and `os.walk(followlinks=True)` does not detect one.
`realpath` costs nothing and makes the scan independent of that.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Person-related notes stay out until the operator decides otherwise: 186 of 866
# notes live in `entities/*/network/`, the stage requires a token
# (docs/operations.md, Abschnitt 3) but still ships every project file
# in cleartext HTTP to whoever holds the link, and a file name is a real
# name. See docs/plan-graph-schirm-1.md's open item on this.
DEFAULT_EXCLUDE: tuple[str, ...] = ("*/network/*",)

# The regex M3 measured with, plus the one thing that measurement found:
# an embed (`![[...]]`) is a picture, not a reference. Measured on the real
# vault: 2590 occurrences in 570 of 869 notes, 828 distinct targets, 589
# (23 %) without a file and 82 (3 %) ambiguous -- and three of the five
# most frequent "targets" are `.jpg` embeds.
WIKILINK_RE = re.compile(
    r"(?<!!)\[\[([^\]\|#\^]+)(?:[#\^][^\]\|]*)?(?:\|[^\]]*)?\]\]")
# Belt and braces for an embed written without the `!`.
MEDIA_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf",
                  ".mp4", ".mp3", ".wav", ".webm", ".canvas", ".excalidraw")


def extract_links(text: str) -> list[str]:
    """Every wikilink occurrence in `text`, as a bare file name.

    Occurrences, NOT distinct targets: a note linked three times is closer
    than one linked once, and the ring ranking reads that off this list.

    Code fences are deliberately NOT skipped -- M3 measured the numbers
    this module is checked against without that exception, and a wikilink
    in a fenced block is still a link somebody wrote.
    """
    ziele: list[str] = []
    for treffer in WIKILINK_RE.finditer(text):
        name = treffer.group(1).strip().replace("\\", "/").rstrip("/")
        name = name.split("/")[-1]              # `../NewBauhaus` -> NewBauhaus
        if name.lower().endswith(MEDIA_SUFFIXES):
            continue
        if name.lower().endswith(".md"):
            name = name[:-3]
        if name:
            ziele.append(name)
    return ziele


_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_FM_INLINE_RE = re.compile(r"^tags\s*:\s*\[(.*?)\]\s*$", re.M)
_FM_SCALAR_RE = re.compile(r"^tags\s*:\s*([^\[\r\n].*?)\s*$", re.M)
_FM_BLOCK_RE = re.compile(r"^tags\s*:\s*$\r?\n((?:\s*-\s*.+\r?\n?)+)", re.M)
# An inline tag: `#` at a boundary, then a LETTER. That keeps `#!/bin/sh`,
# `# Ueberschrift` and `C#` out without having to parse the document.
_INLINE_TAG_RE = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=[(\[]))#([^\W\d_][\w/-]*)",
                            re.UNICODE | re.M)


def extract_tags(text: str) -> list[str]:
    """The tags of a note: frontmatter AND inline, in order, deduplicated.

    Measured on the real vault (M4): 586 of 869 notes carry tags, 519
    distinct ones, 565 as a YAML inline list and one scalar. The block
    list does not occur today; it is read anyway, because a note written
    tomorrow may use it and silently dropping its tags would be invisible.
    """
    tags: list[str] = []

    def nimm(roh: str) -> None:
        name = roh.strip().strip("\"'").lstrip("#").strip().casefold()
        if name and name not in tags:
            tags.append(name)

    kopf = _FRONTMATTER_RE.match(text)
    rumpf = text[kopf.end():] if kopf else text
    if kopf:
        block = kopf.group(1)
        inline = _FM_INLINE_RE.search(block)
        if inline:
            for teil in inline.group(1).split(","):
                nimm(teil)
        else:
            liste = _FM_BLOCK_RE.search(block + "\n")
            if liste:
                for zeile in liste.group(1).splitlines():
                    nimm(zeile.strip().lstrip("-"))
            else:
                skalar = _FM_SCALAR_RE.search(block)
                if skalar:
                    nimm(skalar.group(1))
    for treffer in _INLINE_TAG_RE.finditer(rumpf):
        nimm(treffer.group(1))
    return tags


@dataclass(frozen=True)
class Note:
    """One note. `rel` is the identity everything else joins on."""
    path: Path
    rel: str
    stem: str
    mtime: float
    links: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


class NoteIndex:
    """Every note, addressable three ways: by path, by stem, by tag."""

    def __init__(self, notes: list[Note]) -> None:
        self.notes: dict[str, Note] = {n.rel: n for n in notes}
        self.by_stem: dict[str, list[Note]] = {}
        for n in notes:
            self.by_stem.setdefault(n.stem.casefold(), []).append(n)
        self.tag_groups: dict[str, list[Note]] = {}
        for n in notes:
            for tag in n.tags:
                self.tag_groups.setdefault(tag, []).append(n)
        self.unresolved: dict[str, int] = {}
        self.ambiguous: dict[str, int] = {}
        self._count_links()

    def resolve(self, target: str) -> Note | None:
        """The note a wikilink means, or None -- and None is a RESULT.

        Two cases, both measured on the real vault (M3), both of which a
        naive implementation turns into the file-less nodes the operator
        forbade on 2026-09-14:

          * no file at all -- 589 of 2590 occurrences (23 %), `projekt`
            alone 147 times;
          * more than one file with that name -- 82 occurrences (3 %),
            `protokoll` alone 46 times, and there are 12 files called
            `README`.

        Neither produces a node, and neither produces an edge. The
        ambiguous case is counted separately because it is a question
        about the vault, not a broken link.
        """
        name = str(target).strip().split("|")[0].split("#")[0].strip()
        name = name.replace("\\", "/").rstrip("/").split("/")[-1]
        if name.lower().endswith(".md"):
            name = name[:-3]
        treffer = self.by_stem.get(name.casefold(), [])
        return treffer[0] if len(treffer) == 1 else None

    def _count_links(self) -> None:
        """Fill the two counters once, after the index is complete."""
        for note in self.notes.values():
            for ziel in note.links:
                schluessel = ziel.casefold()
                anzahl = len(self.by_stem.get(schluessel, []))
                if anzahl == 1:
                    continue
                buch = self.ambiguous if anzahl > 1 else self.unresolved
                buch[schluessel] = buch.get(schluessel, 0) + 1

    def report(self) -> dict:
        """What the graph should say about itself. Numbers, no names."""
        return {"notes": len(self.notes),
                "unresolved": sum(self.unresolved.values()),
                "ambiguous": sum(self.ambiguous.values()),
                "tags": len(self.tag_groups)}


def scan_vault(root, *, exclude: Sequence[str] = DEFAULT_EXCLUDE) -> NoteIndex:
    """Every `.md` file under `root`, symlinks followed, realpaths unique.

    Directories are entered once by realpath; os.walk itself does not
    detect cycles, and two symlinks pointing at each other would
    otherwise make it walk forever (bounded only by the kernel's ELOOP
    limit, which still takes well over a minute).
    """
    root = Path(root)
    besucht: set[str] = set()
    gesehen: set[str] = set()
    notes: list[Note] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        echt = os.path.realpath(dirpath)
        if echt in besucht:
            dirnames[:] = []
            continue
        besucht.add(echt)
        # Hidden folders are tooling, not notes: `.obsidian`, `.git`,
        # `.trash`. Sorted so the scan is reproducible.
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not name.endswith(".md") or name.startswith("."):
                continue
            p = Path(dirpath) / name
            try:
                real = os.path.realpath(p)
                if real in gesehen or not os.path.isfile(real):
                    continue
                rel = p.relative_to(root).as_posix()
                st = p.stat()
            except (OSError, ValueError):
                continue
            if any(fnmatch.fnmatch("/" + rel, muster) for muster in exclude):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                # An unreadable note is not a reason to lose the graph.
                text = ""
            gesehen.add(real)
            notes.append(Note(path=p, rel=rel, stem=p.stem, mtime=st.st_mtime,
                              links=tuple(extract_links(text)),
                              tags=tuple(extract_tags(text))))
    return NoteIndex(notes)
