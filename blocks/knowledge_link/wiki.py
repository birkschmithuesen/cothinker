#!/usr/bin/env python3
# CoThinker - the wiki layer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Which project notes a topic belongs to -- by the notes, not by a model.

A topic title is free text, not a note. To ask the graph about it, this
module builds a TRANSIENT note for the title: its links are the stems of
existing project notes whose name occurs in the title, its tags are the
project tags that equal a title word. Then it asks the ONE edge source
there is, `graph.neighbours.HardEdges.neighbours`. No embeddings, no
second ranking, no model call, no network.

What it is used for (card MM-G): a new topic block in the protocol gets the
related notes appended as wikilinks (`### HH:MM Titel -> [[a]], [[b]]`).
The stage title stays as it is; the links go to the protocol only.

And a topic page (`create_topic_page`): level 2's `document` action and
the Zuruf "Robo, leg dafuer eine Seite an" (`ZURUF_SEITE`, a regex) both
create `<session>/entwuerfe/<slug>.md` with its related notes under
`Verwandt:` (`WikiLayer.page_links`) -- through ONE path,
`consumer.StageWriter.file_topic_page`, which also files the backlink.
The wiki layer belongs to the knowledge-link block
(`blocks/knowledge_link/__init__.py`), which owns it.

NEVER RAISES into the consumer. Every step is best-effort: a failure costs
the links, never the topic block.
"""
from __future__ import annotations

import threading
import time
import re
import unicodedata
from pathlib import Path

from shared.events import is_token
from shared.minutes_format import clean_line
from shared.wiki_names import PAGE_DIR, _normalise, page_path, page_slug, slugify
from graph.neighbours import HardEdges
from graph.vault_notes import Note, NoteIndex, scan_vault

# How long one scan of the project folder is reused. A topic change is
# rare (minutes apart), a scan walks the whole folder; a note written a
# minute ago not yet being linked costs nothing.
RESCAN_S = 60

# A stem shorter than this matches too much by accident ("tee", "hof"
# inside every other title). Counted after normalising.
MIN_STEM = 4

# The protocol's title line limit (`Protocol.open_topic`, `clean_line`).
TITLE_MAX = 120

# Topic pages (MM-G D4/D5): how many links, how many names.
PAGE_LINKS = 5
MIN_PAGE_LINKS = 2
# ANNAHME: 50 pages of one name in one session is not a meeting, it is a loop.
DOC_NAME_TRIES = 50

# The Zuruf "Robo, leg dafuer eine Seite an" (D7). Zero-LLM, checked only
# on blocks that carry the wake word. Two shapes, each within one clause
# (only whitespace between the words):
#   * an order verb, at most three words, then an INDEFINITE article
#     (eine / ne / 'ne, optionally eigene / neue) directly before "Seite":
#     "mach mal eine Seite dazu", "erstelle eine Seite";
#   * the separable "leg ... Seite ... an" or "Seite ... anlegen".
# "mach weiter die Seite drei" / "mach mal die Seite zwei auf" name a page
# that exists -- a definite article is not an order to create one. `\w` is
# Unicode: "dafür" and "dafuer" are both one word. The apostrophe of 'ne
# may be typographic (’ne) -- speech-to-text writes either.
# A clause with a negation ("leg keine Seite an", "bitte nicht eine Seite")
# is never an order: `wants_page` checks each clause for `ZURUF_NEIN`.
_PAGE_VERB = r"(?:an)?(?:leg|lege|legen|mach|mache|machen)|erstell\w*"
_PAGE_ART = r"(?:eine|ne|['’]ne)(?:\s+(?:eigene|neue))?"
ZURUF_SEITE = re.compile(
    rf"\b(?:{_PAGE_VERB})(?:\s+\w+){{0,3}}?\s+{_PAGE_ART}\s+seiten?\b"
    rf"|\bleg(?:e|t)?(?:\s+[\w'’]+){{1,4}}?\s+seiten?(?:\s+\w+){{0,3}}?"
    rf"\s+an\b"
    rf"|\bseiten?(?:\s+\w+){{0,3}}?\s+anleg\w*",
    re.IGNORECASE)
ZURUF_NEIN = re.compile(r"\b(?:kein\w*|nicht)\b", re.IGNORECASE)
# One clause: what lies between punctuation marks.
_CLAUSE_SPLIT = re.compile(r"[,.;:!?]+")

def wants_page(text) -> bool:
    """True when one clause of `text` orders a topic page (`ZURUF_SEITE`)
    and carries no negation (`ZURUF_NEIN`). Never raises."""
    if not isinstance(text, str):
        return False
    try:
        return any(ZURUF_SEITE.search(satz) is not None
                   and ZURUF_NEIN.search(satz) is None
                   for satz in _CLAUSE_SPLIT.split(
                       unicodedata.normalize("NFC", text)))
    except Exception:                     # noqa: BLE001 -- never raise
        return False


_TITLE_MARK = re.compile(r"\s*\*\((?:autonom|bestätigt [^)]*)\)\*\s*$")
_TITLE_LINKS = re.compile(r"\s+->\s+\[\[[^\]]*\]\](?:,\s*\[\[[^\]]*\]\])*\s*$")


def bare_title(line) -> str:
    """A protocol topic-block title without what `topic_title()` added:
    the level-2 marker and the ` -> [[a]], [[b]]` link suffix."""
    if not isinstance(line, str):
        return ""
    return _TITLE_LINKS.sub("", _TITLE_MARK.sub("", line)).strip()


def _relative(pfad, project_dir) -> str:
    """`pfad` relative to `project_dir`, posix; as given when it is not
    below it or is already relative."""
    p = Path(str(pfad)) if pfad else Path()
    try:
        return p.relative_to(Path(project_dir)).as_posix()
    except ValueError:
        return p.as_posix()


def create_topic_page(session_dir, project_dir, title, links, mark, created,
                      protocol_rel, *, taken=None,
                      report=None) -> Path | None:
    """Create the topic page skeleton (MM-G D5). None = not created.

    EXCLUSIVE: `open("x")`, never overwrites. A name that is taken -- the
    file exists, or `taken(stem)` says a project note carries that stem
    already, which would make the page's own backlink ambiguous -- moves
    on to `<slug>-2`, `-3` …, at most DOC_NAME_TRIES names. `links` are
    stems (already filtered by the caller, see `WikiLayer.page_links`),
    written as `Verwandt: [[a]], [[b]]`; none, no line. `report(token)`
    hears why nothing was created (`name_taken` or the exception's class
    name). Never raises.
    """
    def melden(grund: str) -> None:
        if report is not None:
            try:
                report(grund)
            except Exception:             # noqa: BLE001 -- never raise
                pass

    try:
        titel = clean_line(title if isinstance(title, str) else "", TITLE_MAX)
        slug = page_slug(titel, created)
        stems = [s for s in (links or ()) if isinstance(s, str) and s]
        kopf = (f"---\ntype: topic\ncreated: {created:%Y-%m-%d %H:%M}\n"
                f"sources: [{_relative(protocol_rel, project_dir)}]\n"
                "contributors: [robo]\n---\n\n"
                f"# {titel or f'Themenseite {created:%H:%M}'}\n\n")
        if mark:
            kopf += f"{mark}\n\n"
        if stems:
            kopf += ("Verwandt: " + ", ".join(f"[[{s}]]" for s in stems)
                     + "\n\n")
        ordner = Path(session_dir) / PAGE_DIR
        ordner.mkdir(parents=True, exist_ok=True)
        for n in range(1, DOC_NAME_TRIES + 1):
            pfad = page_path(session_dir, slug, n)
            if taken is not None and taken(pfad.stem):
                continue
            try:
                with pfad.open("x", encoding="utf-8") as fh:
                    fh.write(kopf)
                return pfad
            except FileExistsError:
                continue
        melden("name_taken")
    except Exception as e:                # noqa: BLE001 -- never raise
        name = type(e).__name__
        melden(name if is_token(name) else "exception")
    return None


class WikiLayer:
    """Neighbours of a topic title inside one project folder.

    The folder is the round's documents folder (the consumer's project
    folder), not the session folder: the background notes live there.
    """

    def __init__(self, project_dir, *, clock=time.monotonic,
                 rescan_s: float = RESCAN_S) -> None:
        self.project_dir = Path(project_dir)
        self.clock = clock
        self.rescan_s = rescan_s
        self._lock = threading.Lock()
        self._cache: tuple[NoteIndex, HardEdges] | None = None
        self._scanned_at: float | None = None

    def _edges(self) -> tuple[NoteIndex, HardEdges] | None:
        """The cached (index, edges) pair; rescanned after `rescan_s`.

        A scan error is not cached: the next topic tries again.
        """
        with self._lock:
            now = self.clock()
            if (self._cache is None or self._scanned_at is None
                    or now - self._scanned_at >= self.rescan_s):
                try:
                    index = scan_vault(self.project_dir)
                    self._cache = (index, HardEdges(index))
                    self._scanned_at = now
                except Exception:         # noqa: BLE001 -- never raise
                    self._cache = None
                    self._scanned_at = None
                    return None
            return self._cache

    @staticmethod
    def _transient(index: NoteIndex, titel: str) -> Note | None:
        """The title as a note: matching stems as links, matching tags."""
        slug = _normalise(titel)
        if not slug:
            return None
        umrahmt = f"-{slug}-"
        links: list[str] = []
        for note in index.notes.values():
            stem_n = _normalise(note.stem)
            if (len(stem_n) >= MIN_STEM and f"-{stem_n}-" in umrahmt
                    and note.stem not in links):
                links.append(note.stem)
        tokens = set(slug.split("-"))
        tags = tuple(tag for tag in index.tag_groups
                     if _normalise(tag) in tokens)
        return Note(path=Path("<wiki-topic>"), rel="<wiki-topic>",
                    stem=slugify(titel), mtime=0.0,
                    links=tuple(sorted(links)), tags=tags)

    def nachbarn(self, titel: str, k: int = 5) -> list[Note]:
        """Up to `k` project notes related to `titel`, best first. Never raises.

        Ranking: HardEdges' order, stably sorted by (-weight, rel) --
        drawn links before shared tags.
        """
        try:
            paar = self._edges()
            if paar is None:
                return []
            return self._ranked(*paar, titel, k)
        except Exception:                 # noqa: BLE001 -- never raise
            return []

    def _ranked(self, index: NoteIndex, edges: HardEdges, titel,
                k: int) -> list[Note]:
        """`nachbarn()` on one given (index, edges) pair. May raise."""
        transient = self._transient(index, titel)
        if transient is None:
            return []
        # Stufe 2 (Embeddings): sobald graph/embeddings.py existiert, hier austauschen — HardEdges.neighbours bleibt der Vertrag
        treffer = edges.neighbours(transient)
        treffer = sorted(treffer, key=lambda n: (-n.weight, n.note.rel))
        return [n.note for n in treffer[:max(0, k)]]

    def _unique_stems(self, index: NoteIndex, edges: HardEdges, titel,
                      limit: int, skip=None) -> list[str]:
        """Up to `limit` neighbour stems that each resolve to exactly
        their note -- the ONE uniqueness filter for every link this module
        writes (topic-block suffix and topic page). May raise.

        Every neighbour is looked at, not the first `limit`: an ambiguous
        one drops out and must not take a unique one's place. Same pair as
        the resolve() check -- a rescan in between would compare notes of
        two different scans. `skip(note)` True: not a candidate at all.
        """
        stems: list[str] = []
        for note in self._ranked(index, edges, titel, len(index.notes)):
            if len(stems) >= limit:
                break
            if skip is not None and skip(note):
                continue
            if index.resolve(note.stem) is note and note.stem not in stems:
                stems.append(note.stem)
        return stems

    def page_links(self, titel: str, k: int = PAGE_LINKS, *,
                   session_dir=None) -> list[str]:
        """The stems a topic page links to under `Verwandt:`. Never raises.

        Up to `k` unique neighbours of `titel`; with fewer than
        MIN_PAGE_LINKS the project's index note (stem == project folder
        name) tops them up when it exists and resolves uniquely. Still
        fewer: what there is -- a link is never invented.

        `session_dir`: the page's own session folder. Its notes (this
        run's protocol, the other drafts) are not background material and
        never a candidate; other sessions' notes are. A session folder
        that IS the project folder (the flat layout) excludes nothing.
        """
        try:
            paar = self._edges()
            if paar is None:
                return []
            index, edges = paar
            skip = self._inside(session_dir)
            stems = self._unique_stems(index, edges, titel, max(0, k),
                                       skip=skip)
            if len(stems) < MIN_PAGE_LINKS and len(stems) < k:
                haupt = index.resolve(self.project_dir.name)
                if (haupt is not None and haupt.stem not in stems
                        and not (skip is not None and skip(haupt))):
                    stems.append(haupt.stem)
            return stems
        except Exception:                 # noqa: BLE001 -- never raise
            return []

    def link_paths(self, titel: str, k: int = PAGE_LINKS, *,
                   session_dir=None) -> list[str] | None:
        """The related notes of `titel` as paths relative to the project
        folder (posix), best first; same uniqueness and session-folder
        filter as `page_links`. None when there is no vault: the scan
        failed or the folder holds no notes. Never raises."""
        try:
            paar = self._edges()
            if paar is None or not paar[0].notes:
                return None
            index, edges = paar
            stems = self._unique_stems(index, edges, titel, max(0, k),
                                       skip=self._inside(session_dir))
            return [index.resolve(s).rel.replace("\\", "/") for s in stems]
        except Exception:                 # noqa: BLE001 -- never raise
            return None

    def _inside(self, session_dir):
        """A `skip(note)` predicate: the note lies in `session_dir`. None
        when there is nothing to exclude (no folder, not below the project
        folder, or the project folder itself)."""
        if session_dir is None:
            return None
        try:
            rel = Path(session_dir).resolve().relative_to(
                self.project_dir.resolve())
        except (OSError, ValueError):
            return None
        if not rel.parts:
            return None
        praefix = rel.as_posix() + "/"
        return lambda note: note.rel.replace("\\", "/").startswith(praefix)

    def taken(self, stem: str) -> bool:
        """True when a project note already carries `stem` (any case).

        A page named like an existing note would make its own backlink
        ambiguous. Unknown (scan failed) counts as free: the exclusive
        create still never overwrites. Never raises.
        """
        try:
            paar = self._edges()
            return (paar is not None
                    and bool(paar[0].by_stem.get(str(stem).casefold())))
        except Exception:                 # noqa: BLE001 -- never raise
            return False

    def link_suffix(self, titel: str, *, room: int, max_links: int = 3,
                    session_dir=None) -> tuple[str, int]:
        """` -> [[a]], [[b]]` fitting into `room` characters, and its count.

        Only notes whose stem resolves to exactly that note are linked (by
        stem, no path): an ambiguous name would be a link that means two
        files. Links are dropped from the END until the suffix fits; a
        link is never cut in half. ("", 0) when nothing fits or is found.
        `session_dir`: excluded exactly as in `page_links`.
        """
        try:
            paar = self._edges()
            if paar is None:
                return "", 0
            index, edges = paar
            stems = self._unique_stems(index, edges, titel, max_links,
                                       skip=self._inside(session_dir))
            while stems:
                suffix = " -> " + ", ".join(f"[[{s}]]" for s in stems)
                if len(suffix) <= room:
                    return suffix, len(stems)
                stems.pop()
        except Exception:                 # noqa: BLE001 -- never raise
            pass
        return "", 0


def topic_title(titel, mark: str, wiki, *, limit: int = TITLE_MAX,
                session_dir=None) -> tuple[str, int]:
    """The protocol's topic-block title: title, links, marker. Never raises.

    `Titel -> [[a]], [[b]] *(autonom)*` within `limit` characters. The
    marker is never cut; links drop from the end until everything fits;
    only when not even the bare title fits is the title shortened. Returns
    the line and the number of links in it. `wiki` None or failing: the
    title (and marker) without links, count 0. `session_dir` goes to
    `link_suffix`: the protocol's own session folder is not linked.
    """
    basis = clean_line(titel if isinstance(titel, str) else "", limit)
    platz_marker = len(mark) + 1 if mark else 0
    suffix, n = "", 0
    if wiki is not None and basis:
        try:
            suffix, n = wiki.link_suffix(
                basis, room=limit - len(basis) - platz_marker,
                session_dir=session_dir)
        except Exception:                 # noqa: BLE001 -- never raise
            suffix, n = "", 0
    text = basis + suffix
    if not mark:
        return text[:limit], n
    rest = max(0, limit - platz_marker)
    return (text[:rest].rstrip() + " " + mark).strip(), n
