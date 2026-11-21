#!/usr/bin/env python3
# CoThinker — web view of the running protocol
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Stage — the curated main screen of the meeting.

Four zones, laid out as approved on 08.09.2026 (docs/stage-methodology.md):

  SIDEBAR      Left column of its own: `Vertiefen` (the documents that are
               relevant right now) and `Termin` (the protocol of the day).
  HEADER       Sticky rows: the operating line (written by the consumer,
               deterministic), a trouble line that only appears when
               something is wrong, the mirror line (the agent's
               paraphrase), the decision line, and the notices.
  MAIN AREA    What holds NOW. Quiet, short, curated. Not an archive.
               Replaced only on a call-out; the overflow goes into a
               collapsed block instead of being deleted.
  BACKEND      The project folder in the vault, reachable via the sidebar —
               opened manually, never pushed on the viewer.

The consumer sets the stage via `buehne.json` (schema v2) in the project
folder; `gateway/stage_state.py` produces its content fields:

    {"version": 2, "updated": "2026-09-08T19:12:04",
     "status": {"state": "working", "text": "⚙️ Robo werkelt seit m:ss — …",
                "ticking": true, "since": "2026-09-08T19:11:50",
                "warning": "⚠️ Konfliktdateien im Projektordner — …"},
     "mirror": "Robo hat die vier Stränge aufgenommen.",
     "headline": "Vorbereitung Projekt-Call",
     "main": {"markdown": "## Vier Stränge\\n- Update",
              "collapsed_lines": 3, "collapsed_markdown": "- a\\n- b\\n- c"},
     "decisions": [{"id": "D1", "time": "19:43", "text": "kein Pitch",
                    "added_at": "2026-09-08T19:12:02+02:00"}],
     "notices": [{"id": "N1", "kind": "value_conflict", "text": "…",
                  "raised_at": "…", "state": "open", "follow_up": false}],
     "question": {"id": "Q1", "text": "…", "raised_at": "…", …},
     "links": [{"file": "usecases", "label": "Use-Case-Tabelle"}],
     "parked": 1, "parked_entries": [{"text": "…", "parked_at": "…"}],
     "age_visible_after_s": 20, "follow_up_after_s": 120}

UX principle: the page NEVER reloads. It fetches only a fingerprint every
3 s; only when that changes is the affected section swapped out. Scroll
position, expanded blocks and text selection are preserved.

The running duration and the notice ages tick in the BROWSER. Everything
that carries a clock is deliberately kept out of the fingerprint — the
durations, `since`, and the operating line's text, whose dominant state
`listening` reads `🎧 hört zu · zuletzt HH:MM:SS`. A per-second value in
the fingerprint would report a change on every poll and run the swap
logic continuously. The operating line loses nothing by it: the page
applies it on every poll, before it ever looks at the fingerprint.

  python3 stage.py --project meeting-begleiter --port 8020
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph.neighbours import HardEdges                     # noqa: E402
from graph.ring_select import Hysteresis                   # noqa: E402
from graph.vault_notes import DEFAULT_EXCLUDE, scan_vault   # noqa: E402
# `session_graph` sits next to this file. Run as a script, this directory
# is already on the path; imported by a test or a tool, it might not be.
# `append`, not `insert(0, ...)`: putting this directory FIRST would let a
# bare `import stage` resolve to `stage/stage.py` as a flat module instead
# of the `stage` package rooted one level up (line above), which breaks
# `import stage.<x>` for anything else in the process. Appended, it is
# only consulted after the repo root, and `from session_graph import ...`
# below still finds it either way.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parent))
from session_graph import (build_session_graph,             # noqa: E402
                           protocol_head, topics_from_stage)
from gateway.protocol_doc import is_v2, parse_topics         # noqa: E402
from gateway import stage_image                              # noqa: E402

ENTITIES = Path(os.environ.get(
    "COTHINKER_ENTITIES", Path.home() / "vault/entities"))
PROJECT: Path = Path()
BUEHNE_JSON = "buehne.json"

# The vault root for the graph surface. EMPTY BY DEFAULT and NOT derived
# from ENTITIES: that one falls back to `~/vault/entities`, a path which
# does not exist (the symlink is called `30-Entities`). An empty root
# means the click-through is off, not that the graph stops being drawn.
VAULT: Path = Path(os.environ.get("COTHINKER_VAULT", ""))
VAULT_EXCLUDE: tuple[str, ...] = DEFAULT_EXCLUDE
# `None` means no check at all (`--no-token`); otherwise every request must
# present this value, via `?t=` or the `X-Stage-Token` header (`H._get()`).
TOKEN: str | None = None
STATIC = Path(__file__).resolve().parent / "static"

# ---- Participant actions (POST /join, POST /action) ----------------------
# The stage records taps in an append-only spool file OUTSIDE the project
# folder (a second writer inside a synced folder makes conflict copies); the
# consumer reads it. The stage never writes `buehne.json`.
DEFAULT_SPOOL_DIR = "~/.local/state/cothinker/spool"
SPOOL_DIR: Path = Path(DEFAULT_SPOOL_DIR).expanduser()
ACTIONS_FILE = "actions.jsonl"
COOKIE_NAME = "cothinker_pid"
MAX_POST_BYTES = 2048
MIN_ACTION_GAP_S = 0.5           # per participant
NAME_MAX = 40
QUEUE_OPS = ("request", "done")
_PID_RE = re.compile(r"[0-9a-f]{16}")
_last_action: dict[str, float] = {}      # pid -> time.monotonic() of last POST
_action_lock = threading.Lock()


def clean_name(raw: object) -> str | None:
    """A participant name, or None if unusable.

    Strips control (Cc) and format (Cf, e.g. zero-width / bidi marks)
    characters, then surrounding whitespace; 1..NAME_MAX characters remain.
    """
    if not isinstance(raw, str):
        return None
    name = "".join(c for c in raw
                   if unicodedata.category(c) not in ("Cc", "Cf")).strip()
    return name if 1 <= len(name) <= NAME_MAX else None


def append_action(pid: str, intent_id: str, kind: str, payload: dict) -> None:
    """Appends one JSON line to `<SPOOL_DIR>/<session folder>/actions.jsonl`.

    The session folder is the one holding the `buehne.json` the stage is
    showing (`stage_file().parent`). Line: `{"v": 1, "at", "person",
    "intent_id", "kind", "payload"}`; `at` is ISO-8601 UTC with
    milliseconds and a `Z` suffix, e.g. `2026-09-08T19:12:04.123Z`. The
    append happens under an exclusive `flock` as a single write.
    """
    at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    at = at.replace("+00:00", "Z")
    line = json.dumps({"v": 1, "at": at, "person": pid,
                       "intent_id": intent_id, "kind": kind,
                       "payload": payload}, ensure_ascii=False) + "\n"
    folder = SPOOL_DIR / stage_file().parent.name
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ACTIONS_FILE, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
# Read once, at import: the list is a deployment fact, and a directory
# listing per request would be a file system call in front of a room.
VENDOR_FILES = ("cytoscape.min.js", "layout-base.js", "cose-base.js",
                "cytoscape-fcose.js", "cytoscape-layout-utilities.js",
                "qrcode.js")
VAULT_TTL = 60.0                 # the vault changes slowly, the stage polls fast
# (timestamp, (NoteIndex | None, HardEdges | None, rel -> Path, digest)):
# ONE scan feeds both the `/vault/` allow-list and the graph tab's ring 1
# and 2. `digest` is a content hash of the scan result (see `_vault_scan`)
# -- the graph tab's cache key uses it INSTEAD of the timestamp, so a
# rescan that finds the same notes does not count as a change.
_vault_cache: tuple[float, tuple] | None = None
# One folder per meeting: `sessions/<YYYY-MM-DD>-<slug>/`, written by
# `gateway/consumer.py`. Runs from before that convention still lie flat
# in the project folder; the stage reads both.
#
# `sitzungen` (Befund 1, 2026-09-21): the vault-git history
# shows this folder was once named `sessions/` and got renamed to the
# German `sitzungen/` during a project cleanup (hermes-entities commit
# cdafadd) -- but `gateway/consumer.py::session_paths` still WRITES
# `sessions/` (measured: consumer.py:310/394, unchanged). Both names are
# real on disk right now and the renaming project is not the only one
# that will ever do this, so the stage reads both rather than betting on
# either being the final answer.
SESSIONS_DIR = "sessions"
SESSIONS_DIR_ALT = "sitzungen"


def configured_sessions_dir() -> str:
    """`COTHINKER_SESSIONS_DIR`, or `SESSIONS_DIR` when unset.

    Read at call time, like `gateway/consumer.py`'s own copy -- a partner
    repo may file meetings under a folder name that is neither `sessions/`
    nor `sitzungen/` (e.g. `protokolle/`); `session_dirs()` below adds it
    to the lookup rather than replacing the existing two.
    """
    return os.environ.get("COTHINKER_SESSIONS_DIR", "").strip() or SESSIONS_DIR

# The stage shows what holds NOW — 15 lines per the instructions given to the
# agent. The cap is the emergency brake in case it gets it wrong: a language
# model that accidentally writes the whole protocol into it must not be
# allowed to freeze every browser in the room.
MAX_STAGE_CHARS = 20000

# How many positions of the history the page renders at most. The consumer
# keeps twenty; this is the guard against a file that says otherwise, in
# the same spirit as MAX_STAGE_CHARS — the newest ones are the ones anybody
# pages back to.
MAX_HISTORY_SHOWN = 40

_lock = threading.Lock()
_last_good: dict | None = None          # last readable buehne.json
_files_cache: tuple[float, list] | None = None
_render_cache: dict = {}
_history_cache: dict = {}
FILES_TTL = 2.0                          # seconds

# `stage_parts()` + `fingerprint()` re-render and re-hash the whole stage
# on EVERY `/parts` poll, for EVERY viewer, every 3 s -- 1.29 MB/s and
# 8.6 % CPU per viewer, measured, on a public page with no access control
# (docs/publish-stage.md). But `buehne.json` only changes when the
# consumer writes it -- between two writes, ten viewers polling ten times
# each recompute the exact same bytes a hundred times. Cached by the
# file's own identity (mtime + size), the same pattern `render_file()`
# already uses below: the work happens once per WRITE, not once per POLL.
_parts_cache: tuple | None = None       # (schluessel, teile, mark)

# The graph tab (MM-J) polls `/parts?g=1`. Same posture as `_parts_cache`:
# built once per WRITE (plus once per vault rescan), not once per poll.
# The hysteresis lives here, at module level, and is only advanced by an
# actual build -- so its grace rounds count changes of the session, not
# how many tabs happen to be polling.
_graph_cache: tuple | None = None       # (schluessel, payload)
_graph_hysteresis = Hysteresis()
_graph_seed: str | None = None          # the session the hysteresis belongs to


def clear_cache() -> None:
    """Clears the file-list cache and the rendered-file cache.
    (The last good stage state stays — it is the fallback, not a cache.)"""
    global _files_cache, _vault_cache, _parts_cache, _graph_cache
    global _graph_hysteresis, _graph_seed
    with _lock:
        _files_cache = None
        _vault_cache = None
        _render_cache.clear()
        _history_cache.clear()
        _parts_cache = None
        _graph_cache = None
        _graph_hysteresis = Hysteresis()
        _graph_seed = None


# ---------------------------------------------------------------- Markdown
def md_inline(s: str, dateien: set) -> str:
    """One line of inline markup: escaped FIRST, marked up second.

    Shared by `md()` and the v2 protocol column (`render_protocol_v2()`),
    so an entry text gets exactly the same treatment as a line in any
    other file -- wikilinks included.
    """
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)

    def wl(m):
        ziel = m.group(1).split("|")[0].split("/")[-1]
        titel = m.group(1).split("|")[-1]
        if ziel in dateien:
            return f'<a href="file/{ziel}">{titel}</a>'
        return f'<span class="tot">{titel}</span>'
    s = re.sub(r"\[\[([^\]]+)\]\]", wl, s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def md(text, dateien: set, max_zeichen: int | None = None) -> str:
    """The in-house, deliberately small Markdown converter.

    Everything that goes in here is unchecked: automatic speech recognition
    and JSON written by a language model. That's why EVERY line is first
    HTML-escaped and only then marked up — never the other way round.

    `max_zeichen` caps the stage. A work document in the backend, by
    contrast, is shown in full — that's where you go to look things up.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if max_zeichen and len(text) > max_zeichen:
        text = (text[:max_zeichen]
                + f"\n\n*… gekuerzt, {len(text)} Zeichen insgesamt.*")
    # Strip control characters (from broken data streams), keep line breaks.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    out, in_code, in_list, in_table = [], False, False, False

    def inline(s: str) -> str:
        return md_inline(s, dateien)

    for zeile in text.split("\n"):
        if zeile.startswith("```"):
            if in_list: out.append("</ul>"); in_list = False
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(zeile)); continue
        if zeile.strip().startswith("|") and zeile.strip().endswith("|"):
            zellen = [c.strip() for c in zeile.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in zellen):
                continue
            if not in_table:
                out.append("<table>"); in_table = True
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in zellen) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        if m := re.match(r"^(#{1,6})\s+(.*)", zeile):
            if in_list: out.append("</ul>"); in_list = False
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>"); continue
        if m := re.match(r"^\s*[-*]\s+(.*)", zeile):
            if not in_list: out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(m.group(1))}</li>"); continue
        if in_list:
            out.append("</ul>"); in_list = False
        if not zeile.strip():
            out.append("")
        elif zeile.strip() == "---":
            out.append("<hr>")
        else:
            out.append(f"<p>{inline(zeile)}</p>")
    for schluss, offen in (("</ul>", in_list), ("</pre>", in_code), ("</table>", in_table)):
        if offen: out.append(schluss)
    return "\n".join(out)


# The three second-level headings a v2 protocol is cut at. Same tolerance
# as gateway/protocol_doc.py: trailing blanks and a CRLF line end.
_V2_SECTION_RE = re.compile(r"^## (Überblick|Themen|Rohtranskript)[ \t]*\r?$",
                            re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def render_protocol_v2(text: str, dateien: set) -> str:
    """The right column for a v2 protocol (topic blocks, tagged entries).

    Layout: the header (title, warning, start) as plain `md()`; the derived
    view `## Überblick` in a `.sicht` block the CSS keeps sticky at the top
    of the column; each topic as a `<details class="thema">` keyed by its
    1-based position (blocks are only ever appended, so the position is a
    stable identity the page script restores the open state by); the raw
    transcript collapsed at the end. Only the NEWEST topic arrives open,
    marked `data-auf="1"` as the server's default (see `detailsMerken` in
    the page script: only a reader's deviation from it is kept).

    Everything in the file is untrusted -- titles and entry texts go
    through `md_inline()` (escape first, mark up second), the view and the
    raw transcript through `md()`. The view's HTML comment is a note for
    whoever opens the file, not for the room, and is dropped.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Cut into sections by the FIRST occurrence of each heading, in order.
    grenzen: dict[str, tuple[int, int]] = {}
    for m in _V2_SECTION_RE.finditer(text):
        grenzen.setdefault(m.group(1), (m.start(), m.end()))
    ende = len(text)
    starts = sorted(s for s, _ in grenzen.values())

    def body(name: str) -> str:
        if name not in grenzen:
            return ""
        s, e = grenzen[name]
        naechste = [x for x in starts if x > s]
        return text[e:naechste[0] if naechste else ende]

    kopf_ende = starts[0] if starts else ende
    out = [md(text[:kopf_ende], dateien)]

    if "Überblick" in grenzen:
        sicht = _HTML_COMMENT_RE.sub("", body("Überblick"))
        out.append(f'<div class="sicht">{md("## Überblick" + sicht, dateien)}'
                   f'</div>')

    out.append("<h2>Themen</h2>")
    themen = parse_topics(text)
    if not themen:
        out.append("<p><em>Noch kein Thema.</em></p>")
    for n, thema in enumerate(themen, 1):
        # `data-auf` marks the server's default, so the page can tell a
        # block the READER opened or closed from one that is merely in its
        # default state -- and keep only the reader's deviations. Without
        # it the previous newest topic stayed open forever: its recorded
        # `true` was replayed after a newer topic took over the default.
        offen = ' open data-auf="1"' if n == len(themen) else ""
        teile = [f'<details class="thema" data-key="thema-{n}"{offen}>'
                 f'<summary>{md_inline(thema.time + " " + thema.title, dateien)}'
                 f' <span class="n">({len(thema.entries)})</span></summary>']
        if thema.entries:
            teile.append('<ul class="eintraege">')
            for e in thema.entries:
                tag = html.escape(e.tag, quote=True)
                teile.append(f'<li><span class="zeit">{html.escape(e.time)}</span> '
                             f'<span class="tag tag-{tag}">{tag}</span> '
                             f'{md_inline(e.text, dateien)}</li>')
            teile.append("</ul>")
        teile.append("</details>")
        out.append("".join(teile))

    if "Rohtranskript" in grenzen:
        roh = body("Rohtranskript")
        zeilen = sum(1 for z in roh.splitlines() if z.strip())
        out.append(f'<details class="roh" data-key="roh"><summary>Rohtranskript '
                   f'({zeilen} Zeilen)</summary>{md(roh, dateien)}</details>')
    return "\n".join(out)


def list_files() -> list[Path]:
    """The project's Markdown files — cached briefly.

    Every viewer polls every 3 s; without a cache, an `rglob` over the
    project folder would run several times per fetch.
    """
    global _files_cache
    with _lock:
        if _files_cache and time.monotonic() - _files_cache[0] < FILES_TTL:
            return _files_cache[1]
    try:
        wurzel = PROJECT.resolve()
        # Symlinks must not lead out of the project folder.
        alle = [p for p in PROJECT.rglob("*.md")
                if p.is_file() and str(p.resolve()).startswith(str(wurzel) + os.sep)]
    except OSError:
        alle = []
    alle.sort(key=lambda p: (p.parent != PROJECT, p.name))
    with _lock:
        _files_cache = (time.monotonic(), alle)
    return alle


def find_file(name: str) -> Path | None:
    """A project file by name, stem, or path relative to the project.

    The relative forms exist because the session folders introduced a
    second `protokoll.md` (and a third, and a fourth): the bare stem no
    longer identifies one file, so the `Termin` link carries
    `sessions/<YYYY-MM-DD>-<slug>/protokoll` and is resolved here. The
    candidates all come from `list_files()`, which already refuses
    anything that resolves outside the project folder -- a `..` in the
    request can therefore not match a file outside it.
    """
    try:
        wurzel = PROJECT.resolve()
    except OSError:
        wurzel = PROJECT
    gesucht = name.strip("/")
    for p in list_files():
        if p.name == name or p.stem == name:
            return p
        try:
            rel = p.resolve().relative_to(wurzel).as_posix()
        except (OSError, ValueError):
            continue
        if gesucht in (rel, rel[:-len(p.suffix)] if p.suffix else rel):
            return p
    return None


def _vault_scan() -> tuple[float, tuple]:
    """`(timestamp, (index, edges, rel -> Path, digest))`, rescanned once
    per VAULT_TTL.

    `digest` is a sha256 over the sorted `(rel, mtime)` of every note,
    computed ONCE per scan, here -- not once per poll. The graph tab's
    cache key (`session_graph_payload()`) uses this digest, not the
    timestamp above: the vault is rescanned every VAULT_TTL regardless of
    whether anything changed, and keying on the timestamp made the graph
    rebuild on that same 60 s clock -- which in turn advanced
    `Hysteresis.apply()`'s grace-round countdown once a minute even with
    a silent room, so "grace rounds" ended up measuring wall-clock
    minutes instead of session changes. Keyed on content instead, an
    unchanged rescan is a cache hit.
    """
    global _vault_cache
    with _lock:
        if _vault_cache and time.monotonic() - _vault_cache[0] < VAULT_TTL:
            return _vault_cache
    index = edges = None
    eintraege: dict = {}
    digest = ""
    if VAULT != Path():
        # `Path("")` normalises to `Path(".")`, and `str(Path("."))` is
        # ".", which is truthy -- `not str(VAULT)` never fired and left
        # the read path on by default. Compare the path itself instead.
        try:
            index = scan_vault(VAULT, exclude=VAULT_EXCLUDE)
            edges = HardEdges(index)
            eintraege = {rel: n.path for rel, n in index.notes.items()}
            digest = hashlib.sha256("\n".join(
                f"{rel}\t{n.mtime!r}"
                for rel, n in sorted(index.notes.items())).encode()
                ).hexdigest()
        except OSError:
            index = edges = None
            eintraege = {}
            digest = ""
    eintrag = (time.monotonic(), (index, edges, eintraege, digest))
    with _lock:
        _vault_cache = eintrag
    return eintrag


def vault_note_index() -> tuple:
    """`(NoteIndex | None, HardEdges | None)`, from the same cached scan
    as `vault_index()`. Both None without a vault root."""
    index, edges, _, _ = _vault_scan()[1]
    return index, edges


def vault_scan_digest() -> str:
    """The vault scan's content digest (see `_vault_scan`), computed once
    per scan and reused here rather than recomputed per poll. Part of the
    graph tab's cache key instead of the scan's timestamp -- see the
    comment on `_vault_scan` for why the timestamp alone was wrong."""
    return _vault_scan()[1][3]


def vault_index() -> dict:
    """The vault notes, `rel -> Path`, cached. Empty without a vault root.

    THE ALLOW-LIST, and it is deliberately not a path comparison. The
    vault root consists of symlinks that lead OUT of it (measured: five
    of them carry 865 of 869 notes), so `resolve().startswith(root)` --
    the check `list_files()` uses for the project folder -- would reject
    every real note. What is servable is what the scan found: a `..`
    cannot be a key, a `.png` is not in there, and neither is a note the
    privacy exclusion dropped.
    """
    return _vault_scan()[1][2]


def vault_file(rel: str) -> Path | None:
    """The note behind a `/vault/` request, or None. No guessing, no stems."""
    ziel = vault_index().get(unquote(str(rel)).strip("/"))
    if ziel is None or not ziel.is_file():
        return None
    return ziel


_beide_ordner_gemeldet: set[str] = set()  # PROJECT-Pfade, fuer die schon geloggt wurde


def beide_ordnernamen_da() -> bool:
    """True, wenn `PROJECT` sowohl `sessions/` als auch `sitzungen/` hat.

    Beide Namen nebeneinander lesend zu akzeptieren (Befund 1)
    darf nicht still bleiben, sobald beide real existieren --
    genau die Lage, die den stummen Ausfall am 21.09. verursacht hat, wenn
    sie sich unbemerkt wiederholt (z.B. ein Alt-Ordner unter dem alten
    Namen neben einem neuen unter dem neuen). Reine Existenzpruefung,
    unabhaengig davon ob ein Ordner Sitzungen enthaelt -- das Auftreten
    beider Namen selbst ist das meldenswerte Ereignis.
    """
    try:
        return (PROJECT / SESSIONS_DIR).is_dir() and (PROJECT / SESSIONS_DIR_ALT).is_dir()
    except OSError:
        return False


def session_dirs() -> list[Path]:
    """The `sessions/<YYYY-MM-DD>-<slug>/` (or `sitzungen/…`) folders,
    oldest first.

    One folder per meeting (see `gateway/consumer.py::session_paths`,
    which still writes `sessions/`) -- but a project's own folder may
    have been renamed to `sitzungen/` afterwards (Befund 1),
    so both are read and merged rather than either alone. Sorted by
    name, which starts with the date, so the last entry is the newest
    meeting across both folders.

    Finding BOTH names side by side is logged once per
    project path (stdout, the operator sees it) rather than picked
    silently -- see `beide_ordnernamen_da()` and the `warning` field
    `stage_parts()` sets from it for the stage itself.

    `COTHINKER_SESSIONS_DIR` adds a third folder name to the
    lookup (e.g. `protokolle/`, for a partner repo) -- additive, like the
    English/German pair above; unset, behaviour is unchanged.
    """
    if beide_ordnernamen_da():
        schluessel = str(PROJECT)
        if schluessel not in _beide_ordner_gemeldet:
            _beide_ordner_gemeldet.add(schluessel)
            print(f"stage: WARNUNG -- {PROJECT} hat sowohl "
                  f"{SESSIONS_DIR}/ als auch {SESSIONS_DIR_ALT}/ -- beide "
                  f"werden gelesen und gemischt, keiner wird bevorzugt.",
                  flush=True)
    kandidaten_namen = [SESSIONS_DIR, SESSIONS_DIR_ALT]
    configured = configured_sessions_dir()
    if configured not in kandidaten_namen:
        kandidaten_namen.append(configured)
    gefunden: list[Path] = []
    for name in kandidaten_namen:
        try:
            gefunden += [d for d in (PROJECT / name).iterdir() if d.is_dir()]
        except OSError:
            continue
    return sorted(gefunden, key=lambda d: d.name)


def stage_file() -> Path:
    """The `buehne.json` the consumer is writing right now.

    Two layouts are live at the same time: a run started before the
    session folders existed writes flat into the project folder, a run
    started after writes into `sessions/<YYYY-MM-DD>-<slug>/`. Which one
    the stage must read is not a question of layout but of time -- the
    file that was written last is the running meeting. Falls back to the
    flat path so that a project with neither still has a path to report
    as missing.
    """
    kandidaten = [d / BUEHNE_JSON for d in session_dirs()]
    kandidaten.append(PROJECT / BUEHNE_JSON)
    neuste, zeit = PROJECT / BUEHNE_JSON, None
    for k in kandidaten:
        try:
            m = k.stat().st_mtime
        except OSError:
            continue
        if zeit is None or m >= zeit:
            neuste, zeit = k, m
    return neuste


def read_stage() -> dict:
    """Reads `buehne.json` — and NEVER falls back to the raw transcript.

    The agent keeps replacing the file throughout the meeting. If a reader
    hits the file half-written, the previous outcome was: invalid JSON,
    falling back to the most recently changed Markdown file — and in
    practice that is exactly the protocol. The public stage would then have
    shown the room the unfiltered raw transcript.

    So: on a broken file, show the last readable state. It's a few seconds
    old, but correct.
    """
    global _last_good
    f = stage_file()
    try:
        roh = f.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        roh = None
    if roh is not None:
        try:
            b = json.loads(roh)
        except (json.JSONDecodeError, ValueError):
            b = None
        if isinstance(b, dict):
            with _lock:
                _last_good = b
            return b
    with _lock:
        if _last_good is not None:
            return _last_good
    return {"version": 2,
            "main": {"markdown": "*Die Bühne ist noch nicht gesetzt. "
                                 "Der erste verdichtete Stand erscheint "
                                 "gleich.*"},
            "links": []}


def rel_name(p: Path) -> str:
    """The identifier of a project file for `/file/…` and `/parts?d=…`.

    The stem alone stopped being unique when the session folders arrived:
    every meeting has a `protokoll.md`. So a file inside a subfolder is
    addressed by its path relative to the project, without the suffix
    (`sessions/2026-09-08-projekt/protokoll`); a file directly in the
    project folder keeps its bare stem, which is what every existing link
    and every wikilink in the documents already uses.
    """
    try:
        rel = p.resolve().relative_to(PROJECT.resolve())
    except (OSError, ValueError):
        return p.stem
    if rel.parent == Path("."):
        return p.stem
    return (rel.parent / rel.stem).as_posix()


def _mtime_then_name(p: Path) -> tuple[float, str]:
    """Sort key: modification time first, name as the tie-breaker.

    Two protocols written in the same second (a test, or a copy) would
    otherwise pick arbitrarily; the name keeps the answer stable.
    """
    try:
        return (p.stat().st_mtime, p.name)
    except OSError:
        return (-1.0, p.name)


def current_protocol_file() -> Path | None:
    """The protocol file of the RUNNING meeting (`Path`), or `None`.

    Shared logic behind `protocol_link()` (the sidebar link, name only)
    and the A1-Minimal right column (rendered content).

    Befund 3 (2026-09-21): this used to pick the protocol
    with the newest mtime across EVERY session folder in the project,
    independently of which folder `stage_file()` had decided is the
    running meeting. A sync/Obsidian touch on an old protocol was enough
    to make that stale file look newer than the running one -- measured:
    an old 07.09. `protokoll.md` outranked the running session's, and
    the column showed the wrong meeting's text mid-session.

    So: bind to the SAME session folder `stage_file()` picked, since that
    is the one place that already decides which meeting is running (by
    `buehne.json` mtime, the file the consumer writes far more often and
    is therefore the more reliable clock). Only when no `buehne.json`
    exists ANYWHERE yet (no running session has been established -- a
    project with nothing but legacy flat protocols) does this fall back
    to the old global-mtime search, so those projects keep finding
    something.
    """
    sf = stage_file()
    if sf.exists():
        ordner = sf.parent
        if ordner == PROJECT:
            try:
                kandidaten = [p for p in PROJECT.glob("protokoll-*.md")
                              if p.is_file()]
            except OSError:
                kandidaten = []
            return max(kandidaten, key=_mtime_then_name, default=None)
        p = ordner / "protokoll.md"
        return p if p.is_file() else None

    kandidaten: list[Path] = []
    try:
        kandidaten += [p for p in PROJECT.glob("protokoll-*.md") if p.is_file()]
    except OSError:
        pass
    for d in session_dirs():
        p = d / "protokoll.md"
        try:
            if p.is_file():
                kandidaten.append(p)
        except OSError:
            continue
    return max(kandidaten, key=_mtime_then_name, default=None)


def protocol_link() -> str:
    """The newest protocol in the project, addressable via `/file/…`, or "".

    The `Termin` block in the sidebar is the one link that is always
    right: the protocol is the hub, the stage only the excerpt. Before
    the first protocol exists there is nothing to link to, and then the
    block is left out entirely rather than shown dead.

    Returns a path relative to the project, not a bare stem: there is a
    `protokoll.md` in every session folder, and a stem no longer names
    one file.
    """
    neuste = current_protocol_file()
    return rel_name(neuste) if neuste is not None else ""


def _as_text(wert, vorgabe: str = "") -> str:
    """A field from foreign JSON, as text. The agent is a language model,
    not a schema: `headline` might be a number, `markdown` a list."""
    if wert is None:
        return vorgabe
    if isinstance(wert, str):
        return wert
    if isinstance(wert, (int, float, bool)):
        return str(wert)
    return json.dumps(wert, ensure_ascii=False)[:MAX_STAGE_CHARS]


def _int(wert, vorgabe: int) -> int:
    """A number from foreign JSON. `"viel"` must not take the page down.

    `OverflowError` belongs in the list: `json.loads` accepts the literals
    `Infinity` and `NaN`, and a plain-looking `1e400` parses to `inf` as
    well. `int(inf)` raises OverflowError — and because the file is kept
    as the last good state, the stage would stay broken until the next
    write. So the value is dropped, not the page.
    """
    try:
        return int(wert)
    except (TypeError, ValueError, OverflowError):
        return vorgabe


def _inline_bold(escaped: str) -> str:
    """`**x**` -> <strong>x</strong>, on ALREADY-escaped text.

    Notices are one line each; the full Markdown converter would wrap them
    in paragraphs. Only the emphasis is needed — the agent marks the
    conflicting value with it.
    """
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)


def _mark(kind: str, state: str) -> str:
    """The coloured character at the start of a notice line."""
    if state == "expired":
        return "⌛"
    if state != "open":
        return "✓"
    return "?" if kind == "question" else "⚠"


def _notice_line(entry: dict, kind: str) -> str:
    """One notice as ONE line: a coloured mark plus text, never a box.

    Approved 2026-09-08: notices and the question are recognisable by the
    mark at the start of the line, not by an area of their own -- they are
    fine, but they must not dominate.

    A settled entry is not removed here but turned into a receipt in the
    same slot ("nichts verschwindet ersatzlos"); the consumer drops it a
    few seconds later.

    RESOLVED AND EXPIRED ARE NOT THE SAME THING. Both are "not open", and
    rendering both as a green receipt told the round that a question
    nobody answered had been settled -- with the question itself as the
    receipt text, since there is no resolution to show. Section 4.4 asks
    for the opposite: a dimmed line saying it is unanswered and now lives
    under `## Offene Fragen`.
    """
    state = _as_text(entry.get("state")) or "open"
    text = _as_text(entry.get("text"))
    if state == "expired":
        text = ("<strong>unbeantwortet</strong> — steht jetzt unter "
                "Offene Fragen")
    elif state != "open":
        text = _as_text(entry.get("resolution")) or text
        text = f"<strong>aufgelöst</strong> — {html.escape(text)}"
    else:
        text = _inline_bold(html.escape(text))
    classes = ["notice", kind]
    if state != "open":
        classes.append("settled")
    if state == "expired":
        classes.append("abgelaufen")
    if entry.get("follow_up"):
        classes.append("nachfassen")
    return (f'<div class="{" ".join(classes)}" '
            f'data-raised="{html.escape(_as_text(entry.get("raised_at")))}">'
            f'<span class="mark">{_mark(kind, state)}</span>'
            f'<span>{text}<span class="age"></span></span></div>')


def _parked_line(entries) -> str:
    """The third notice: one dimmed line, no content of its own.

    The spec is explicit that only two message slots and one question spot
    exist; a third entry "wird geparkt" and is shown briefly as a dimmed
    line before it goes. What it SAID is deliberately not on the stage --
    that lives in `## Offene Fragen` in the protocol, which is where the
    line points. One line, no matter how many entries are parked: the
    point of the cap is that the notice zone cannot grow.

    The mark is neutral, not the warning mark: nothing here is a conflict,
    the surface is only telling the room that it held something back.
    """
    if not isinstance(entries, list):
        return ""
    if not any(isinstance(e, dict) for e in entries):
        return ""
    return ('<div class="notice parked">'
            '<span class="mark">·</span>'
            '<span><strong>geparkt</strong> — zwei Plätze belegt, '
            'Rest steht unter Offene Fragen</span></div>')


# Operator, 2026-10-26 (live, after 20.10.): in a topic entry the structure
# (call answers, coach contributions) belongs on TOP and the spoken text
# below, collapsed. An entry's markdown is the topic's spoken text; lane
# results are only ever APPENDED after it (stage_state.append_to_history),
# so everything from the first labelled paragraph on is structure.
_STRUCTURE_PARA = re.compile(
    r"^(\*\*(Robo \(|Abschluss |Nachtrag )|\*{0,2}(Struktur|Spiegel|Frage)\*{0,2}:)")


def structure_first(markdown: str, namen: set) -> str:
    """Topic markdown rendered structure-first, spoken text collapsed."""
    paras = [p for p in re.split(r"\n\s*\n", markdown or "") if p.strip()]
    cut = next((i for i, p in enumerate(paras)
                if _STRUCTURE_PARA.match(p.lstrip())), None)
    if cut == 0:                      # structure only, nothing spoken before it
        return md(markdown, namen, MAX_STAGE_CHARS)
    if cut is None:                   # spoken text only: title stays, text folds
        if not paras:
            return ""
        return ('<p class="leer"><em>Noch keine Struktur zu diesem Thema.</em></p>'
                '<details class="gesagt"><summary>Gesagtes</summary>'
                + md(markdown, namen, MAX_STAGE_CHARS) + "</details>")
    return (md("\n\n".join(paras[cut:]), namen, MAX_STAGE_CHARS)
            + '<details class="gesagt"><summary>Gesagtes</summary>'
            + md("\n\n".join(paras[:cut]), namen, MAX_STAGE_CHARS)
            + "</details>")

def history_parts(b: dict, namen: set) -> list[dict]:
    """The history as finished, swappable pieces — newest last.

    Every position travels with the poll, and that is deliberate: the
    pointer is client state (docs/entwurf-buehne-verlauf.md section 3.2),
    so a key press has to be able to change the view without asking the
    server. A fetch per step would put the network between the reader and
    the arrow key, and a second endpoint would be a second thing to keep
    in step with this one.

    Rendered HERE, like everything else: the file is written from speech
    recognition and a language model, the escaping is this module's one
    guarantee, and there is no Markdown converter on the other side. The
    title is escaped too — unlike `headline` the page writes it with
    `innerHTML`, together with the markup of the bar around it.

    Cached by content: a position never changes once it exists, and
    without this the whole history would be re-rendered for every viewer
    every three seconds — the same reason `render_file()` has a cache.
    """
    roh = b.get("history")
    roh = roh if isinstance(roh, list) else []
    teile: list[dict] = []
    for row in roh[-MAX_HISTORY_SHOWN:]:
        if not isinstance(row, dict):
            continue
        markdown = _as_text(row.get("markdown"))
        eingeklappt = _as_text(row.get("collapsed_markdown"))
        zeilen = _int(row.get("collapsed_lines"), 0)
        roh_graph = row.get("graph") if row.get("kind") == "graph" else None
        # C2 (gateway/stage_state.py:_history_row, Variante 1, operator
        # decision 2026-09-16): an OLDER graph position arrives with only its seed
        # -- `nodes`/`edges` were stripped server-side to hold content()
        # under the size bound. That is the signal to draw a stub instead
        # of an (empty, misleading) graph container.
        ist_voller_graph = isinstance(roh_graph, dict) and "nodes" in roh_graph
        graph = clean_graph(roh_graph) if ist_voller_graph else None
        graph_saat = (_as_text(roh_graph.get("seed"))
                      if isinstance(roh_graph, dict) and not ist_voller_graph
                      else "")
        schluessel = hashlib.sha256("\x00".join(
            [_as_text(row.get("id")), _as_text(row.get("title")), markdown,
             eingeklappt, str(zeilen), str(len(namen)),
             json.dumps(graph, sort_keys=True) if graph else "",
             "stub:" + graph_saat if graph_saat else ""]
        ).encode()).hexdigest()[:16]
        with _lock:
            treffer = _history_cache.get(schluessel)
        if treffer is None:
            collapsed = ""
            if zeilen > 0:
                collapsed = (f'<details><summary>{zeilen} weitere Zeilen'
                             f'</summary>'
                             f'{md(eingeklappt, namen, MAX_STAGE_CHARS)}'
                             f'</details>')
            if graph:
                haupt = graph_part(graph)
            elif graph_saat:
                haupt = graph_part_stub(graph_saat)
            else:
                haupt = structure_first(markdown, namen)
            treffer = {"id": _as_text(row.get("id"))[:64],
                       "title": html.escape(_as_text(row.get("title"))[:200]),
                       "main": haupt,
                       "collapsed": collapsed}
            with _lock:
                if len(_history_cache) > 64:
                    _history_cache.clear()
                _history_cache[schluessel] = treffer
        teile.append(treffer)
    return teile


# The stage draws what another process wrote. These two numbers are the
# emergency brake in the spirit of MAX_STAGE_CHARS: the selection keeps
# the graph at ~61 nodes (measured), and a file that says otherwise must
# not be able to freeze every browser in the room.
MAX_GRAPH_NODES = 200
MAX_GRAPH_EDGES = 800
EDGE_KINDS = ("wikilink", "tag")


def clean_graph(roh: object) -> dict:
    """A graph payload reduced to what this page will draw.

    Keys filtered, counts capped, unknown edge kinds dropped, every value
    coerced. Nothing here trusts the file -- same posture as `md()`.
    """
    if not isinstance(roh, dict):
        return {"seed": "", "nodes": [], "edges": []}
    knoten, kennungen = [], set()
    for n in (roh.get("nodes") if isinstance(roh.get("nodes"), list) else []):
        if not isinstance(n, dict) or len(knoten) >= MAX_GRAPH_NODES:
            continue
        kennung = _as_text(n.get("id"))[:300]
        if not kennung or kennung in kennungen:
            continue
        kennungen.add(kennung)
        knoten.append({"id": kennung, "label": _as_text(n.get("label"))[:120],
                       "ring": _int(n.get("ring"), 0),
                       "seed": bool(n.get("seed")),
                       "grace": bool(n.get("grace"))})
    kanten = []
    for e in (roh.get("edges") if isinstance(roh.get("edges"), list) else []):
        if not isinstance(e, dict) or len(kanten) >= MAX_GRAPH_EDGES:
            continue
        art = _as_text(e.get("kind"))
        quelle, ziel = _as_text(e.get("source")), _as_text(e.get("target"))
        if art not in EDGE_KINDS or quelle not in kennungen or ziel not in kennungen:
            continue
        kanten.append({"id": _as_text(e.get("id"))[:300] or f"e{len(kanten)}",
                       "source": quelle, "target": ziel, "kind": art})
    return {"seed": _as_text(roh.get("seed"))[:300],
            "nodes": knoten, "edges": kanten}


# The announcement line ("gleich kommt was", gateway/stage_state.py). The
# consumer writes at most three and caps the label itself; these are the
# same guards once more on the reading side, because this module trusts no
# field of the file -- a hand-edited or older/newer file must not be able
# to put a paragraph, or a fourth line, into the header zone.
PENDING_KINDS = ("topic", "main", "research", "document", "question",
                 "decision", "entry")
MAX_PENDING_SHOWN = 3
MAX_PENDING_LABEL = 60


def pending_part(raw: object) -> list[dict]:
    """The announcements as plain data: `id`, `kind`, `label` -- nothing else.

    A LIST, not HTML, unlike every other header piece: the page reconciles
    the lines by id (a line that already morphs must not restart on the
    next poll), so it needs the entries, not a finished string. That makes
    the page responsible for escaping, and it takes that on by writing the
    label with `textContent` only. So the label leaves here UNescaped --
    escaping it here as well would put a visible `&amp;` on the stage.

    Keys are copied by name, never by spreading the entry: whatever else
    sits in the file (a `since`, or a field a later writer adds) does not
    reach the page. An unknown kind or an id that is not `P<n>` drops the
    whole entry -- the id is the key the page reconciles on.
    """
    if not isinstance(raw, list):
        return []
    parts: list[dict] = []
    for e in raw:
        if len(parts) >= MAX_PENDING_SHOWN:
            break
        if not isinstance(e, dict):
            continue
        entry_id = _as_text(e.get("id"))
        kind = _as_text(e.get("kind"))
        label = _as_text(e.get("label")).strip()
        if not re.fullmatch(r"P\d{1,9}", entry_id) or kind not in PENDING_KINDS:
            continue
        # Control characters out, one line: the page shows it in a
        # one-line monospace row.
        label = re.sub(r"[\x00-\x1f\x7f]+", " ", label)[:MAX_PENDING_LABEL]
        if not label.strip():
            continue
        parts.append({"id": entry_id, "kind": kind, "label": label.rstrip()})
    return parts


def image_part(raw: object) -> dict | None:
    """The image on request as plain data: `id` (16 hex) and `caption`,
    copied by name. The page builds the URL itself (`image/<id>` + token)
    and writes the caption with `textContent`."""
    if not isinstance(raw, dict):
        return None
    image_id = _as_text(raw.get("id"))
    if not stage_image.ID_RE.fullmatch(image_id):
        return None
    caption = re.sub(r"[\x00-\x1f\x7f]+", " ",
                     _as_text(raw.get("caption")))[:stage_image.MAX_CAPTION]
    return {"id": image_id, "caption": caption}


def image_dirs() -> list:
    """Where an image may come from: the project folder and every
    COTHINKER_EXTRA_DIRS entry -- the same list the consumer checks."""
    extra = [p.strip() for p in os.environ.get(
        "COTHINKER_EXTRA_DIRS", "").split(":") if p.strip()]
    return stage_image.allowed_dirs(PROJECT, extra)


_image_cache: dict = {}


def image_file(image_id: str) -> Path | None:
    """The file behind `/image/<id>` -- ONLY the image buehne.json shows
    right now, and only after the path check again (a hand-edited stage
    file must not make this route a file reader)."""
    current = image_part(read_stage().get("image"))
    if current is None or current["id"] != image_id:
        return None
    dirs = image_dirs()
    hit = _image_cache.get(image_id)
    if hit is not None and stage_image.validate(str(hit), dirs):
        return hit
    hit = stage_image.find_by_id(image_id, dirs)
    if hit is not None:
        _image_cache[image_id] = hit
    return hit


# The agenda zone (gateway/consumer.py::AgendaController, gateway/
# stage_state.py::agenda_payload). Plain data, like `pending_part` above,
# for the same reason: `item_started_at` and `meeting_deadline` are the
# clocks the PAGE counts down from, so the page needs the numbers, not a
# rendered string -- and like `pending`, the zone is deliberately left
# out of `fingerprint()` (see there): advancing the current item must
# not run the swap/scroll logic that exists for the MAIN surface.
MAX_AGENDA_TEXT = 200
MAX_AGENDA_PEOPLE = 20


def _agenda_text(wert: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", _as_text(wert))
    return text.strip()[:MAX_AGENDA_TEXT]


def _agenda_number(wert: object) -> float | None:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return float(wert)


def agenda_part(raw: object) -> dict | None:
    """The agenda zone as plain data, or None -- no `agenda.md` this
    session, or a file too foreign to show. Never raises: the consumer
    writes this from a process reading speech, and an older/newer stage
    page must keep working against a file this one wrote.
    """
    if not isinstance(raw, dict):
        return None
    total = _int(raw.get("total"), 0)
    if total <= 0:
        return None
    current = _int(raw.get("current"), 0)
    if current <= 0:
        return None
    next_n = _int(raw.get("next"), 0) if raw.get("next") is not None else 0
    has_next = next_n > 0
    people_raw = raw.get("people")
    people = ([_agenda_text(p) for p in people_raw[:MAX_AGENDA_PEOPLE]]
             if isinstance(people_raw, list) else [])
    people = [p for p in people if p]
    speaker = _agenda_text(raw.get("speaker")) if raw.get("speaker") else ""
    next_speaker = (_agenda_text(raw.get("next_speaker"))
                   if raw.get("next_speaker") else "")
    budget = raw.get("item_budget_s")
    return {
        "current": current,
        "current_text": _agenda_text(raw.get("current_text")),
        "next": next_n if has_next else None,
        "next_text": _agenda_text(raw.get("next_text")) if has_next else None,
        "total": total,
        "item_started_at": _agenda_number(raw.get("item_started_at")),
        "item_budget_s": _int(budget, 0) if budget is not None else None,
        "meeting_deadline": _agenda_number(raw.get("meeting_deadline")),
        "people": people,
        "speaker": speaker or None,
        "next_speaker": next_speaker or None,
    }


def graph_part_stub(seed: str) -> str:
    """An older graph position, without its drawing (C2, Variante 1).

    Full nodes/edges only ride with the NEWEST graph position
    (gateway/stage_state.py:_history_row) -- an older one would
    otherwise multiply the ~160 kB one graph payload measures by up to
    MAX_HISTORY, the exact overshoot the review measured (1 916 705
    bytes for 20 positions, 2.7x the 700 000-byte bound that measurement
    was checked against at the time). The seed's name is the only thing
    worth keeping: it says what note this position was about, without
    the drawing that made it expensive.
    """
    text = (f"Aelterer Graph-Stand ({html.escape(seed)}) nicht "
            "gespeichert \u2014 nur der neueste Graph wird gezeichnet."
            if seed else
            "Aelterer Graph-Stand nicht gespeichert \u2014 nur der "
            "neueste Graph wird gezeichnet.")
    return f'<div class="graf-stub">{text}</div>'


def graph_part(graph: dict) -> str:
    """One graph surface as a container the page script draws into.

    The payload rides in an ATTRIBUTE, not in a script tag: the page
    swaps `#content` wholesale (markdown today, a graph now), and this way
    a graph position needs no second path through the swap logic. The
    attribute is HTML-escaped, so a label out of a file name cannot end
    the attribute and open a tag.
    """
    nutz = html.escape(json.dumps(graph, ensure_ascii=False), quote=True)
    return (f'<div class="graf" data-graph="{nutz}">'
            f'<noscript>Der Graph braucht JavaScript.</noscript></div>')


def stage_parts() -> dict:
    """The stage in individually swappable pieces (schema v2).

    `headline` stays plain text: the page sets it via `textContent` and
    escapes it itself. Double-escaping would turn "Budget & Personal" into
    "Budget &amp; Personal". Everything else leaves here as finished HTML,
    escaped first and marked up second — the file is written from speech
    recognition and a language model, so nothing in it is trusted.
    """
    b = read_stage()
    namen = {q.stem for q in list_files()}

    # A1-Minimal (operator, 2026-09-21): the right column always shows the
    # running session's protocol, rendered in full -- "nie gekuerzt", the
    # consumer already writes this file, the stage only READS it (no
    # second writer, see A1 in docs/roadmap.md). Same render+cache path
    # as `/file/<name>`, so this costs nothing extra beyond the first
    # render after each write.
    protokoll_pfad = current_protocol_file()
    protokoll_html = (render_file(protokoll_pfad, namen, spalte=True)[0]
                       if protokoll_pfad is not None else "")

    status = b.get("status")
    status = status if isinstance(status, dict) else {}

    main = b.get("main")
    main = main if isinstance(main, dict) else {}
    collapsed_lines = _int(main.get("collapsed_lines"), 0)
    collapsed = ""
    if collapsed_lines > 0:
        # Overflow is collapsed, not deleted: deleting would shift what is
        # left under the reading eye, and it is not reversible for the
        # reader. The page keeps the open/closed state across swaps.
        collapsed = (
            f'<details><summary>{collapsed_lines} weitere Zeilen</summary>'
            f'{md(_as_text(main.get("collapsed_markdown")), namen, MAX_STAGE_CHARS)}'
            f'</details>')

    decisions = b.get("decisions")
    decisions = decisions if isinstance(decisions, list) else []
    chips = []
    for d in decisions:
        if not isinstance(d, dict):
            continue
        # `data-added` is the timestamp, not a verdict: the page decides in
        # the browser whether an entry is still "neu" and drops the mark
        # after a few seconds. A server-side mark would have to be
        # rewritten on a clock -- and would move the fingerprint with it.
        chips.append(f'<span data-added='
                     f'"{html.escape(_as_text(d.get("added_at")))}">'
                     f'<b>{html.escape(_as_text(d.get("time")))}</b> '
                     f'{html.escape(_as_text(d.get("text")))}</span>')

    raw_notices = b.get("notices")
    raw_notices = raw_notices if isinstance(raw_notices, list) else []
    notices = "".join(_notice_line(n, "conflict")
                      for n in raw_notices if isinstance(n, dict))

    q = b.get("question")
    question = _notice_line(q, "question") if isinstance(q, dict) else ""

    parked = _parked_line(b.get("parked_entries"))

    verlauf = b.get("history")
    verlauf = verlauf if isinstance(verlauf, list) else []
    letzte_position = verlauf[-1] if verlauf and isinstance(verlauf[-1],
                                                            dict) else {}

    ziele = b.get("links")
    ziele = ziele if isinstance(ziele, list) else []
    knoepfe = []
    for z in ziele:
        if not isinstance(z, dict) or not z.get("file"):
            continue
        datei = _as_text(z.get("file"))
        label = _as_text(z.get("label")) or datei
        knoepfe.append(f'<a href="file/{html.escape(quote(datei, safe=""))}">'
                       f'{html.escape(label)}</a>')

    return {
        "status": html.escape(_as_text(status.get("text"))[:300]),
        # Operational trouble sits in a line of ITS OWN, under the
        # operating line. It used to replace it — and because a conflict
        # file does not remove itself, one warning at ten o'clock cost the
        # round the answer to "did you hear me" for the rest of the
        # meeting. Both fit now.
        #
        # `sessions/` AND `sitzungen/` next to each other is a
        # second source for this line -- session_dirs() already reads both
        # and merges them without preferring one, so the room should see
        # that, not just the operator's log. The consumer's own warning
        # (a real conflict during the meeting) wins if both apply at once;
        # this one is informational, not urgent.
        "warning": html.escape(_as_text(status.get("warning"))[:300])
                   or (f"\u2139\ufe0f Ordnernamen {SESSIONS_DIR}/ und "
                       f"{SESSIONS_DIR_ALT}/ liegen beide vor \u2014 "
                       f"beide werden gelesen."
                       if beide_ordnernamen_da() else ""),
        "ticking": bool(status.get("ticking")),
        "since": _as_text(status.get("since")),
        "mirror": html.escape(_as_text(b.get("mirror")).strip()[:300]),
        # A substantive call-out answer, kept on its own
        # panel -- outlives `mirror` (60s) and survives a topic change
        # (unlike `main`). Not truncated to mirror's 300 chars: this is
        # meant to be read, not glanced at.
        "call_answer": html.escape(_as_text(b.get("call_answer")).strip()[:2000])
                       .replace("\n", "<br>"),
        "headline": _as_text(b.get("headline"))[:200],
        "main": (graph_part(clean_graph(letzte_position.get("graph")))
                 if letzte_position.get("kind") == "graph"
                 else structure_first(_as_text(main.get("markdown")), namen)),
        "collapsed": collapsed,
        "history": history_parts(b, namen),
        "decisions": '<span class="sep">·</span>'.join(chips),
        "notices": notices,
        "question": question,
        "parked": parked,
        "links": (f'<div class="lab">Vertiefen</div>{"".join(knoepfe)}'
                  if knoepfe else ""),
        "protokoll": protokoll_html,
        # Deliberately NOT in `fingerprint()`: see there, and the call next
        # to the trouble line in the page script's `tick()`.
        "pending": pending_part(b.get("pending")),
        # Out of the fingerprint like `pending`: the page applies it on
        # every poll and keys the overlay on the id.
        "image": image_part(b.get("image")),
        "age_visible_after_s": _int(b.get("age_visible_after_s"), 20),
        "follow_up_after_s": _int(b.get("follow_up_after_s"), 120),
        # Deliberately NOT in `fingerprint()`, like `pending` above --
        # see `agenda_part()`'s own docstring.
        "agenda": agenda_part(b.get("agenda")),
    }


def fingerprint(teile: dict) -> str:
    """Fingerprint of the stage content — only changes on a real change.

    Everything that carries a clock is deliberately EXCLUDED: `since`, the
    ticking duration, and the operating line's TEXT. The dominant state is
    `listening`, whose line reads `🎧 hört zu · zuletzt HH:MM:SS` — a value
    with second resolution. In the fingerprint it would report a change on
    every poll and run the swap logic (pulse, `details` restore,
    `window.scrollTo`) continuously, which is the exact opposite of what
    this whole mechanism is for.

    Nothing is lost by leaving it out: the polling script applies
    `status`, `ticking` and `since` and calls `paint()` BEFORE it compares
    the fingerprint, so the operating line was never gated by it.

    The `data-raised` and `data-added` timestamps inside the notice and
    decision markup are NOT clocks in this sense: they are fixed
    properties of an entry and only change when an entry does. The ageing
    of both happens in the browser, off these values.

    The history is IN, for the same reason: it moves only when a position
    is added or the cap drops one, and both are changes the page has to
    hear about even while somebody is paged back — that is when it has to
    say "neu: …" instead of jumping. It carries no clock: `set_at` stays
    out of the rendered pieces entirely.

    The announcements (`pending`) are OUT, like the trouble line. They are
    about now: one has to be visible on the first poll after the consumer
    wrote it, not whenever something else on the surface moves -- and
    coming and going every few seconds, they must not run the swap logic
    (pulse, scroll restore) each time. The page applies them on every poll,
    before it looks at the fingerprint, and reconciles them by id itself.
    """
    roh = "".join(str(teile[k]) for k in (
        "mirror", "call_answer", "headline", "main", "collapsed",
        "decisions", "notices", "question", "parked", "links",
        "history", "protokoll")).encode()
    return hashlib.sha256(roh).hexdigest()[:16]


def _stage_key() -> tuple | None:
    """The identity of what the stage shows: `buehne.json`'s and the
    running protocol's (path, mtime_ns, size). None when there is no
    `buehne.json` on disk -- no stable stat, so nothing may be cached."""
    try:
        p = stage_file()
        st = p.stat()
        schluessel = [(str(p), st.st_mtime_ns, st.st_size)]
    except OSError:
        return None
    protokoll_pfad = current_protocol_file()
    if protokoll_pfad is not None:
        try:
            pst = protokoll_pfad.stat()
            schluessel.append((str(protokoll_pfad), pst.st_mtime_ns,
                               pst.st_size))
        except OSError:
            schluessel.append((str(protokoll_pfad), None, None))
    return tuple(schluessel)


def stage_parts_cached() -> tuple[dict, str]:
    """`(teile, mark)`, recomputed only when `buehne.json` or the running
    protocol actually changed.

    Mirrors `render_file()`'s cache below, one level up: that one caches
    a single project file by its own mtime+size, this one caches the
    whole rendered stage by `buehne.json`'s (and, since A1-Minimal, the
    current protocol file's) mtime+size. Without the protocol in the key,
    an update to `protokoll.md` alone -- the consumer writes it far more
    often than it replaces `buehne.json` -- would sit uncached forever,
    and the right column would silently stop following the meeting.
    Falling back to `_last_good` (no file on disk, e.g. right after a
    write mid-flight) means there is no stable stat to key the cache on
    -- in that case every call recomputes, same as before this cache
    existed.
    """
    global _parts_cache
    schluessel = _stage_key()
    if schluessel is not None:
        with _lock:
            if _parts_cache is not None and _parts_cache[0] == schluessel:
                return _parts_cache[1], _parts_cache[2]
    teile = stage_parts()
    mark = fingerprint(teile)
    if schluessel is not None:
        with _lock:
            _parts_cache = (schluessel, teile, mark)
    return teile, mark


_graph_lock = threading.Lock()          # one build at a time, see below


def _session_resolve(name: str) -> str | None:
    """A wikilink target -> its node id (`file:<rel>` / `vault:<rel>`),
    or None -- the `resolve` contract of `build_session_graph()`, which
    derives the href from the id itself.

    The project folder first -- the same files the stage's own wikilinks
    open via `/file/` -- then the vault, through `NoteIndex.resolve`,
    which answers None for a missing AND for an ambiguous name. Nothing
    is guessed, nothing gets a node without a file behind it.
    """
    name = str(name or "").strip()
    if not name:
        return None
    p = find_file(name)
    if p is not None:
        return "file:" + rel_name(p)
    index, _ = vault_note_index()
    note = index.resolve(name) if index is not None else None
    if note is not None:
        return "vault:" + note.rel
    return None


def _build_session_graph(hysteresis) -> dict:
    """One build of the graph tab's payload from the files as they are."""
    buehne = read_stage()
    protokoll_pfad = current_protocol_file()
    text = ""
    if protokoll_pfad is not None:
        try:
            text = protokoll_pfad.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    if protokoll_pfad is not None:
        rel = rel_name(protokoll_pfad)
        session_id = "file:" + rel
        # The protocol's own title, from ABOVE `## Rohtranskript` only;
        # else the session folder's name (or the flat file's stem).
        titel = next((z[2:].strip() for z in protocol_head(text).splitlines()
                      if z.startswith("# ") and z[2:].strip()), "")
        label = titel or (protokoll_pfad.parent.name
                          if protokoll_pfad.name == "protokoll.md"
                          else protokoll_pfad.stem)
    else:
        session_id, label = "session", "Sitzung"
    index, edges = vault_note_index()
    return build_session_graph(
        topics_from_stage(buehne, text), session_id=session_id,
        session_label=label, resolve=_session_resolve, index=index,
        edges=edges, hysteresis=hysteresis)


def session_graph_payload() -> dict:
    """The graph tab's payload, rebuilt only when the session or the
    vault's content changed.

    Keyed like `stage_parts_cached()` (buehne.json + running protocol)
    plus the vault scan's content digest (`vault_scan_digest()`, NOT its
    timestamp -- see the comment on `_vault_scan` for why: the vault is
    rescanned every VAULT_TTL regardless of whether anything changed, and
    a timestamp in the key made the graph rebuild -- and the hysteresis
    advance -- on that clock instead of on an actual change). Builds are
    serialised by their own lock: two tabs polling the same change must
    not advance the hysteresis twice. Without a stable key (no
    `buehne.json` on disk) every call builds, and then WITHOUT the
    hysteresis -- a grace round per poll would be a clock, not a change.
    When the running session (the seed) changes, the hysteresis starts
    over: grace notes never carry from one meeting into the next.
    """
    global _graph_cache, _graph_hysteresis, _graph_seed
    with _graph_lock:
        schluessel = _stage_key()
        if schluessel is not None:
            schluessel = schluessel + (vault_scan_digest(),)
            with _lock:
                if _graph_cache is not None and _graph_cache[0] == schluessel:
                    return _graph_cache[1]
        # A new meeting starts with a clean grace list: the previous
        # one's notes are not "just dropped" from this one, they were
        # never part of it.
        pfad = current_protocol_file()
        saat = "file:" + rel_name(pfad) if pfad is not None else "session"
        with _lock:
            if saat != _graph_seed:
                _graph_hysteresis = Hysteresis()
                _graph_seed = saat
        nutz = _build_session_graph(
            _graph_hysteresis if schluessel is not None else None)
        if schluessel is not None:
            with _lock:
                _graph_cache = (schluessel, nutz)
        return nutz


def render_file(p: Path, namen: set, spalte: bool = False) -> tuple[str, str]:
    """A project file as HTML plus fingerprint, cached by modification time.
    Without this, every viewer would re-render the whole protocol every
    3 s — on two cores that's the most expensive item.

    `spalte=True` is the right column's call: a v2 protocol (detected by
    content, on every render) gets `render_protocol_v2()`, anything else
    the plain `md()` -- byte-identical to `spalte=False`. `/file/<name>`
    never passes it and stays plain Markdown. The flag is part of the
    cache key, so the two renders of one file never answer for each other.
    """
    try:
        st = p.stat()
        schluessel = (str(p), st.st_mtime_ns, st.st_size, len(namen))
    except OSError:
        schluessel = (str(p), 0, 0, 0)
    if spalte:
        schluessel += ("spalte",)
    with _lock:
        treffer = _render_cache.get(schluessel)
    if treffer:
        return treffer
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = "*Datei nicht lesbar.*"
    inhalt = (render_protocol_v2(text, namen) if spalte and is_v2(text)
              else md(text, namen))
    erg = (inhalt, hashlib.sha256(inhalt.encode()).hexdigest()[:16])
    with _lock:
        if len(_render_cache) > 32:
            _render_cache.clear()
        _render_cache[schluessel] = erg
    return erg


STYLE = """
:root{--bg:#14161a;--fg:#e8e6e3;--dim:#9aa0a6;--akz:#7cc4a4;--warn:#e0a458;--lin:#2a2e35}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:17px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}
header{position:sticky;top:0;background:rgba(15,17,20,.94);backdrop-filter:blur(8px);
 border-bottom:1px solid var(--lin);padding:.75rem 1.4rem;display:flex;gap:1rem;
 align-items:baseline;flex-wrap:wrap;z-index:9}
header a.zur{color:var(--akz);text-decoration:none;font-size:.9rem}
header b{color:var(--akz);font-size:1.05rem}
.puls{margin-left:auto;display:flex;align-items:center;gap:.45rem;
 color:#5f666e;font-size:.78rem}
.punkt{width:7px;height:7px;border-radius:50%;background:#3d8b6a;
 transition:background .3s,transform .3s}
.punkt.an{background:var(--akz);transform:scale(1.5)}
.punkt.weg{background:#8b3d3d}
main{max-width:820px;margin:0 auto;padding:2rem 1.6rem 5rem}
h1,h2,h3{line-height:1.25;margin:1.5rem 0 .6rem}
h1{font-size:1.75rem;margin-top:0}
h2{font-size:1.35rem;border-bottom:1px solid var(--lin);padding-bottom:.3rem}
h3{font-size:1.1rem;color:var(--akz)}
p{margin:.6rem 0} ul{margin:.5rem 0;padding-left:1.3rem} li{margin:.25rem 0}
a{color:var(--akz)} .tot{color:var(--dim)}
code{background:#22262c;padding:.12em .38em;border-radius:4px;font-size:.88em}
pre{background:#0f1114;border:1px solid var(--lin);padding:.8rem 1rem;
 border-radius:6px;overflow:auto;font-size:.85rem}
table{border-collapse:collapse;margin:.9rem 0;width:100%}
td{border:1px solid var(--lin);padding:.45rem .65rem;font-size:.93rem;vertical-align:top}
tr:first-child td{background:#1a1e24;font-weight:600}
hr{border:0;border-top:1px solid var(--lin);margin:1.5rem 0}
.lab{color:#5f666e;font-size:.72rem;text-transform:uppercase;
 letter-spacing:.09em;margin-bottom:.6rem}
.fluss{color:var(--dim);font-size:.85rem;margin-top:2rem}
details{margin:.6rem 0;border:1px solid var(--lin);border-radius:6px;padding:.2rem .8rem}
summary{cursor:pointer;padding:.4rem 0;color:var(--akz);font-size:.94rem}
/* Gentle fade-in of NEW content — no flash of the whole page */
.frisch{animation:auf .5s ease-out}
@keyframes auf{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.frisch{animation:none}.punkt{transition:none}
 #pending .pending-line.resting,#pending .pending-line.leaving{animation:none}}
@media(max-width:640px){main{padding:1.2rem}body{font-size:16px}}

/* ---- The stage, in the layout approved on 2026-09-08: sidebar left as
   its own column, header zone with four rows, main area free below.
   The rules hang off #stage, not off body — the file view uses the same
   stylesheet and keeps its plain header/main flow. */
#stage{display:flex;min-height:100vh}
#side{width:220px;flex:0 0 220px;border-right:1px solid var(--lin);
 background:#101216;padding:1.1rem .9rem;position:sticky;top:0;height:100vh;
 overflow-y:auto}
#side .brand{color:var(--akz);font-size:1.02rem;font-weight:600;
 margin-bottom:1.4rem;display:block}
#side .lab{color:#5f666e;font-size:.68rem;text-transform:uppercase;
 letter-spacing:.09em;margin:1.2rem 0 .5rem}
#side a{display:block;margin:0 0 .35rem;padding:.4rem .55rem;background:#1a1e24;
 border:1px solid var(--lin);border-radius:5px;text-decoration:none;
 font-size:.84rem;line-height:1.35;transition:border-color .2s}
#side a:hover{border-color:var(--akz)}
#right{flex:1;min-width:0;display:flex;flex-direction:column}
#head{position:sticky;top:0;z-index:9;background:rgba(15,17,20,.96);
 backdrop-filter:blur(8px);border-bottom:1px solid var(--lin);
 padding:.55rem 1.6rem .5rem}
#ops{color:#6b7280;font-size:.76rem;line-height:1.5;display:flex;
 align-items:center;gap:.5rem;font-variant-numeric:tabular-nums}
#ops .num{color:#8b93a1}
#ops .right{margin-left:auto;display:flex;align-items:center;gap:.45rem}
#ops .graf-link{color:var(--dim);text-decoration:none;margin-left:.8rem;
 white-space:nowrap}
#ops .graf-link:hover,#ops .graf-link:focus{color:var(--akz)}
/* The trouble line: its own row under the operating line, warning colour,
   invisible while nothing is wrong. It never takes the acknowledgement's
   place — a sync service writing along can stand for hours. */
#warn{color:var(--warn);font-size:.76rem;line-height:1.5;margin-top:.15rem;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#warn:empty{display:none}
/* The announcement line ("gleich kommt was"): one quiet monospace row per
   pending item, under the operating line. Muted green, small, a faint
   scanline -- it has to be noticed out of the corner of an eye, not
   watched. Glyphs settle into the label once, then the line only breathes
   (low amplitude, slow); when the item arrives, a short brightening and
   gone. No font download: the look is CSS alone. */
#pending{margin-top:.15rem}
/* The image on request: over everything, fit to the viewport, caption
   under it. A click closes it for this viewer; the next mirror line or
   topic closes it for everyone. */
#bild{position:fixed;top:0;left:0;width:100vw;height:100vh;margin:0;z-index:50;background:rgba(10,12,14,.94);
 display:flex;flex-direction:column;align-items:center;justify-content:center;
 padding:2vmin;box-sizing:border-box;cursor:zoom-out}
#bild[hidden]{display:none}
#bild img{max-width:96vw;max-height:calc(100vh - 5rem);object-fit:contain;
 background:#fff;border-radius:4px;box-shadow:0 0 0 1px #333}
#bild figcaption{color:#e8e8e8;font-size:1.05rem;margin-top:.7rem;
 text-align:center;max-width:90vw}
#pending:empty{display:none}
#pending .pending-line{font:.72rem/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 color:#6fa889;letter-spacing:.03em;padding:0 .35rem;white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis;text-shadow:0 0 2px rgba(111,168,137,.3);
 background:repeating-linear-gradient(to bottom,rgba(111,168,137,.06) 0,
  rgba(111,168,137,.06) 1px,transparent 1px,transparent 3px)}
#pending .pending-line.resting{animation:pendingBreathe 3.2s ease-in-out infinite}
#pending .pending-line.leaving{animation:pendingLeave .4s ease-out forwards}
@keyframes pendingBreathe{0%,100%{opacity:.9}50%{opacity:.62}}
@keyframes pendingLeave{0%{opacity:1;color:#b6e3c8}100%{opacity:0}}
/* The label for screen readers, once; the glyph frames are aria-hidden so
   a live region does not read out twenty frames of noise. */
.sr{position:absolute;width:1px;height:1px;overflow:hidden;
 clip:rect(0 0 0 0);white-space:nowrap}
/* The agenda zone: the current item in bold with its live timer, the
   next item and the speaker queue underneath, dimmed. `.agenda-over`
   (budget or meeting time run out) is the one accent colour in here --
   everything else stays as quiet as #mirror/#decisions. */
#agenda{margin-top:.3rem;font-size:.8rem;line-height:1.5}
#agenda:empty{display:none}
#agenda .agenda-row{color:var(--fg)}
#agenda #agenda-timer{color:var(--dim);font-variant-numeric:tabular-nums;
 margin-left:.4rem}
#agenda #agenda-timer.agenda-over{color:#f85149}
#agenda .agenda-next,#agenda .agenda-who{color:var(--dim)}
#agenda .agenda-meeting{color:var(--dim);font-variant-numeric:tabular-nums}
#agenda .agenda-meeting.agenda-over{color:#f85149}
#head #headline{color:var(--fg);font-size:.9rem;font-weight:600;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:45%}
#head #headline:empty{display:none}
#mirror{color:var(--dim);font-size:.8rem;line-height:1.5;margin-top:.3rem;
 border-left:2px solid var(--akz);padding-left:.6rem;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
#mirror:empty{display:none}
/* Unlike #mirror (one dim, truncated line), a call's
   substantive answer is meant to be read -- full width, wraps, no
   ellipsis, legible font size. Still quiet in colour: it is an answer,
   not an alert. */
#call-answer{color:var(--fg);font-size:.85rem;line-height:1.5;margin-top:.4rem;
 border-left:2px solid var(--akz);padding-left:.6rem;white-space:normal}
#call-answer:empty{display:none}
#decisions{color:#7f8794;font-size:.73rem;line-height:1.5;margin-top:.3rem;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#decisions:empty{display:none}
#decisions b{color:#9aa6b4;font-weight:500}
#decisions .sep{color:#454b54;margin:0 .45rem}
/* A freshly appended decision is marked for a few seconds and then falls
   back to the normal colour — same `frisch` class the swap uses, so a new
   entry fades in and stands out without a second mechanism. */
#decisions span{transition:color .6s ease}
#decisions span.frisch{color:var(--akz)}
#decisions span.frisch b{color:var(--akz)}
#notices{margin-top:.3rem}
#notices:empty{display:none}
.notice{font-size:.76rem;line-height:1.55;color:#9aa0a6;display:flex;
 align-items:baseline;gap:.45rem;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.notice .mark{color:#c96a5a;flex:0 0 auto}
.notice strong{color:#c99b8a;font-weight:500}
.notice .age{color:#4d535c;font-size:.7rem;margin-left:.3rem}
.notice.nachfassen .age{color:var(--warn)}
.notice.question .mark,.notice.question strong{color:var(--warn)}
.notice.settled{opacity:.75}
.notice.settled .mark,.notice.settled strong{color:var(--akz)}
/* Expired is not resolved: dimmed and neutral, never the green receipt.
   Nobody answered it — it only moved to `## Offene Fragen`. */
.notice.abgelaufen{opacity:.6}
.notice.abgelaufen .mark,.notice.abgelaufen strong{color:var(--dim);
 font-weight:500}
/* The parked third notice: dimmed, neutral mark — it is not a conflict,
   only the note that something was held back. */
.notice.parked{opacity:.6}
.notice.parked .mark,.notice.parked strong{color:var(--dim);font-weight:500}
#area{flex:1;padding:1.8rem 1.6rem 5rem;max-width:760px}
#area h2{margin-top:0}
#collapsed:empty{display:none}
/* A1-Minimal (operator, 2026-09-21): the right column, the running protocol.
   ALWAYS the full file, never cut -- unlike #area it gets its own scroll
   container instead of growing the page, so the left side stays short
   while the protocol can be arbitrarily long. */
#protokoll-col{width:380px;flex:0 0 380px;border-left:1px solid var(--lin);
 background:#101216;padding:1.3rem 1.5rem 3rem;position:sticky;top:0;
 height:100vh;overflow-y:auto}
#protokoll-col .lab{position:sticky;top:-1.3rem;background:#101216;
 padding-top:1.3rem;margin-bottom:.7rem;z-index:1;display:flex;
 align-items:center;justify-content:space-between;gap:.6rem}
#protokoll-col h1{font-size:1.25rem}
#protokoll-col h2{font-size:1.05rem}
/* Protocol v2: the derived view stays in sight while the topics scroll
   under it; its own background, capped so it never eats the column. */
#protokoll-col .sicht{position:sticky;top:2.4rem;z-index:0;background:#171a1f;
 border:1px solid var(--lin);border-radius:6px;padding:.1rem .8rem;
 max-height:40vh;overflow-y:auto;font-size:.9rem}
#protokoll-col .sicht h2{margin:.4rem 0 .2rem}
#protokoll-col .sicht h3{font-size:.92rem;margin:.5rem 0 .1rem;color:var(--dim)}
#protokoll-col details.thema summary .n,#protokoll-col .zeit{color:var(--dim)}
#protokoll-col .eintraege{padding-left:1rem;font-size:.92rem}
.tag{display:inline-block;font-size:.72rem;line-height:1.3;padding:0 .35rem;
 border-radius:4px;border:1px solid var(--lin);color:var(--dim)}
.tag-beschluss{color:var(--akz);border-color:var(--akz)}
.tag-aufgabe{color:var(--warn);border-color:var(--warn)}
.tag-frage{color:#8fb3e0;border-color:#8fb3e0}
#protokoll-toggle{background:#1a1e24;color:var(--dim);border:1px solid var(--lin);
 border-radius:5px;padding:.15rem .55rem;font:inherit;font-size:.74rem;
 cursor:pointer;white-space:nowrap}
#protokoll-toggle:hover{border-color:var(--akz);color:var(--fg)}
/* Befund 2: only the content collapses now, not the whole
   aside -- the header (label + toggle) stays, so there is always a way
   back. Collapsed, the column shrinks to the header's width instead of
   keeping the full 380px reserved for nothing. */
#protokoll-col.protokoll-zu{width:auto;flex:0 0 auto;padding-right:1.3rem}
[hidden]{display:none!important}
/* The history bar, one row above the main area. It KEEPS ITS HEIGHT while
   it is empty: it appears as soon as a second surface exists, and taking
   its row at that moment would push the main area down under the reading
   eye — the one movement section 4.1 rules out. So the row always stands
   and only its content changes. */
#verlauf{min-height:1.8rem;display:flex;align-items:center;gap:.45rem;
 font-size:.76rem;color:#6b7280;margin-bottom:.5rem;flex-wrap:wrap}
#verlauf button{background:#1a1e24;color:var(--dim);border:1px solid var(--lin);
 border-radius:5px;padding:.05rem .5rem;font:inherit;line-height:1.6;
 cursor:pointer}
#verlauf button:hover:not(:disabled){border-color:var(--akz);color:var(--fg)}
#verlauf button:disabled{opacity:.3;cursor:default}
#verlauf a{color:var(--akz);text-decoration:none}
#verlauf .neu{color:var(--akz)}
/* Paged back is not the current view and must not look like one. */
#verlauf.zurueck{color:var(--warn)}
/* Portrait / phone: header, then the content, and the links last —
   the content is the most important thing. */
@media(max-width:720px){
  #stage{flex-direction:column}
  #right{order:1}
  #side{order:2;width:auto;flex:none;height:auto;position:static;
   border-right:0;border-top:1px solid var(--lin);padding:1rem 1.1rem 1.4rem}
  #side .brand{display:none}
  #side a{display:inline-block;margin:0 .35rem .35rem 0}
  #head{padding:.5rem 1.1rem}
  #area{padding:1.4rem 1.1rem 2rem}
  /* Below the main content, own scroll, not a second full-height column
     next to a narrow left side -- see A1-Minimal's "schmaler Bildschirm"
     requirement. */
  #protokoll-col{order:3;width:auto;flex:none;height:60vh;position:static;
   border-left:0;border-top:1px solid var(--lin);padding:1.1rem 1.1rem 2rem}
}
/* The graph takes the free area below the header zone. A fixed height,
   because the layout engine needs one and a growing container would push
   the page around while the round is reading. */
.graf{height:62vh;min-height:320px;background:#0e1116;border:1px solid var(--lin);border-radius:6px}
/* An older graph position that had its drawing stripped for size (C2,
   Variante 1) -- a plain notice, not a container graph.js tries to draw
   into. */
.graf-stub{padding:1.4rem;color:var(--dim);font-size:.9rem;
 background:#0e1116;border:1px solid var(--lin);border-radius:6px}

/* --- Participant parts (join dialog, queue buttons, share view) ------
   The button bar sits bottom right under the image overlay (#bild, 50);
   the two dialogs sit above everything (60), so a participant who still
   has to give a name is never blocked by an image. */
#teiln{position:fixed;right:.8rem;bottom:.8rem;z-index:40;display:flex;
 flex-wrap:wrap;gap:.4rem;align-items:center;justify-content:flex-end;
 max-width:calc(100vw - 1.6rem);font-size:.85rem}
#teiln button{font:inherit;color:#e8e8e8;background:#1d2128;
 border:1px solid #3a4150;border-radius:6px;padding:.45rem .8rem;cursor:pointer}
#teiln button:disabled{opacity:.5;cursor:default}
#teiln-status{color:var(--dim);font-size:.8rem}
#join-dlg,#share-dlg{position:fixed;top:0;left:0;width:100vw;height:100vh;
 z-index:60;background:rgba(10,12,14,.94);display:flex;align-items:center;
 justify-content:center;padding:2vmin;box-sizing:border-box}
#join-dlg[hidden],#share-dlg[hidden]{display:none}
.teiln-box{background:#171a1f;border:1px solid #3a4150;border-radius:8px;
 padding:1.2rem 1.4rem;max-width:min(26rem,94vw);color:#e8e8e8;
 display:flex;flex-direction:column;gap:.7rem;align-items:stretch}
.teiln-box input{font:inherit;padding:.5rem;border-radius:6px;
 border:1px solid #3a4150;background:#0e1116;color:#e8e8e8}
.teiln-box button{font:inherit;color:#e8e8e8;background:#1d2128;
 border:1px solid #3a4150;border-radius:6px;padding:.5rem .9rem;cursor:pointer}
#join-err{color:var(--warn);font-size:.85rem}
#join-err:empty{display:none}
#share-link{word-break:break-all;font-family:monospace;font-size:.8rem;
 user-select:all}
#share-qr{background:#fff;padding:.6rem;border-radius:4px;align-self:center;
 line-height:0}
/* --- end participant parts --- */
"""

# No location.reload(): only the changed section is swapped.
SCRIPT = """
// MM-J: where a topic click from the graph tab lands in the protocol
// column. Pure (elements in document order in, one of them or null out),
// so tests/test_stage_js.py runs it under node as shipped. Labels arrive
// cut to LABEL_MAX with a trailing "…" -- that is dropped and the rest
// matched as a prefix. Nothing at or below the `Rohtranskript` heading is
// a target: a time like 10:40 appears there far more often than above.
function stageTopicTarget(els, title, time){
  var vor=[];
  for(var i=0;i<els.length;i++){
    var e=els[i];
    if(/^H[1-6]$/i.test(e.tagName||'') &&
       (e.textContent||'').trim()==='Rohtranskript') break;
    vor.push(e);
  }
  function kern(s){
    if(typeof s!=='string') return '';
    s=s.trim();
    if(s.slice(-1)==='…') s=s.slice(0,-1).trim();
    return s;
  }
  function suche(s){
    s=kern(s);
    if(!s) return null;
    for(var j=0;j<vor.length;j++){
      if((vor[j].textContent||'').indexOf(s)>=0) return vor[j];
    }
    return null;
  }
  return suche(title)||suche(time);
}
// MM-M: a topic click in the graph tab, opened WITHOUT window.opener
// (directly, not as a popup from the stage), lands here instead as
// `/file/<protokoll>#block-<id>`. `window.SG_BLOCKS` is only set below --
// by the `/file/` route, for the running protocol only -- mapping that id
// to the same title/time the live postMessage path already matches on
// with stageTopicTarget: one matching rule, two ways in.
function sgBlockAnchorJump(){
  if (!window.SG_BLOCKS || location.hash.indexOf('#block-') !== 0) return null;
  var info=window.SG_BLOCKS[decodeURIComponent(location.hash.slice(7))];
  if (!info) return null;
  var inhaltEl=document.getElementById('content');
  var ziel=inhaltEl && stageTopicTarget(
    [].slice.call(inhaltEl.querySelectorAll('h1,h2,h3,h4,h5,h6,li,p')),
    info.title, info.time);
  if (ziel && ziel.scrollIntoView) ziel.scrollIntoView({block:'start'});
  return ziel||null;
}
window.SG_BLOCKS = null;
sgBlockAnchorJump();
// --- Participant parts: name dialog, queue buttons, share view -------
// Its own closure ahead of the page script below, so it neither depends
// on nor disturbs the polling. The `cothinker_pid` cookie is HttpOnly and
// invisible here; the sessionStorage entry is the page's own "this
// browser session has given a name" signal, and a 401 on /action (cookie
// gone) clears it and asks for the name again. The dialog can be put off
// ("Spaeter" / Escape): a second marker keeps it from reappearing on
// reload in the same browser session, so a shared display is not blocked.
// A tap on a queue button without a name opens it again and is sent once
// after the join.
(function(){
  var TOKEN=%TOKEN%, KEY='cothinker-joined-name',
      DKEY='cothinker-name-dismissed', pending=null;
  function $(id){ return document.getElementById(id); }
  var dlg=$('join-dlg'), form=$('join-form'), inp=$('join-name'),
      err=$('join-err'), wort=$('teiln-wort'), fertig=$('teiln-fertig'),
      teilen=$('teiln-teilen'), stat=$('teiln-status'),
      share=$('share-dlg'), shareLink=$('share-link'),
      shareQr=$('share-qr'), shareClose=$('share-close'),
      later=$('join-later');
  if(!dlg || !form || !inp) return;
  function post(path, obj){
    var u=TOKEN ? path+'?t='+encodeURIComponent(TOKEN) : path;
    return fetch(u,{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});
  }
  function joined(){
    try{ return !!sessionStorage.getItem(KEY); }catch(e){ return false; }
  }
  function dismissed(){
    try{ return !!sessionStorage.getItem(DKEY); }catch(e){ return false; }
  }
  function openDlg(){
    dlg.toggleAttribute('hidden', false);
    if(inp.focus) inp.focus();
  }
  function dismissDlg(){
    try{ sessionStorage.setItem(DKEY, '1'); }catch(e){}
    pending=null;
    dlg.toggleAttribute('hidden', true);
  }
  if(!joined() && !dismissed()) openDlg();
  if(later) later.addEventListener('click', dismissDlg);
  form.addEventListener('submit', async function(ev){
    ev.preventDefault();
    var name=(inp.value||'').trim();
    if(!name){ err.textContent='Bitte gib einen Namen ein.'; return; }
    try{
      var r=await post('join',{name:name});
      if(!r.ok) throw new Error('join '+r.status);
      try{ sessionStorage.setItem(KEY, name); }catch(e){}
      err.textContent='';
      dlg.toggleAttribute('hidden', true);
      if(pending){ var p=pending; pending=null; senden(p.op, p.text, true); }
    }catch(e){
      err.textContent='Das hat nicht geklappt. Bitte versuche es noch einmal.';
    }
  });
  var btns=[wort, fertig];
  function lock(on){
    btns.forEach(function(b){ if(b) b.toggleAttribute('disabled', on); });
  }
  async function senden(op, text, retry){
    if(!joined()){
      pending={op:op, text:text};
      openDlg();
      return;
    }
    lock(true);
    var wait=false;
    try{
      var r=await post('action',{intent_id:'queue:room', op:op});
      if(r.status===401){
        try{ sessionStorage.removeItem(KEY); }catch(e){}
        if(!retry) pending={op:op, text:text};
        openDlg();
      } else if(r.status===429){
        wait=true;                      // too quick: a brief pause, no message
      } else if(r.ok){
        if(stat) stat.textContent=text;
      } else if(stat){
        stat.textContent='Nicht gesendet.';
      }
    }catch(e){ if(stat) stat.textContent='Nicht gesendet.'; }
    if(wait) setTimeout(function(){ lock(false); }, 600); else lock(false);
  }
  if(wort) wort.addEventListener('click', function(){
    senden('request','Wortmeldung gesendet'); });
  if(fertig) fertig.addEventListener('click', function(){
    senden('done','Fertig gemeldet'); });
  // The shared link: the stage root with its token, as a participant has
  // to open it -- no fragment, nothing page- or person-specific.
  function shareUrl(){
    try{
      var u=new URL(document.baseURI || location.href);
      u.hash=''; u.search='';
      if(TOKEN) u.searchParams.set('t', TOKEN);
      return u.href;
    }catch(e){ return location.href; }
  }
  function shareOpen(){
    if(!share) return;
    var u=shareUrl();
    if(shareLink) shareLink.textContent=u;
    if(shareQr){
      if(typeof qrcode==='function'){
        var q=qrcode(0,'M'); q.addData(u); q.make();
        shareQr.innerHTML=q.createSvgTag(5,2);   // own output, not user text
      } else { shareQr.textContent='QR-Code nicht verfügbar'; }
    }
    share.toggleAttribute('hidden', false);
  }
  function shareShut(){ if(share) share.toggleAttribute('hidden', true); }
  if(teilen) teilen.addEventListener('click', shareOpen);
  if(shareClose) shareClose.addEventListener('click', shareShut);
  document.addEventListener('keydown', function(ev){
    if(ev && ev.key==='Escape'){
      shareShut();
      if(!dlg.hasAttribute('hidden')) dismissDlg();
    }
  });
})();
(function(){
  var mark=%MARK%, halt=false,
      punkt=document.getElementById('punkt'),
      clock=document.getElementById('clock'),
      warnEl=document.getElementById('warn'),
      opsEl=document.getElementById('ops-text');
  // `null` means the server runs without a check (--no-token); otherwise
  // every request this script triggers has to carry it or the server
  // answers 404.
  var TOKEN=%TOKEN%;
  // Published for graph.js's tap handler (grafZiel there), read lazily at
  // click time -- graph.js loads in <head>, this script runs at the end of
  // <body>, so by the time a click can happen this line has long run.
  window.STAGE_TOKEN=TOKEN;
  // A1-Minimal (operator, 2026-09-21): shown by default -- "wenn unsicher:
  // default AN, weil genau das der Wunsch ist" (Kartenauflage). Read
  // BEFORE the first paint so a reader who hid it on a previous visit
  // does not see it flash on and off.
  //
  // Befund 2 (2026-09-21): `hidden` used to sit on the WHOLE
  // `#protokoll-col`, the container the toggle button lives in -- so
  // hiding it also hid the only way back, and the state survived a
  // reload via localStorage. There was no keyboard path either
  // (`keydown` below only knows ArrowLeft/ArrowRight). Measured with
  // this exact script under node: one click, hidden=true, button gone,
  // reload still hidden -- unrecoverable without DevTools. Now only
  // `#protokoll-content` is hidden; the `.lab` header (label + button)
  // stays put, so the button that hid it can always show it again.
  var protokollCol=document.getElementById('protokoll-col');
  var protokollContent=document.getElementById('protokoll-content');
  var protokollToggle=document.getElementById('protokoll-toggle');
  var protokollAus=false;
  try{ protokollAus=localStorage.getItem('cothinker-protokoll-aus')==='1'; }
  catch(e){ /* private mode / disabled storage: default stays AN */ }
  function protokollAnwenden(){
    if(protokollContent) protokollContent.toggleAttribute('hidden', protokollAus);
    if(protokollCol) protokollCol.classList.toggle('protokoll-zu', protokollAus);
    if(protokollToggle){
      protokollToggle.textContent=protokollAus?'einblenden':'ausblenden';
    }
  }
  protokollAnwenden();
  // The raw-transcript toggle (`data-key="roh"`, `render_protocol_v2()`)
  // survives a real reload too -- its own localStorage key so the two
  // toggles never collide. Read BEFORE the first paint, same reasoning as
  // `protokollAus` above: the server's own default (collapsed) must not
  // flash before the reader's earlier choice applies. A later swap
  // replaces this element; `detailsMerken`/`detailsZurueck` below already
  // carry the reader's choice across THOSE polls -- this listener only
  // has to catch the toggle itself, once, to persist it past a reload.
  var ROH_KEY='cothinker-roh-auf';
  function rohElement(){
    var ds=document.querySelectorAll('details');
    for(var i=0;i<ds.length;i++){
      if(ds[i].getAttribute('data-key')==='roh') return ds[i];
    }
    return null;
  }
  try{
    var rohGespeichert=localStorage.getItem(ROH_KEY);
    if(rohGespeichert==='1' || rohGespeichert==='0'){
      var rohStart=rohElement();
      if(rohStart) rohStart.open=(rohGespeichert==='1');
    }
  }catch(e){ /* private mode / disabled storage: server default stands */ }
  document.addEventListener('toggle', function(ev){
    var t=ev && ev.target;
    if(t && t.getAttribute && t.getAttribute('data-key')==='roh'){
      try{ localStorage.setItem(ROH_KEY, t.open?'1':'0'); }catch(e){}
    }
  }, true);
  // The `/[?&]t=/` guard matters: on the stage page `location.search`
  // already carries the token as a query parameter, so without it the
  // URL would carry the token twice.
  function mitToken(u){
    if(!TOKEN || /[?&]t=/.test(u)) return u;
    return u+(u.indexOf('?')<0?'?':'&')+'t='+encodeURIComponent(TOKEN);
  }
  // The first paint happens before the first poll (3 s away). The server
  // already rendered the operating line, so the browser starts from what
  // is on the page instead of blanking it.
  var opsText=opsEl ? opsEl.innerHTML : '';
  var ticking=!!(opsEl && opsEl.getAttribute('data-ticking')==='1');
  var since=(opsEl && opsEl.getAttribute('data-since'))
            ? Date.parse(opsEl.getAttribute('data-since')) : null;
  var ageAfter=20, followAfter=120;
  // "Ein neu angehaengter Eintrag ist markiert (`neu`), die Markierung
  // verblasst nach ~4 s." Counted HERE, from the entry's own timestamp —
  // a server-side mark would have to move on a clock, and the clock must
  // never reach the fingerprint.
  var frischMs=4000;
  function tippt(){
    var a=document.activeElement;
    return !!(a && (a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.isContentEditable));
  }
  // Do NOT interfere while typing/selecting/menu open.
  function busy(){
    var s=window.getSelection();
    if(s && !s.isCollapsed) return true;                 // text selected
    if(tippt()) return true;
    if(document.querySelector('details[open]:hover')) return true;
    return false;
  }
  function swap(el, html){
    if(!el || el.innerHTML===html) return;
    el.innerHTML=html;
    el.classList.remove('frisch'); void el.offsetWidth; el.classList.add('frisch');
  }
  // I1: `#content` is special. Once a graph has been drawn into it,
  // `grafBooten()` marks the container with `data-auf` and cytoscape
  // writes its own `<canvas>` elements into it -- so `el.innerHTML` (what
  // `swap()` compares against) can never equal the server's HTML again,
  // even when the server sent the exact same surface. `swap()` therefore
  // saw a "change" on EVERY poll and tore the graph down and rebuilt it
  // (`randomize:true`, stage/static/graph.js), whether or not the surface
  // actually changed -- measured: `grafZeichnen` ran once per poll, not
  // once per surface. `swapInhalt()` compares against the SOURCE string
  // this script itself last set, not against what the DOM turned it
  // into, and arrives here already baked in from the server, before
  // `grafBooten()` runs the first time, so it starts from the untouched
  // string the server rendered. That start value cannot come from a DOM
  // read either, for the same reason: `.innerHTML`'s getter re-serialises
  // the parsed tree, which is not byte-identical to the source whenever
  // it held an HTML entity (`&#x27;` comes back as `'`) -- so it is
  // embedded here as the server's own bytes, next to `%MARK%`.
  var letzterInhalt=%INHALT%;
  function swapInhalt(el, html){
    html=html||'';
    if(!el || html===letzterInhalt) return;
    letzterInhalt=html;
    el.innerHTML=html;
    el.classList.remove('frisch'); void el.offsetWidth; el.classList.add('frisch');
  }
  // A graph position is a container the server sent; the drawing happens
  // here, once per container. Called after every swap of #content and
  // once on load, so the first surface is drawn before the first poll.
  function grafBooten(){
    var g=document.querySelector('#content .graf');
    if(!g || g.getAttribute('data-auf')) return;
    var roh=g.getAttribute('data-graph');
    if(!roh || !window.grafZeichnen) return;
    g.setAttribute('data-auf','1');
    try{ window.grafZeichnen(g, JSON.parse(roh)); }
    catch(e){ g.textContent='Der Graph liess sich nicht aufbauen.'; }
  }
  grafBooten();
  // The status line and the mirror change often. They are swapped WITHOUT
  // the fade — "moeglichst wenig flackernd" was the requirement.
  // The open/closed state of every `details` survives a swap. A keyed one
  // (`data-key`, the v2 protocol's topic blocks and raw transcript) is
  // matched BY KEY: a newly appended topic shifts no index. Only the
  // READER's deviation from the server's default is kept (`data-auf`
  // marks a block the server opened): an untouched block follows the
  // server, so the previous newest topic closes when a newer one arrives
  // open. Unkeyed ones keep the old rule, by position among the unkeyed.
  function detailsMerken(){
    var z={key:{},idx:[]};
    [].slice.call(document.querySelectorAll('details')).forEach(function(d){
      var k=d.getAttribute('data-key');
      if(k){ if(d.open!==d.hasAttribute('data-auf')) z.key[k]=d.open; }
      else z.idx.push(d.open);
    });
    return z;
  }
  function detailsZurueck(z){
    var i=0;
    [].slice.call(document.querySelectorAll('details')).forEach(function(d){
      var k=d.getAttribute('data-key');
      if(k){ if(Object.prototype.hasOwnProperty.call(z.key,k)) d.open=z.key[k]; }
      else { if(z.idx[i]!==undefined) d.open=z.idx[i]; i++; }
    });
  }
  function swapQuiet(el, html){ if(el && el.innerHTML!==html) el.innerHTML=html; }
  function mmss(ms){ var s=Math.max(0,Math.floor(ms/1000));
    return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }
  // The running time and the notice ages tick HERE, not through the
  // fingerprint: a per-second value in the fingerprint would report a
  // change on every poll and run the swap logic continuously.
  function paint(){
    if(opsEl){
      // The duration goes WHERE THE SENTENCE PUTS IT: "Robo werkelt seit
      // m:ss — nicht abgestuerzt, nur gruendlich". Appended at the end it
      // read as broken German ("… nur gruendlich 1:23"). The German
      // string carries the placeholder; only this line fills it.
      var zeile = opsText;
      if(zeile.indexOf('m:ss')>=0){
        zeile = zeile.replace('m:ss', '<span class="num">'
          + ((ticking && since) ? mmss(Date.now()-since) : '0:00')
          + '</span>');
      }
      // Only write when the rendered string really changed. An
      // unconditional write once per second would drop the selection of
      // anyone trying to copy the operating line.
      if(opsEl.innerHTML!==zeile) opsEl.innerHTML=zeile;
    }
    var now=Date.now();
    document.querySelectorAll('.notice[data-raised]').forEach(function(d){
      var t=Date.parse(d.getAttribute('data-raised'));
      var a=d.querySelector('.age');
      if(!a || isNaN(t)) return;
      var s=Math.floor((now-t)/1000);
      if(s<ageAfter){ a.textContent=''; d.classList.remove('nachfassen'); return; }
      var m=Math.floor(s/60);
      if(s>=followAfter){
        d.classList.add('nachfassen');
        a.textContent=' · steht seit '+m+' min, Robo prüft nach';
      } else {
        d.classList.remove('nachfassen');
        a.textContent=' · seit '+(m? m+' min' : s+' s');
      }
    });
    // Only the newest decision can be the new one; the others fall back
    // to the normal colour by losing the class again.
    var besch=document.querySelectorAll('#decisions span[data-added]');
    besch.forEach(function(d,i){
      var t=Date.parse(d.getAttribute('data-added'));
      var frisch=(i===besch.length-1) && !isNaN(t) && (now-t)<frischMs;
      if(frisch!==d.classList.contains('frisch'))
        d.classList.toggle('frisch', frisch);
    });
  }
  setInterval(paint, 1000);

  // ---- The history. THE POINTER LIVES HERE, never in buehne.json: a
  // viewer who pages back would otherwise page the view away for the
  // whole room, and the consumer's state would hang off a browser. The
  // server sends the positions, the position inside them is local.
  //   pos === null   the current view (the last position)
  //   pos is a number  paged back, and then a poll must not move it
  var verlauf=[], pos=null, basis='', letzte=null;
  function neustes(){ return verlauf.length ? verlauf[verlauf.length-1] : null; }
  function male(){
    var el=document.getElementById('verlauf');
    if(!el) return;                                   // the file view has none
    var n=verlauf.length;
    if(n<2){ el.innerHTML=''; el.classList.remove('zurueck'); return; }
    var i=(pos===null) ? n-1 : pos;
    var titel=verlauf[i].title ? ' · '+verlauf[i].title : '';
    // An old state must not be indistinguishable from the current one.
    var text=(pos===null)
      ? '<span>Verlauf '+(i+1)+'/'+n+titel+'</span>'
      : '<span>Verlauf '+(i+1)+'/'+n+titel+' — <a href="#" data-v="live">'
        +'zurück zur aktuellen Ansicht</a></span>';
    // A NEW position while paged back is reported, never jumped to:
    // jumping is exactly the unexpected displacement section 4.1 rules out.
    if(pos!==null && basis && verlauf[n-1].id!==basis){
      text+=' <span class="neu">· neu: '
            +(verlauf[n-1].title||'ein neuer Stand')+'</span>';
    }
    el.innerHTML=
      '<button data-v="zurueck"'+(i<=0?' disabled':'')
      +' title="Zurück (Pfeil links)">◀</button>'
      +'<button data-v="vor"'+(i>=n-1?' disabled':'')
      +' title="Vor (Pfeil rechts)">▶</button>'+text;
    el.classList.toggle('zurueck', pos!==null);
  }
  function zeige(i){
    if(i!==null && pos===null){ var n=neustes(); basis=n ? n.id : ''; }
    if(i===null) basis='';
    pos=i;
    var e=(i===null) ? letzte : verlauf[i];
    if(e){
      swapInhalt(document.getElementById('content'), e.main||'');
      swap(document.getElementById('collapsed'), e.collapsed||'');
    }
    male();
    grafBooten();
  }
  // Three equal ways, and two of them need no mouse: this one is shared by
  // the buttons and the arrow keys. The third is the call-out, which runs
  // through the agent and puts the earlier surface up as a new one.
  function blaettern(schritt){
    if(verlauf.length<2) return;
    var ziel=((pos===null) ? verlauf.length-1 : pos)+schritt;
    if(ziel<0) ziel=0;
    if(ziel>verlauf.length-1) ziel=verlauf.length-1;
    zeige(ziel===verlauf.length-1 ? null : ziel);
  }
  document.addEventListener('click', function(ev){
    var t=ev.target && ev.target.closest ? ev.target.closest('[data-v]') : null;
    if(!t) return;
    var was=t.getAttribute('data-v');
    if(was==='zurueck') blaettern(-1);
    else if(was==='vor') blaettern(1);
    else if(was==='live') zeige(null);
    else if(was==='protokoll-toggle'){
      protokollAus=!protokollAus;
      protokollAnwenden();
      try{ localStorage.setItem('cothinker-protokoll-aus', protokollAus?'1':'0'); }
      catch(e){ /* nothing to persist to, the session keeps the toggle */ }
    }
    else return;
    ev.preventDefault();
  });
  // A second, separate listener -- not folded into the `[data-v]` one
  // above, which is only about the history buttons. Every plain link on
  // the page (the sidebar `Termin` link, wikilinks in rendered markdown,
  // the `← Bühne` back links) is relative and carries no query, so with a
  // token set it would 404 the moment it is followed.
  //
  // Delegated rather than a one-time rewrite at load time: `tick()` swaps
  // `#links` and `#content` wholesale every few seconds, so a link
  // rewritten once at load would be replaced by a fresh, token-less one
  // before anyone could click it. The stronger reason: the rendering side
  // (markdown -> HTML, wikilinks) may not be touched at all here, so the
  // server has no chance to emit tokenised hrefs into the polled payload
  // in the first place -- this listener is not an optimisation over
  // rewriting links server-side, it is the only place left that can do it.
  document.addEventListener('click', function(ev){
    if(!TOKEN) return;
    if(ev.metaKey||ev.ctrlKey||ev.shiftKey||ev.altKey) return;  // "open elsewhere"
    var a=ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    // Presence, not truthiness: an explicit data-v="" still marks this
    // anchor as belonging to the history handler, not this one.
    if(!a || a.getAttribute('data-v')!==null
     || (a.getAttribute('href')||'').indexOf('#')===0) return;
    // The href PROPERTY, not the attribute: it already honours <base
    // href>, which every page sets (see page()).
    var url;
    try{ url=new URL(a.href); }catch(e){ return; }
    if(url.origin!==location.origin || url.searchParams.has('t')) return;
    url.searchParams.set('t', TOKEN);
    a.setAttribute('href', url.toString());
  });
  document.addEventListener('keydown', function(ev){
    if(ev.metaKey||ev.ctrlKey||ev.altKey||ev.shiftKey) return;
    if(tippt()) return;
    if(ev.key==='ArrowLeft') blaettern(-1);
    else if(ev.key==='ArrowRight') blaettern(1);
    else return;
    ev.preventDefault();
  });
  function verlaufUebernehmen(j){
    // The reader follows the CONTENT, not the index: when the cap drops
    // the oldest position, the entry under the pointer would otherwise
    // silently become a different one.
    var hier=(pos!==null && verlauf[pos]) ? verlauf[pos].id : null;
    verlauf=Array.isArray(j.history) ? j.history : [];
    if(pos!==null){
      var gefunden=-1;
      for(var i=0;i<verlauf.length;i++){ if(verlauf[i].id===hier) gefunden=i; }
      // The position the reader was on fell out of the history: hold the
      // oldest one that is left instead of jumping to the end.
      pos=(gefunden>=0) ? gefunden : (verlauf.length ? 0 : null);
    }
    male();
  }

  // ---- The announcement line. `pending` comes as DATA (id/kind/label),
  // not HTML, so each line can be owned by its id: a line that is already
  // there is left alone on every later poll -- restarting its morph every
  // 3 s would be exactly the flicker the page exists to avoid. The label
  // is untrusted and only ever goes in via textContent.
  var pendingEl=document.getElementById('pending');
  var pendingLines={};                     // id -> {el, glyph, timer, leaveTimer, label, leaving}
  // Checked once: a reader who asked the system for less motion gets the
  // plain label with trailing dots instead. `matchMedia` can be missing
  // (old engines, the test DOM) -- then the morph is the default.
  var pendingReducedMotion=false;
  try{
    pendingReducedMotion=!!(window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }catch(e){ pendingReducedMotion=false; }
  // HALFWIDTH Katakana (U+FF66-FF9D) and block elements (U+2580-259F), the
  // Matrix rain alphabet. Halfwidth on purpose: the full-width block
  // (U+30A1-30FA) is two columns wide even in a monospace font, so the
  // line's right edge jumped with every frame -- movement nobody asked
  // for. Built from code points rather than spelled out as 88 literal
  // characters nobody can proofread.
  var GLYPHS=(function(){
    var s='', c;
    for(c=0xFF66;c<=0xFF9D;c++) s+=String.fromCharCode(c);
    for(c=0x2580;c<=0x259F;c++) s+=String.fromCharCode(c);
    return s;
  })();
  function randomGlyph(){ return GLYPHS.charAt(Math.floor(Math.random()*GLYPHS.length)); }
  // Left to right: the characters behind the head are final, a head of
  // PENDING_HEAD characters flickers through random glyphs, nothing ahead of
  // it yet. One interval per line, cleared when the label stands or when
  // the line is removed first -- never a timer left running.
  var PENDING_HEAD=3;
  // The whole morph takes at most ~1.2 s, and no frame is shorter than
  // 40 ms: a faster flicker reads as strobing, not as settling. So a long
  // label gets FEWER frames, each advancing the head by more than one
  // character, instead of shorter ones.
  var PENDING_MORPH_MS=1200, PENDING_TICK_MIN_MS=40;
  function pendingMorph(entry, target){
    var n=target.length, span=n+PENDING_HEAD, step=0;
    var steps=Math.max(1, Math.min(span, Math.floor(PENDING_MORPH_MS/PENDING_TICK_MIN_MS)));
    var tickMs=Math.max(PENDING_TICK_MIN_MS, Math.floor(PENDING_MORPH_MS/steps));
    entry.el.classList.add('morphing');
    entry.timer=setInterval(function(){
      step++;
      if(step>=steps){
        clearInterval(entry.timer); entry.timer=null;
        entry.glyph.textContent=entry.label;
        entry.el.classList.remove('morphing');
        entry.el.classList.add('resting');
        return;
      }
      var head=Math.round(step*span/steps);
      var settled=Math.max(0, head-PENDING_HEAD), upTo=Math.min(n, head), t='';
      for(var i=0;i<upTo;i++){
        var ch=target.charAt(i);
        t+=(i<settled || ch===' ') ? ch : randomGlyph();   // spaces stay spaces
      }
      entry.glyph.textContent=t;
    }, tickMs);
  }
  // Out of the DOM and out of the map, now; every timer of the line with it.
  function pendingRemove(id){
    var entry=pendingLines[id];
    if(!entry) return;
    if(entry.timer){ clearInterval(entry.timer); entry.timer=null; }
    if(entry.leaveTimer){ clearTimeout(entry.leaveTimer); entry.leaveTimer=null; }
    if(entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
    delete pendingLines[id];
  }
  function pendingLeave(id){
    var entry=pendingLines[id];
    if(!entry || entry.leaving) return;
    if(entry.timer){ clearInterval(entry.timer); entry.timer=null; }
    if(pendingReducedMotion){ pendingRemove(id); return; }
    // A short brightening, then gone: the CSS animation runs 400 ms.
    entry.leaving=true;
    entry.el.classList.remove('morphing'); entry.el.classList.remove('resting');
    entry.el.classList.add('leaving');
    entry.leaveTimer=setTimeout(function(){ entry.leaveTimer=null; pendingRemove(id); }, 400);
  }
  function pendingApply(list){
    if(!pendingEl) return;                  // the file view has no line
    list=Array.isArray(list) ? list.slice(0,3) : [];
    var present={};
    list.forEach(function(e){ if(e && typeof e.id==='string') present[e.id]=true; });
    Object.keys(pendingLines).forEach(function(id){
      var entry=pendingLines[id];
      if(!present[id]) pendingLeave(id);
      else if(entry.leaving){
        // Back within the flash (a stale file read in between): keep it.
        // The morph may have been cut off by the flash, so the label is
        // set whole, without a second morph.
        clearTimeout(entry.leaveTimer); entry.leaveTimer=null; entry.leaving=false;
        entry.glyph.textContent=entry.label;
        entry.el.classList.remove('leaving'); entry.el.classList.add('resting');
      }
    });
    // Never more than three lines on the page, flashing ones included: when
    // the new ones would push the count past three, a line that is only
    // flashing out goes at once, without its flash -- oldest first. The
    // flash is a courtesy; the cap is the promise.
    var added=list.filter(function(e){
      return e && typeof e.id==='string' && !pendingLines[e.id]; }).length;
    var ids=Object.keys(pendingLines);
    var excess=ids.length+added-3;
    ids.forEach(function(id){
      if(excess>0 && pendingLines[id].leaving){ pendingRemove(id); excess--; }
    });
    list.forEach(function(e){
      if(!e || typeof e.id!=='string' || pendingLines[e.id]) return;
      var label=String(e.label||'');
      var line=document.createElement('div');
      line.className='pending-line';
      line.setAttribute('data-id', e.id);
      var glyph=document.createElement('span');
      glyph.setAttribute('aria-hidden','true');
      var sr=document.createElement('span');
      sr.className='sr';
      sr.textContent=label;
      line.appendChild(glyph); line.appendChild(sr);
      // Appended, never inserted: a new line goes below the ones the room
      // is already reading.
      pendingEl.appendChild(line);
      var entry={el:line, glyph:glyph, label:label, timer:null, leaving:false};
      pendingLines[e.id]=entry;
      if(pendingReducedMotion) glyph.textContent=label+' …';
      else pendingMorph(entry, label);
    });
  }

  // The image on request (`BILD:`). Keyed on the id: a new id opens it,
  // no id closes it, a click closes it for this viewer only.
  var bildEl=document.getElementById('bild'), bildId=null, bildZu=null;
  if(bildEl && bildEl.addEventListener) bildEl.addEventListener('click', function(){
    bildZu=bildId; bildEl.hidden=true;
  });
  function bildApply(b){
    if(!bildEl) return;
    var id=(b && typeof b.id==='string') ? b.id : null;
    if(id===null){ bildId=null; bildEl.hidden=true; return; }
    if(id===bildZu) return;
    if(id!==bildId){
      bildId=id;
      document.getElementById('bild-img').src=mitToken('image/'+id);
      document.getElementById('bild-img').alt=b.caption||'';
      document.getElementById('bild-cap').textContent=b.caption||'';
    }
    bildEl.hidden=false;
  }

  // ---- The agenda zone (gateway/stage_state.py::agenda_payload). DATA,
  // not HTML, for the same reason as `pending` above: `item_started_at`,
  // `item_budget_s` and `meeting_deadline` are numbers the page itself
  // counts down from in `agendaPaint()`, driven by the existing 1 s
  // `paint()` interval -- never by the server, and never part of the
  // fingerprint (see `fingerprint()` and `stage_parts()` there). Text
  // fields go in via `textContent` only: untrusted, like every other
  // field of this file.
  var agendaEl=document.getElementById('agenda');
  var agendaState=null;
  function agendaApply(data){
    if(!agendaEl) return;
    if(!data || typeof data!=='object'){
      agendaState=null;
      if(agendaEl.firstChild) agendaEl.innerHTML='';
      return;
    }
    agendaState=data;
    if(!agendaEl.firstChild){
      agendaEl.innerHTML=
        '<div class="agenda-row"><b id="agenda-current"></b>'
        +'<span id="agenda-timer"></span></div>'
        +'<div id="agenda-next" class="agenda-next"></div>'
        +'<div id="agenda-who" class="agenda-who"></div>'
        +'<div id="agenda-meeting" class="agenda-meeting"></div>';
    }
    document.getElementById('agenda-current').textContent=
      'Punkt '+data.current+'/'+data.total+': '+(data.current_text||'');
    document.getElementById('agenda-next').textContent=
      data.next ? ('Weiter: '+data.next_text) : '';
    var wer=data.speaker
      ? ('Dran: '+data.speaker
         +(data.next_speaker ? ' · weiter: '+data.next_speaker : ''))
      : '';
    document.getElementById('agenda-who').textContent=wer;
    agendaPaint();
  }
  // The live countdown: driven by the SAME 1 s interval as the running
  // duration and the notice ages above (`paint()`), off numbers the
  // consumer sent once -- not a value re-fetched or recomputed server-side.
  function agendaPaint(){
    if(!agendaEl || !agendaState) return;
    var now=Date.now()/1000;
    var timerEl=document.getElementById('agenda-timer');
    if(timerEl){
      if(typeof agendaState.item_started_at==='number'){
        var elapsed=Math.max(0, now-agendaState.item_started_at);
        if(typeof agendaState.item_budget_s==='number'){
          var left=agendaState.item_budget_s-elapsed;
          timerEl.textContent=' '+(left<0?'+':'')+mmss(Math.abs(left)*1000);
          timerEl.classList.toggle('agenda-over', left<0);
        } else {
          timerEl.textContent=' '+mmss(elapsed*1000);
          timerEl.classList.remove('agenda-over');
        }
      } else {
        timerEl.textContent='';
        timerEl.classList.remove('agenda-over');
      }
    }
    var meetingEl=document.getElementById('agenda-meeting');
    if(meetingEl){
      if(typeof agendaState.meeting_deadline==='number'){
        var rest=agendaState.meeting_deadline-now;
        meetingEl.textContent='Resttermin: '+(rest<0?'+':'')+mmss(Math.abs(rest)*1000);
        meetingEl.classList.toggle('agenda-over', rest<0);
      } else {
        meetingEl.textContent='';
        meetingEl.classList.remove('agenda-over');
      }
    }
  }
  setInterval(agendaPaint, 1000);

  async function tick(){
    if(halt) return;
    try{
      // Relative to <base>: the page can be mounted at / or at /buehne/,
      // and the file view sits one level deeper. <base> resolves both.
      var r=await fetch(mitToken('parts'+location.search),{cache:'no-store'});
      var j=await r.json();
      punkt.classList.remove('weg');
      clock.textContent=j.clock;
      ticking=!!j.ticking; opsText=j.status||'';
      // The trouble line rides with the operating line, OUTSIDE the
      // fingerprint: it belongs to the same header row group, it never
      // carries a clock, and it has to appear the moment the condition
      // does — not when some other part of the surface happens to change.
      swapQuiet(warnEl, j.warning||'');
      // The announcements, for the same reasons and deliberately NOT in
      // the fingerprint either (see `fingerprint()`): they are about now,
      // and their coming and going must not run the swap logic below.
      pendingApply(j.pending);
      bildApply(j.image);
      agendaApply(j.agenda);
      since=j.since ? Date.parse(j.since) : null;
      // A configured 0 is a decision ("show the age immediately"), not a
      // missing value — `||` would silently turn it into the default.
      ageAfter=(typeof j.age_visible_after_s==='number')
               ? j.age_visible_after_s : 20;
      followAfter=(typeof j.follow_up_after_s==='number')
                  ? j.follow_up_after_s : 120;
      paint();
      // Taken over BEFORE the fingerprint is consulted, like the operating
      // line: the first poll finds the mark unchanged by definition, and
      // the page would stand there without a history until something else
      // on the surface happened to move.
      letzte=j;
      verlaufUebernehmen(j);
      if(j.mark===mark) return;
      if(busy()) return;                                  // try again later
      mark=j.mark;
      var y=window.scrollY, h=document.body.scrollHeight;
      var bottom=(window.innerHeight+y)>=h-140;
      var open=detailsMerken();
      var hl=document.getElementById('headline');
      if(hl) hl.textContent=j.headline||'';
      swapQuiet(document.getElementById('mirror'), j.mirror||'');
      swapQuiet(document.getElementById('call-answer'), j.call_answer||'');
      swapQuiet(document.getElementById('decisions'), j.decisions||'');
      swapQuiet(document.getElementById('notices'),
                (j.notices||'')+(j.question||'')+(j.parked||''));
      // The protocol column has ITS OWN scroll container (#protokoll-col,
      // overflow-y:auto) -- replacing only #protokoll-content's innerHTML
      // never resets that container's scrollTop, so a reader who scrolled
      // down to follow along stays there, the same guarantee the left
      // side gets via `y`/`bottom` below. swapQuiet, not swap: no fade
      // flash on every append, the requirement was "kein Flackern".
      swapQuiet(document.getElementById('protokoll-content'), j.protokoll||'');
      // While the reader is paged back, the main area is NOT swapped --
      // the swap logic must not page the reader's position away. The
      // header zone is swapped either way: it never displaced the main
      // area, and a notice is about now, not about the position.
      if(pos===null){
        swapInhalt(document.getElementById('content'), j.main||'');
        grafBooten();
        swap(document.getElementById('collapsed'), j.collapsed||'');
      }
      swap(document.getElementById('links'), j.links||'');
      detailsZurueck(open);                               // keep expanded state
      paint();                                            // ages on fresh lines
      punkt.classList.add('an'); setTimeout(function(){punkt.classList.remove('an')},700);
      if(bottom){ window.scrollTo(0,document.body.scrollHeight); }
      else { window.scrollTo(0,y); }                      // keep position
    }catch(e){ punkt.classList.add('weg'); clock.textContent='getrennt'; }
  }
  // MM-J: a click on a topic in the graph tab scrolls the protocol column
  // here to it. Only messages from this very origin, only this one type;
  // the title first, the time as the fallback, and nothing at all when
  // neither is found -- a wrong jump is worse than none. (The guard is for
  // the node harnesses in tests/test_stage_js.py, whose fake `window` has
  // no addEventListener; every browser has one.)
  if(window.addEventListener) window.addEventListener('message', function(ev){
    try{
      if(!ev || ev.origin!==location.origin) return;
      var data=ev.data;
      if(!data || data.type!=='cothinker-topic') return;
      var box=document.getElementById('protokoll-content');
      if(!box) return;
      var ziel=stageTopicTarget(
        [].slice.call(box.querySelectorAll('h1,h2,h3,h4,h5,h6,li,p')),
        data.title, data.time);
      if(ziel && ziel.scrollIntoView) ziel.scrollIntoView({block:'start'});
    }catch(e){ /* a foreign message must never break the stage */ }
  });
  document.addEventListener('visibilitychange',function(){ halt=document.hidden; });
  setInterval(tick,3000);
})();
"""


# The participant parts' markup (issue #47): name dialog, queue buttons,
# share view. Static; the script above the page script drives it.
PARTICIPATE_HTML = """
<div id="teiln"><span id="teiln-status" aria-live="polite"></span>
<button type="button" id="teiln-wort">Wortmeldung</button>
<button type="button" id="teiln-fertig">Fertig</button>
<button type="button" id="teiln-teilen">Link teilen</button></div>
<div id="join-dlg" role="dialog" aria-modal="true" aria-labelledby="join-titel" hidden>
<form id="join-form" class="teiln-box" autocomplete="off">
<label id="join-titel" for="join-name">Wie heißt du?</label>
<input id="join-name" type="text" maxlength="40" required>
<div id="join-err" aria-live="polite"></div>
<button type="submit">Los</button>
<button type="button" id="join-later">Später</button></form></div>
<div id="share-dlg" role="dialog" aria-modal="true" aria-label="Link teilen" hidden>
<div class="teiln-box"><div id="share-qr"></div>
<div id="share-link"></div>
<button type="button" id="share-close">Schließen</button></div></div>
"""


def page(titel: str, kopf: str, rumpf: str, mark_json: str, inhalt: str,
         wurzel: str = "./") -> bytes:
    """A page. `wurzel` is the path from HERE to the root of the stage.

    All links and the fingerprint fetch are relative, so the stage also
    works under a proxy subpath (`/buehne/`). For this to also hold in the
    file view — which lives under `/file/<name>`, one level deeper —
    `<base>` sets the reference point. Without it, a wikilink from there
    would point at `/file/file/<name>`.

    `inhalt` is the exact string this call put into `<div id="content">`,
    handed back to the script as its seed for `letzterInhalt` (see there)
    so the first comparison is against the server's own bytes.
    """
    # The UMD bundles find each other over `window` and must therefore
    # stand before graph.js and before the page script -- on EVERY page
    # `page()` builds, the file view included, since it shares the script.
    # These two are requested by the browser as plain `<script src>`, with
    # no query of their own, so the token has to ride along here or the
    # access check above would 404 the page's own scripts.
    tok = f"?t={quote(TOKEN, safe='')}" if TOKEN is not None else ""
    vendor = "".join(
        f'<script src="{wurzel}static/vendor/{n}{tok}"></script>'
        for n in VENDOR_FILES) + f'<script src="{wurzel}static/graph.js{tok}"></script>'
    # `inhalt` is untrusted, vault-file/markdown-derived content; escaping
    # `</` here is defense-in-depth against a literal `</script>` closing
    # this tag early, should that ever become reachable.
    inhalt_json = json.dumps(inhalt).replace("</", "<\\/")
    # Same reason as inhalt_json above: a served page is otherwise fully
    # attacker-controlled content wrapped in <script>.
    token_json = json.dumps(TOKEN).replace("</", "<\\/")
    # A chain of three str.replace() calls has no safe order. Whichever
    # placeholder is substituted FIRST is re-scanned by the later passes,
    # so its value -- if it happens to contain another placeholder's exact
    # text, e.g. a token whose value is the literal string "%INHALT%", or
    # vault content that contains the literal string "%TOKEN%" -- gets
    # spliced into the wrong slot or corrupted. There is no ordering of
    # the three replaces that protects all of them at once. A single
    # regex pass looks up each match's own replacement exactly once, so no
    # substituted value is ever re-scanned.
    werte = {'%TOKEN%': token_json, '%MARK%': mark_json, '%INHALT%': inhalt_json}
    script = re.sub(r'%(?:TOKEN|MARK|INHALT)%', lambda m: werte[m.group(0)], SCRIPT)
    return (f"""<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{wurzel}">
<title>{titel}</title><style>{STYLE}</style>{vendor}</head><body>
{kopf}{rumpf}{PARTICIPATE_HTML}
<script>{script}</script></body></html>""").encode()


# The graph tab (MM-J). Its own look -- the MM-D green-on-dark, pixel
# font where available -- but readable first: 15px minimum, labels in a
# light green on near-black, no glow on text.
GRAPH_STYLE = """
:root{--bg:#050a06;--hell:#39ff6a;--mittel:#1f8f3f;--text:#c8f5d2;
 --dim:#6fae7f;--lin:#143d1f}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
 font:16px/1.4 "VT323","IBM Plex Mono",ui-monospace,monospace}
header#sg-head{display:flex;align-items:center;gap:1rem;height:2.8rem;
 padding:0 1rem;border-bottom:1px solid var(--lin);background:#07110a}
header#sg-head .brand{color:var(--hell);letter-spacing:.08em;font-size:1.05rem}
header#sg-head a{color:var(--hell);text-decoration:none;
 border:1px solid var(--mittel);padding:.15rem .55rem;border-radius:3px}
header#sg-head a:hover,header#sg-head a:focus{background:#0d2414}
#sg-toggle{display:inline-flex;border:1px solid var(--mittel);border-radius:3px}
#sg-toggle button{font:inherit;font-size:15px;color:var(--dim);
 background:transparent;border:0;padding:.15rem .7rem;cursor:pointer}
#sg-toggle button+button{border-left:1px solid var(--mittel)}
#sg-toggle button.an,#sg-toggle button[aria-pressed="true"]{
 color:var(--bg);background:var(--hell)}
#sg-count{margin-left:auto;color:var(--dim);font-size:15px}
#sg{position:relative;width:100%;height:calc(100vh - 2.8rem);
 background:var(--bg)}
"""


def graph_page() -> bytes:
    """The graph tab: header, toggle, count line, and one container that
    `static/session_graph.js` draws into. NOT `page()`: that one carries
    the stage's poller, and this tab polls on its own terms."""
    tok = f"?t={quote(TOKEN, safe='')}" if TOKEN is not None else ""
    skripte = "".join(
        f'<script src="static/vendor/{n}{tok}"></script>'
        for n in VENDOR_FILES) + (
        f'<script src="static/graph.js{tok}"></script>'
        f'<script src="static/session_graph.js{tok}"></script>')
    token_json = json.dumps(TOKEN).replace("</", "<\\/")
    return (f"""<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="./">
<title>Graph der Sitzung</title><style>{GRAPH_STYLE}</style>{skripte}</head><body>
<header id="sg-head"><span class="brand">CoThinker · Graph</span>
<a href="./{tok}" target="_blank">Bühne ↗</a>
<span id="sg-toggle"><button data-layout="graph">Graph</button><button data-layout="kanban">Kanban</button></span>
<span id="sg-count"></span></header>
<div id="sg"></div>
<script>window.STAGE_TOKEN={token_json};sgStart(document.getElementById('sg'));</script>
</body></html>""").encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30                 # hanging connections don't occupy a thread

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        """A reader closing the tab is not an error.

        Without this, the server would write a stack trace into the
        operations log for every closed browser window — during a meeting
        exactly the noise that hides real errors.
        """
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            self.close_connection = True

    def _send(self, code, body: bytes, typ="text/html; charset=utf-8"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # The token rides in the URL query string, and the stage
            # renders external markdown links: a referrer sent to one of
            # them would leak it cross-origin. Browsers already drop the
            # query cross-origin by default, but this does not depend on
            # that default.
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _token_ok(self, q: dict) -> bool:
        """The access check shared by GET and POST (`True` when no token)."""
        if TOKEN is None:
            return True
        candidate = q.get("t", [""])[0] or self.headers.get("X-Stage-Token", "")
        # Compared as UTF-8 BYTES, not as `str`: `compare_digest`
        # raises `TypeError` on a non-ASCII `str` operand, which would
        # turn a one-byte probe into a 500 -- distinguishing a
        # token-protected stage from an unknown path, exactly what
        # the GET 404 exists to prevent (and bricking a non-ASCII
        # `--token` for every request, correct token included).
        return bool(candidate) and secrets.compare_digest(
            candidate.encode("utf-8"), TOKEN.encode("utf-8"))

    def do_POST(self):
        try:
            self._post()
        except Exception as e:            # noqa: BLE001
            print(f"stage: error on POST {self.path.split('?')[0]!r}: {e!r}",
                  flush=True)
            self._send(500, b"error", "text/plain")

    def _post(self):
        path = self.path.split("?")[0]
        q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        if path not in ("/join", "/action"):
            return self._send(404, b"unbekannt", "text/plain")
        if not self._token_ok(q):
            return self._send(403, b"forbidden", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._send(400, b"bad request", "text/plain")
        if length < 0:
            return self._send(400, b"bad request", "text/plain")
        if length > MAX_POST_BYTES:
            # Not read: the connection is closed instead.
            self.close_connection = True
            return self._send(413, b"too large", "text/plain")
        raw = self.rfile.read(length)

        cookie = ""
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                cookie = v
        pid = cookie if _PID_RE.fullmatch(cookie) else ""
        if path == "/action" and not pid:
            return self._send(401, b"no participant", "text/plain")
        if pid:
            now = time.monotonic()
            with _action_lock:
                last = _last_action.get(pid)
                limited = last is not None and now - last < MIN_ACTION_GAP_S
                if not limited:
                    if len(_last_action) >= 1024:
                        for k in [k for k, t in _last_action.items()
                                  if now - t >= MIN_ACTION_GAP_S]:
                            del _last_action[k]
                    _last_action[pid] = now
            if limited:
                return self._send(429, b"slow down", "text/plain")

        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            body = None
        if not isinstance(body, dict):
            return self._send(400, b"bad request", "text/plain")

        if path == "/join":
            name = clean_name(body.get("name"))
            if name is None:
                return self._send(400, b"bad name", "text/plain")
            pid = pid or secrets.token_hex(8)
            append_action(pid, "participant:name", "edit", {"name": name})
            payload = json.dumps({"ok": True}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Set-Cookie",
                    f"{COOKIE_NAME}={pid}; HttpOnly; SameSite=Strict; Path=/")
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
            return
        if body.get("intent_id") != "queue:room" or \
                body.get("op") not in QUEUE_OPS:
            return self._send(400, b"bad action", "text/plain")
        append_action(pid, "queue:room", "tap", {"op": body["op"]})
        self._send(200, b'{"ok": true}', "application/json")

    def do_GET(self):
        try:
            self._get()
        except Exception as e:            # noqa: BLE001
            # The stage is in front of a room full of people. It would
            # rather show one line of apology than a stack trace.
            # Redact `?t=<token>` before it can reach stdout / the journal:
            # this line fires on every 500, including one hit by an
            # authenticated request that carries the real token.
            path_for_log = re.sub(r"([?&]t=)[^&]*", r"\1<token>", self.path)
            print(f"stage: error on {path_for_log!r}: {e!r}", flush=True)
            self._send(500, b"Die Buehne konnte diese Seite gerade nicht "
                            b"aufbauen. Sie versucht es gleich erneut.",
                       "text/plain; charset=utf-8")

    def _get(self):
        path = self.path.split("?")[0]
        q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")

        # The access check, BEFORE any path is evaluated -- one place for
        # every route, not one check per route. A missing or empty value is
        # a failure. Answers byte-identical to the unknown-path 404 below:
        # on this server, a rejected request is indistinguishable from a
        # wrong path, which is why 404 and not 401.
        if not self._token_ok(q):
            return self._send(404, b"unbekannt", "text/plain")

        d = unquote(q.get("d", [""])[0])

        # --- Data only, no reload ---
        if path == "/parts":
            # MM-J: the graph tab reads `graph` and nothing else. It gets
            # nothing else -- in particular not the rendered protocol with
            # its raw transcript. The stage's own poll (no `g`) and the
            # file view (`d`) are untouched.
            if not d and q.get("g", [""])[0] == "1":
                nutz = {"graph": session_graph_payload(),
                        "clock": time.strftime("%H:%M:%S")}
                return self._send(200, json.dumps(nutz).encode(),
                                  "application/json")
            if d:
                p = find_file(d)
                if not p:
                    return self._send(404, b"{}", "application/json")
                inhalt, m = render_file(p, {x.stem for x in list_files()})
                # The file view uses the same script and therefore the same
                # payload shape: everything the stage has, emptied out.
                nutz = {"mark": m, "headline": p.stem, "main": inhalt,
                        "status": "", "warning": "", "ticking": False,
                        "since": "",
                        "mirror": "", "call_answer": "", "collapsed": "",
                        "decisions": "",
                        "notices": "", "question": "", "parked": "",
                        "links": "", "history": [], "protokoll": "",
                        "pending": [], "image": None, "agenda": None,
                        "age_visible_after_s": 20, "follow_up_after_s": 120}
            else:
                t, m = stage_parts_cached()
                nutz = {"mark": m, **t}
                # `history[-1]` is BY CONSTRUCTION the same surface as the
                # top-level `main` (StageState.main IS `self.history[-1]`,
                # gateway/stage_state.py) -- stage_parts() renders both
                # independently from the same source, so the two carry
                # byte-identical HTML on every single poll, from every
                # viewer (measured: 184 704 bytes, twice, one real graph
                # position). The client never reads this copy:
                # `blaettern()` in SCRIPT always converts a request for
                # the newest index into `zeige(null)`, which reads the
                # top-level `main`/`collapsed` instead -- so it is blanked
                # here rather than rendered twice for nothing.
                if nutz["history"]:
                    letzte = dict(nutz["history"][-1])
                    letzte["main"] = ""
                    letzte["collapsed"] = ""
                    nutz["history"] = [*nutz["history"][:-1], letzte]
            nutz["clock"] = time.strftime("%H:%M:%S")
            return self._send(200, json.dumps(nutz).encode(), "application/json")

        # --- The image on request (`BILD:`), by id, never by path ---
        if path.startswith("/image/"):
            p = image_file(unquote(path[len("/image/"):]))
            if p is None:
                return self._send(404, b"unbekannt", "text/plain")
            try:
                roh = p.read_bytes()
            except OSError:
                return self._send(404, b"unbekannt", "text/plain")
            typ = stage_image.IMAGE_EXTS[p.suffix.lower()]
            return self._send(200, roh, typ)

        # --- The vendored drawing libraries, by exact name ---
        if path.startswith("/static/vendor/"):
            name = unquote(path[len("/static/vendor/"):])
            # By NAME, never by path: no join, no traversal, no directory
            # listing. The same posture as `/vault/`, one line shorter.
            if name not in VENDOR_FILES:
                return self._send(404, b"unbekannt", "text/plain")
            try:
                roh = (STATIC / "vendor" / name).read_bytes()
            except OSError:
                return self._send(404, b"unbekannt", "text/plain")
            return self._send(200, roh, "application/javascript; charset=utf-8")
        if path in ("/static/graph.js", "/static/session_graph.js"):
            try:
                roh = (STATIC / path[len("/static/"):]).read_bytes()
            except OSError:
                return self._send(404, b"unbekannt", "text/plain")
            return self._send(200, roh, "application/javascript; charset=utf-8")

        # --- The graph tab (MM-J): the session as a graph, own page ---
        if path == "/graph":
            return self._send(200, graph_page())

        # --- A vault note, by the graph's own read path ---
        if path.startswith("/vault/"):
            p = vault_file(path[7:])
            if not p:
                return self._send(404, b"unbekannt", "text/plain")
            inhalt, m = render_file(p, set())
            kopf = (f'<header><a class="zur" href="./">&larr; Bühne</a>'
                    f'<b id="headline">{html.escape(p.stem)}</b>'
                    f'<span class="puls"><span class="punkt" id="punkt"></span>'
                    f'<span id="clock">…</span></span></header>')
            rumpf = (f'<main><div id="content">{inhalt}</div>'
                     f'<p class="fluss">Notiz aus dem Vault, nur gelesen. '
                     f'Die <a href="./">Bühne</a> zeigt, was gerade gilt.</p>'
                     f'</main>')
            # Same climb as the file view: `/vault/a/b/c.md` is three
            # levels below the stage, and `<base>` has to get back out or
            # the fingerprint fetch goes to `/vault/a/b/parts`.
            wurzel = "../" * (1 + path[7:].strip("/").count("/"))
            return self._send(200, page(html.escape(p.stem), kopf, rumpf,
                                        json.dumps(m), inhalt, wurzel=wurzel))

        # --- Backend: a project file ---
        if path.startswith("/file/"):
            ziel_name = unquote(path[6:])
            p = find_file(ziel_name)
            if not p:
                return self._send(404, b"unbekannt", "text/plain")
            inhalt, m = render_file(p, {x.stem for x in list_files()})
            kopf = (f'<header><a class="zur" href="./">&larr; Bühne</a>'
                    f'<b id="headline">{html.escape(p.stem)}</b>'
                    f'<span class="puls"><span class="punkt" id="punkt"></span>'
                    f'<span id="clock">…</span></span></header>')
            rumpf = (f'<main><div id="content">{inhalt}</div>'
                     f'<p class="fluss">Arbeitsdokument aus dem Projektordner. '
                     f'Die <a href="./">Bühne</a> zeigt, was gerade gilt.</p></main>')
            # The file view sits below the stage — one level for a file in
            # the project folder, deeper for one in a session folder, whose
            # name carries its own slashes. `<base>` has to climb back out
            # of every one of them, or the fingerprint fetch and every
            # wikilink from the page would point into the session folder.
            wurzel = "../" * (1 + ziel_name.strip("/").count("/"))
            body = page(html.escape(p.stem), kopf, rumpf, json.dumps(m),
                       inhalt, wurzel=wurzel)
            # The fingerprint fetch needs the file name. It has to be
            # URL-encoded and then embedded as JSON in the string —
            # otherwise an apostrophe in the file name would break the script.
            ziel = json.dumps("parts?d=" + quote(rel_name(p), safe="")).encode()
            body = body.replace(b"'parts'+location.search", ziel)
            # MM-M: the graph tab's direct-open topic click lands here as
            # `#block-<id>`. Only the RUNNING protocol's own topic nodes are
            # published -- same identity (`rel_name`) `_build_session_graph`
            # uses for the seed, so a jump never lands on someone else's ids.
            protokoll_pfad = current_protocol_file()
            if protokoll_pfad is not None and rel_name(p) == rel_name(protokoll_pfad):
                knoten = session_graph_payload().get("nodes") or []
                bloecke = {n["id"]: {"title": n.get("label") or "",
                                     "time": n.get("time") or ""}
                          for n in knoten
                          if isinstance(n, dict) and n.get("kind") == "topic"}
                sg_blocks = (json.dumps(bloecke, ensure_ascii=False)
                            .replace("</", "<\\/").encode())
                body = body.replace(b"window.SG_BLOCKS = null;",
                                    b"window.SG_BLOCKS = " + sg_blocks + b";")
            return self._send(200, body)

        if path not in ("/", "/index.html"):
            return self._send(404, b"unbekannt", "text/plain")

        # --- Stage: sidebar left, header zone, main area free below ---
        t, mark = stage_parts_cached()
        protokoll = protocol_link()
        termin = (f'<div class="lab">Termin</div>'
                  f'<a href="file/{quote(protokoll, safe="/")}">Protokoll</a>'
                  if protokoll else "")
        seite = (f'<nav id="side"><span class="brand">CoThinker</span>'
                 f'<div id="links">{t["links"]}</div>{termin}</nav>')
        # A1-Minimal (operator, 2026-09-21): the right column, the running
        # protocol -- rendered, scrollable in its own container, never
        # cut. Abschaltbar per Auflage; default AN (the toggle only
        # hides #protokoll-col, it never stops fetching/rendering it --
        # so turning it back on needs no reload). State lives in
        # localStorage, read by SCRIPT before the first paint so a
        # returning reader who hid it does not see it flash on.
        protokoll_seite = (
            f'<aside id="protokoll-col"><div class="lab">Protokoll'
            f'<button id="protokoll-toggle" data-v="protokoll-toggle" '
            f'title="Protokollspalte ein-/ausblenden">ausblenden</button>'
            f'</div><div id="protokoll-content">{t["protokoll"]}</div>'
            f'</aside>')
        # MM-J: the graph tab opens beside the stage, `rel="opener"` so it
        # can post a topic click back here (the listener in SCRIPT).
        graf_tok = f"?t={quote(TOKEN, safe='')}" if TOKEN is not None else ""
        graf_link = (f'<a class="graf-link" href="graph{graf_tok}" '
                     f'target="_blank" rel="opener">Graph ↗</a>')
        rumpf = (f'<div id="stage">{seite}<div id="right">'
                 f'<div id="head">'
                 f'<div id="ops"><b id="headline">'
                 f'{html.escape(t["headline"])}</b>'
                 f'<span id="ops-text" data-ticking="{1 if t["ticking"] else 0}"'
                 f' data-since="{html.escape(t["since"])}">{t["status"]}</span>'
                 f'{graf_link}'
                 f'<span class="right"><span class="punkt" id="punkt"></span>'
                 f'<span id="clock">…</span></span></div>'
                 # Empty on purpose: the lines are built by the page script
                 # from `pending`, on the first poll -- it has to own them
                 # by id to animate each one exactly once.
                 f'<div id="pending" aria-live="polite"></div>'
                 # Empty on purpose, like #pending above: built from
                 # `agenda` by the page script, which owns the current/
                 # next text and runs the live countdown off numbers it
                 # is handed, never off a server-rendered string.
                 f'<div id="agenda"></div>'
                 f'<div id="warn">{t["warning"]}</div>'
                 f'<div id="mirror">{t["mirror"]}</div>'
                 f'<div id="call-answer">{t["call_answer"]}</div>'
                 f'<div id="decisions">{t["decisions"]}</div>'
                 f'<div id="notices">{t["notices"]}{t["question"]}'
                 f'{t["parked"]}</div>'
                 f'</div>'
                 f'<div id="area"><div id="verlauf"></div>'
                 f'<div id="content">{t["main"]}</div>'
                 f'<div id="collapsed">{t["collapsed"]}</div>'
                 f'<p class="fluss">Diese Seite zeigt den aktuellen Stand und '
                 f'ändert sich laufend — sie ist kein Archiv.</p></div>'
                 f'</div>{protokoll_seite}</div>'
                 # Outside #stage on purpose: inside the header a sticky/
                 # transformed ancestor becomes the containing block of
                 # `position:fixed` and the image collapsed to 0x0 (real
                 # run 2026-10-20).
                 f'<figure id="bild" hidden><img id="bild-img" alt="">'
                 f'<figcaption id="bild-cap"></figcaption></figure>')
        self._send(200, page("Bühne", "", rumpf, json.dumps(mark),
                             t["main"]))


def main() -> int:
    global PROJECT
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--host", default="0.0.0.0",
                    help="address to listen on. Behind nginx, 127.0.0.1 is "
                         "better — that way the port isn't unnoticed public.")
    ap.add_argument("--vault", default=os.environ.get("COTHINKER_VAULT", ""),
                    help="vault root, so a graph node can open its note")
    ap.add_argument("--vault-include-network", action="store_true",
                    help="also serve the person-related notes under "
                         "*/network/*. Off by default, deliberately.")
    ap.add_argument("--spool-dir", default=DEFAULT_SPOOL_DIR,
                    help="where participant actions are spooled "
                         "(<spool>/<session-folder>/actions.jsonl); the "
                         "consumer must be given the same folder. "
                         "Default: %(default)s")
    tok_grp = ap.add_mutually_exclusive_group()
    tok_grp.add_argument("--token", default=None,
                         help="access token every request must present, "
                              "via ?t=<token> or the X-Stage-Token header. "
                              "Generated automatically when omitted.")
    tok_grp.add_argument("--no-token", action="store_true",
                         help="disable the access check entirely -- the "
                              "stage and every project file become "
                              "reachable to anyone who can reach this port.")
    a = ap.parse_args()

    # COTHINKER_PROJECT_DIR: the same direct folder override as
    # gateway/consumer.py resolve_project() -- a project outside
    # $COTHINKER_ENTITIES (e.g. a plain git clone). Consumer and stage
    # must serve the SAME folder, so both read the same variable.
    override = os.environ.get("COTHINKER_PROJECT_DIR", "").strip()
    kand = (Path(override).expanduser() if override
            else ENTITIES / a.project)
    if override and not kand.is_dir():
        raise SystemExit(f"COTHINKER_PROJECT_DIR not a directory: {kand}")
    if not kand.is_dir():
        treffer = [x for x in ENTITIES.glob("*/projekte/*")
                   if x.is_dir() and x.name == a.project]
        if len(treffer) != 1:
            raise SystemExit(f"project not unique: {a.project!r} ({len(treffer)})")
        kand = treffer[0]
    PROJECT = kand
    global SPOOL_DIR
    SPOOL_DIR = Path(a.spool_dir).expanduser()
    global VAULT, VAULT_EXCLUDE
    VAULT = Path(a.vault) if a.vault else Path()
    if a.vault_include_network:
        VAULT_EXCLUDE = ()
    global TOKEN
    if a.no_token:
        TOKEN = None
        print("stage: WARNING --no-token -- no access check. Everyone who "
              "can reach this port sees the stage and every file of the "
              "project folder.", flush=True)
    else:
        TOKEN = a.token or secrets.token_urlsafe(16)
    print(f"stage for {PROJECT} on {a.host}:{a.port}", flush=True)
    if not a.no_token:
        # Printed ONCE: this is the URL the operator passes on. Encoded the
        # same way `page()` encodes it (`quote(TOKEN, safe="")`) -- a raw
        # `+` in an unquoted token decodes to a space on the way back
        # through `parse_qs`, locking the operator out of the very link
        # they were told to pass on.
        print(f"stage link: http://{a.host}:{a.port}/?t="
              f"{quote(TOKEN, safe='')}", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stage stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
