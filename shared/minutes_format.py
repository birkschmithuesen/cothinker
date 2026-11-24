#!/usr/bin/env python3
# CoThinker - protocol format v2 (topic blocks with tagged entries)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Pure, stdlib-only format module for protocol v2.

See `docs/entwurf-meeting-modi.md` §1-2 (the design this implements) and
`.superpowers/sdd/plan.md` ("Format v2 (exact)", "Task 1") for the exact
contract. `docs/kurator-kernmotivation.md` is normative for the project as
a whole -- the one rule from it that binds this module directly is "Anbauen
statt Umbauen": topic blocks and entries are only ever APPENDED (`splice`),
never reordered or rewritten.

No I/O in this module except `project_md_names` (a directory walk). No
threading, no logging, no network -- it has to stay importable from
`stage/stage.py` without pulling in anything heavier than the standard
library, and it has to give the exact same rendering whether the caller is
`gateway/consumer.py` (which writes the file) or `stage/stage.py` (which
only reads it), since both hang their tests off byte-identical output.

Document shape (written by the consumer as `HEADER_V2`, see there):

    ---
    <frontmatter, incl. `protokoll_format: 2`>
    ---

    # {title}
    <warning, Start line>

    ## Überblick

    <!-- Abgeleitete Sicht: schreibt allein der Konsument, bei jedem
    Eintrag neu. -->

    ### <section per mode>
    ...

    ## Themen

    ### HH:MM Titel
    - HH:MM [tag] Text
    ...

    ## Rohtranskript
    <touched only by the consumer's transcript writer, never by this
    module or by the agent>

`## Rohtranskript` to EOF is the one section this module treats as opaque
and never touches (AGENTS.md: "## Rohtranskript is touched only by the
consumer, never by an agent" -- `splice()` upholds the same rule for
itself, byte-identically).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from shared.text import MAX_PENDING_LABEL
from shared.wake_word import ROBO_LIKE, WAKE_NOT, WAKE_PHRASE_RE

# --- vocabulary --------------------------------------------------------

TAGS: tuple[str, ...] = ("beschluss", "aufgabe", "frage", "idee",
                          "recherche", "notiz")
MODES: tuple[str, ...] = ("sitzung", "arbeit", "folge")
DEFAULT_MODE = "sitzung"

# Section headings per mode: (heading, tag-to-collect). German headings --
# they land on the protocol page, see AGENTS.md naming convention.
# "arbeit" is a stub (MM-B refines it); "folge" gets the same sections as
# "sitzung" plus a TODO comment in render_view (MM-F: delta since the last
# meeting).
_MODE_SECTIONS: dict[str, list[tuple[str, str]]] = {
    "sitzung": [("Beschlüsse", "beschluss"), ("Aufgaben", "aufgabe"),
                ("Offene Fragen", "frage")],
    "arbeit": [("Ideen", "idee"), ("Recherche", "recherche"),
               ("Offene Fragen", "frage")],
    "folge": [("Beschlüsse", "beschluss"), ("Aufgaben", "aufgabe"),
              ("Offene Fragen", "frage")],
}

_VIEW_COMMENT = (
    "<!-- Abgeleitete Sicht: schreibt allein der Konsument, bei jedem "
    "Eintrag neu. -->"
)
_FOLGE_TODO = "<!-- TODO (MM-F): delta since the last meeting -->"

_THEMEN_MARKER = "## Themen\n"
# [ \t]*, not \s*: \s also matches newlines, and combined with MULTILINE's
# zero-width $ (matches before ANY \n, not just the line's own) a greedy
# \s* would swallow following blank lines into the match unpredictably --
# exactly the position `splice()` depends on to find where the body starts.
# Trailing \r? so a CRLF-saved file (the meeting machine has both kinds of
# editors touch it) still matches: $ under MULTILINE sits right before the
# \n, and a bare \r right before that \n would otherwise not be consumed by
# [ \t]*, making the whole line fail to match.
_UEBERBLICK_LINE_RE = re.compile(r"^## Überblick[ \t]*\r?$", re.MULTILINE)
_THEMEN_LINE_RE = re.compile(r"^## Themen[ \t]*\r?$", re.MULTILINE)
_ROH_LINE_RE = re.compile(r"^## Rohtranskript[ \t]*\r?$", re.MULTILINE)


# --- data ----------------------------------------------------------------

@dataclass
class Entry:
    """One tagged line inside a topic block: `- HH:MM [tag] Text`."""
    time: str
    tag: str
    text: str


@dataclass
class Topic:
    """One topic block: `### HH:MM Titel` plus its entries, in order.

    Carries exactly what rendering needs later (the consumer's `##
    Überblick` and the stage's right column) -- nothing more.
    """
    time: str
    title: str
    entries: list[Entry]


# --- v1 / v2 detection -----------------------------------------------------

def is_v2(text: str) -> bool:
    """True if `text` is a v2 protocol: `## Überblick`, then `## Themen`.

    Both have to be heading lines of their own (CRLF-tolerant), in that
    order -- the same shape `splice()` needs to write the file, so a
    document this accepts is one `splice()` can handle (a stray `##
    Themen` in a v1 file or a hand-written note does not make it v2). v1
    protocols (`## Verlauf` / `## Entscheidungen` / ...) are left exactly
    as they are -- no migration, ever. If a `## Rohtranskript` line is
    also present, `## Themen` has to come before it; a document with the
    order reversed is broken, not v2.
    """
    ueberblick = _UEBERBLICK_LINE_RE.search(text)
    if ueberblick is None:
        return False
    themen = _THEMEN_LINE_RE.search(text, ueberblick.end())
    if themen is None:
        return False
    roh = _ROH_LINE_RE.search(text)
    if roh is not None and roh.start() < themen.start():
        return False
    return True


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$",
                             re.MULTILINE | re.DOTALL)
_MODUS_RE = re.compile(r"^modus:[ \t]*[\"']?([A-Za-z]+)[\"']?[ \t]*\r?$",
                       re.MULTILINE)


def read_mode(text: str) -> str | None:
    """The `modus:` value from the frontmatter, if it is a known mode.

    An existing v2 file keeps the mode it was created with: the view's
    sections depend on it, and a restart with a different `--mode` flag
    must not re-render the same meeting's overview under other headings.
    None if there is no frontmatter, no `modus:` line, or an unknown value.
    """
    front = _FRONTMATTER_RE.match(text)
    if front is None:
        return None
    mode = _MODUS_RE.search(front.group(1))
    if mode is None or mode.group(1) not in MODES:
        return None
    return mode.group(1)


# --- cleaning --------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")
# Markdown structure characters that must never open a line this module
# writes: a stray '#'/'>' would turn an entry into a heading/blockquote, a
# stray '-'/'*' would look like a new list item, a stray '|' would start a
# table. Stripped only at the START of the line -- the same characters
# further in are ordinary text (a dash in "well-known" stays put).
_LEADING_MARKDOWN_RE = re.compile(r"^[#>\-*|\s]+")


def _scrub(text: str) -> str:
    """Drop whatever this text has that UTF-8 cannot encode.

    Same problem and same fix as `gateway/stage_state.scrub()` (lone
    surrogates from a truncated model output) -- kept as a local,
    self-contained copy here rather than an import, so this module stays a
    single, dependency-free file that `stage/stage.py` can import without
    pulling in `stage_state`'s threading/state machinery.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    return text


def clean_line(text: str, max_chars: int) -> str:
    """One safe line: no control chars, one line, no markdown injection.

    Used to build every title (max 120) and every entry text (max 500)
    this module ever writes, so a value that arrived from a language model
    can never break the document it lands in -- turn into a heading,
    close a code fence, or masquerade as a new list item.
    """
    cleaned = _scrub(text)
    cleaned = _CONTROL_RE.sub("", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = _LEADING_MARKDOWN_RE.sub("", cleaned).strip()
    return cleaned[:max_chars]


# --- line formatting ---------------------------------------------------

def topic_line(time: str, title: str) -> str:
    """`### HH:MM Titel\\n` -- one topic block header line."""
    return f"### {time} {clean_line(title, 120)}\n"


def entry_line(time: str, tag: str, text: str) -> str:
    """`- HH:MM [tag] Text\\n` -- one tagged entry line."""
    return f"- {time} [{tag}] {clean_line(text, 500)}\n"


# --- parsing -----------------------------------------------------------

_TOPIC_RE = re.compile(r"^### (\d{2}:\d{2}) (.+)$")
_ENTRY_RE = re.compile(r"^- (\d{2}:\d{2}) \[(\w+)\] (.*)$")


def parse_topics(text: str) -> list[Topic]:
    """Everything under `## Themen`, tolerantly.

    Only the span between `## Themen` and `## Rohtranskript` is read (or
    to EOF if there is no raw section yet). Anything in there that is not
    a topic line or an entry line is ignored -- a stray blank line, a
    comment, a line mangled by a manual edit. An entry with a tag outside
    `TAGS` is ignored too, rather than surfacing an invented one.

    An entry that arrives before any topic block opens an implicit
    `### HH:MM Beginn` block, taking the entry's own time -- the meeting
    was already talking before the agent recognised a topic.
    """
    themen = _THEMEN_LINE_RE.search(text)
    if themen is None:
        return []
    roh = _ROH_LINE_RE.search(text)
    end = roh.start() if roh is not None else len(text)
    body = text[themen.end():end]

    topics: list[Topic] = []
    current: Topic | None = None
    for line in body.splitlines():
        topic_match = _TOPIC_RE.match(line)
        if topic_match is not None:
            current = Topic(time=topic_match.group(1),
                            title=topic_match.group(2).strip(), entries=[])
            topics.append(current)
            continue
        entry_match = _ENTRY_RE.match(line)
        if entry_match is None:
            continue  # unknown line, ignored
        time, tag, entry_text = entry_match.groups()
        if tag not in TAGS:
            continue  # unknown tag, ignored
        if current is None:
            current = Topic(time=time, title="Beginn", entries=[])
            topics.append(current)
        current.entries.append(Entry(time=time, tag=tag,
                                     text=entry_text.strip()))
    return topics


# --- the derived view (`## Überblick`) ----------------------------------

def render_view(topics: list[Topic], mode: str) -> str:
    """The complete body of `## Überblick`, deterministic, zero-LLM.

    From the HTML comment through the last `*noch keine*`/entry line, plus
    the trailing blank line that separates it from `## Themen` -- exactly
    the shape `HEADER_V2` (gateway/consumer.py) ships for a fresh file, so
    `render_view([], DEFAULT_MODE)` reproduces that empty body byte for
    byte and a freshly created file needs no special-casing on its first
    re-render.

    Regenerated from scratch on every call: the ONE part of the document
    that is re-rendered rather than appended to (AGENTS.md: "Add, don't
    restructure" -- the topic blocks below stay untouched, this view does
    not).
    """
    sections = _MODE_SECTIONS.get(mode, _MODE_SECTIONS[DEFAULT_MODE])
    # The leading "" is the blank line between "## Überblick" and this
    # comment. `splice()` copies the text only up to and including
    # "## Überblick\n" -- the separating blank line is this view's own
    # first line, not something splice adds on top.
    lines = ["", _VIEW_COMMENT]
    if mode == "folge":
        lines.append(_FOLGE_TODO)
    lines.append("")
    for heading, tag in sections:
        lines.append(f"### {heading}")
        lines.append("")
        matches = [(topic, entry) for topic in topics for entry in topic.entries
                  if entry.tag == tag]
        if matches:
            for topic, entry in matches:
                lines.append(f"- {entry.time} {entry.text} "
                             f"(Thema: {topic.title})")
        else:
            lines.append("*noch keine*")
        lines.append("")
    return "\n".join(lines) + "\n"


# --- splicing (append-only write) ---------------------------------------

def splice(text: str, *, view: str, append: str) -> str:
    """The new document: unchanged prefix, a fresh view, appended topics.

    Everything up to and including `## Überblick\\n` is passed through
    unchanged. Then comes `view` (a fresh `render_view()` call -- the one
    part that is rewritten every time). Then the `## Themen` section: its
    EXISTING lines are kept exactly as they were, `append` is added at the
    end (before `## Rohtranskript`), and there is exactly one blank line
    before `## Rohtranskript` regardless of how much content is above it.
    Finally the raw section, from `## Rohtranskript` to EOF, byte-identical
    -- AGENTS.md: that section is touched only by the consumer's
    transcript writer, never by this module. This holds regardless of line
    endings: a CRLF-saved file keeps its `\\r\\n` in the raw section (and
    in `prefix`, which is also a direct slice of the original), because
    both are copied through untouched. The regenerated parts (`view` and
    the re-joined `## Themen` body) always use `\\n` -- this module's own
    canonical output -- so an existing `\\r\\n` topic/entry line survives
    with its text unchanged but its line ending normalised to `\\n`.

    Raises `ValueError` if `text` is not a v2 document.
    """
    if not is_v2(text):
        raise ValueError("splice() requires a v2 document (no '## Themen' "
                         "heading found, or it comes after '## Rohtranskript')")
    ueberblick_match = _UEBERBLICK_LINE_RE.search(text)
    if ueberblick_match is None:
        raise ValueError("splice() requires a '## Überblick' heading")
    prefix_end = ueberblick_match.end() + 1  # past the heading's own \n

    themen_match = _THEMEN_LINE_RE.search(text, prefix_end)
    if themen_match is None:
        raise ValueError("splice() requires '## Themen' after '## Überblick'")
    themen_body_start = themen_match.end() + 1  # past the heading's own \n

    roh_match = _ROH_LINE_RE.search(text, themen_body_start)
    if roh_match is None:
        raise ValueError("splice() requires a '## Rohtranskript' heading")
    roh_start = roh_match.start()

    prefix = text[:prefix_end]
    old_body = text[themen_body_start:roh_start]
    raw = text[roh_start:]

    existing = old_body.replace("\r\n", "\n").strip("\n")
    added = append.rstrip("\n") if append else ""
    parts = [p for p in (existing, added) if p]
    body = "\n".join(parts)

    themen_section = _THEMEN_MARKER + ("\n" + body + "\n\n" if body else "\n")
    return prefix + view + themen_section + raw


def split_raw(text: str) -> tuple[str, str]:
    """(head, tail): the tail starts at the raw section's heading line.

    `head + tail == text`. The tail is "" when there is no such heading.
    Lets a caller that renders the document hand over only the part in
    front of the raw section.
    """
    m = _ROH_LINE_RE.search(text)
    if m is None:
        return text, ""
    return text[:m.start()], text[m.start():]


# --- wikilinks -----------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def clean_wikilinks(text: str, names: set[str]) -> tuple[str, int, int]:
    """`[[target]]` / `[[target|label]]`, checked against `names`.

    `names` is the set of known project file stems (`project_md_names()`).
    An existing target is kept verbatim, brackets and all. A dead one --
    naming a file the project does not have -- is replaced by its label
    (or the raw target, if it had none) as plain text: still readable,
    just no longer a link that promises a file which is not there.

    Returns `(text, seen, dead)`.
    """
    seen = 0
    dead = 0

    def repl(match: re.Match) -> str:
        nonlocal seen, dead
        seen += 1
        target, label = match.group(1), match.group(2)
        basename = target.rsplit("/", 1)[-1]
        if basename.endswith(".md"):
            basename = basename[:-3]
        if basename in names:
            return match.group(0)
        dead += 1
        return label if label is not None else target

    return _WIKILINK_RE.sub(repl, text), seen, dead


# --- project file names (the one place this module touches disk) --------

_EXCLUDED_DIRS = {"sessions", "sitzungen"}


def project_md_names(folder: Path, limit: int | None = None) -> list[str]:
    """Stems of `*.md` files under `folder`, sorted and deduplicated.

    Excludes hidden paths (any component starting with `.`, e.g.
    `.trash/`, `.obsidian/`) and anything under a `sessions/` or
    `sitzungen/` folder -- a
    meeting's own session folder holds this run's machine files (protocol,
    proposals, traces), not the background material the prompt sends the
    agent to read (AGENTS.md: "PROJEKTORDNER ... is the project folder,
    not the protocol's parent"). `limit` caps the list -- the prompt uses
    60, so the wikilink instruction stays a fixed, bounded size regardless
    of how large the vault project grows.

    A `folder` that does not exist yields an empty list, not an error:
    `Path.rglob` already returns nothing for it.
    """
    names: set[str] = set()
    for path in Path(folder).rglob("*.md"):
        parts = path.relative_to(folder).parts
        if any(part in _EXCLUDED_DIRS for part in parts[:-1]):
            continue
        # Hidden paths are the tools' business, not the round's material:
        # `.trash/` holds deleted notes, `.obsidian/` the editor's config.
        # A wikilink into either would promise a file nobody meant.
        if any(part.startswith(".") for part in parts):
            continue
        names.add(path.stem)
    result = sorted(names)
    if limit is not None:
        result = result[:limit]
    return result


# --- MM-L: the pending label names the WHAT, not just the kind ----------
# Stage pending kind -> German prefix (docs/superpowers/plans/
# 2026-09-26-mm-l-pending-label.md). "entry" is the prefix for a tag_entry
# pending with no known tag; TAG_PENDING_PREFIX overrides it per protocol
# tag when one is given.
PENDING_PREFIX = {
    "topic": "Thema:",
    "entry": "Notiert:",
    "decision": "Beschluss:",
    "question": "Rückfrage:",
    "research": "Recherche:",
    "document": "Dokument:",
    "main": "Zuruf:",
}
TAG_PENDING_PREFIX = {
    "beschluss": "Beschluss:",
    "aufgabe": "Aufgabe:",
    "frage": "Frage:",
    "idee": "Idee:",
    "recherche": "Recherche:",
}

# A small, fixed German stopword list for pending_label()'s
# content_words(): function words that never carry the WHAT of a block.
# Deliberately no lemmatizer -- inflected forms seen in practice are
# listed explicitly instead of derived.
PENDING_STOPWORDS = frozenset({
    # articles / determiners
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "eines", "einem", "einen",
    # pronouns
    "ich", "du", "er", "sie", "es", "wir", "ihr",
    "mich", "dich", "ihn", "uns", "euch", "ihnen",
    "mein", "dein", "sein", "unser", "man",
    # conjunctions
    "und", "oder", "aber", "doch", "denn", "sondern",
    "weil", "dass", "wenn", "als", "wie", "ob",
    # prepositions
    "in", "an", "auf", "mit", "bei", "von", "zu", "für",
    "über", "unter", "nach", "vor", "aus", "durch", "bis",
    # preposition + article contractions
    "am", "im", "zum", "zur", "vom", "beim", "ins",
    # negation / adverbs / question and filler words
    "nicht", "noch", "auch", "mal", "so", "dann", "da", "hier", "schon",
    "nur", "bitte", "was", "wer", "sich",
    # auxiliaries
    "haben", "hat", "hatte", "ist", "sind", "war", "waren", "wird",
    # modal verbs
    "soll", "sollen", "sollte", "sollten", "muss", "müssen", "musste",
    "kann", "können", "konnte", "will", "wollen", "wollte", "möchte",
    # fillers
    "also", "ähm", "äh", "halt", "eigentlich", "quasi", "genau",
    "okay", "ja", "nein",
})
# Punctuation stripped off each token's ends before it is judged; a
# character inside the token (e.g. the hyphen of a compound word) stays.
_PENDING_PUNCT = ".,;:!?…\"'„“”‚‘’()[]{}«»"


def content_words(text, n: int = 3) -> list[str]:
    """The first `n` content words of `text`.

    Splits on whitespace, strips surrounding punctuation from each token,
    drops empties and tokens with no letter or digit at all (a bare "--",
    "—", "–" or "..." is not a word, even though it survives the
    punctuation strip because a dash isn't in `_PENDING_PUNCT` -- an
    internal hyphen, as in "Kuchenbasar-Termin", must stay),
    drops `PENDING_STOPWORDS` and drops the wake name -- "Robo" or one of
    its misheard variants -- but not "Robotik"/"Roboter...", the same
    exclusion `shared.wake_word.WAKE_NOT` applies, because those are
    content, not an address to Robo.
    """
    words: list[str] = []
    for raw in str(text).split():
        token = raw.strip(_PENDING_PUNCT)
        if not token or not any(c.isalnum() for c in token):
            continue
        low = token.lower()
        if low in PENDING_STOPWORDS:
            continue
        if ROBO_LIKE.match(low) and not WAKE_NOT.match(low):
            continue
        words.append(token)
        if len(words) >= n:
            break
    return words


def wake_source(text) -> str:
    """The part of `text` from the wake name onward (the greeting before
    it, "Hey"/"Hej"/..., is dropped along with everything earlier).

    A wake block still holds every earlier sentence of the open block, so
    the order is what follows the wake phrase, not the talk before it.
    Same matching as `gateway/segmenter.py`: the greeting-gated name,
    minus `WAKE_NOT` ("Robotik"/"Roboter" alone are not the wake word).
    The whole text when no wake phrase is found.
    """
    s = str(text)
    for m in WAKE_PHRASE_RE.finditer(s):
        if not WAKE_NOT.match(m.group("name")):
            return s[m.start("name"):]
    return s


def _cut_pending_label(label: str, limit: int) -> str:
    """Cut `label` to `limit` chars on a word boundary when there is one
    close enough (same shape as consumer.py's slug cutting) -- a hard cut
    only when the first word alone already exceeds the limit."""
    if len(label) <= limit:
        return label
    cut = label[:limit]
    if " " in cut[limit // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip()


def pending_label(kind: str, text, *, tag: str = "", title: str = "",
                   fallback: str = "") -> str:
    """The German pending label: a short prefix plus at most three content
    words, so the stage names the WHAT and not just the kind (MM-L).

    `kind` is the stage pending kind (`PENDING`'s first element), `text`
    the block or ask text, `title` the topic page title (wins over `text`
    when it yields a content word -- the `document` case), `tag` the
    protocol tag (for `entry`). Falls back to `fallback`, or the bare
    prefix when there is none, if neither source has a content word --
    the label is never empty.
    """
    if kind == "entry" and tag in TAG_PENDING_PREFIX:
        prefix = TAG_PENDING_PREFIX[tag]
    else:
        prefix = PENDING_PREFIX.get(kind, "Notiert:")
    title_words = content_words(title, 4)
    if title_words:
        source, words = title, title_words
    else:
        # A call-out block carries earlier talk; name the order (MM-L).
        source = wake_source(text) if kind == "main" else text
        words = content_words(source, 4)
        # a wake word with nothing after it: fall back to the whole text
        if not words and source is not text:
            source = text
            words = content_words(source, 4)
    if not words:
        return fallback or prefix.rstrip(":")
    # content_words() already stripped a trailing "?" off the last word
    # (it is in _PENDING_PUNCT); put it back only when that word really is
    # the source's last content word (not when the label was cut short).
    complete = len(words) <= 3
    words = words[:3]
    if complete and str(source).rstrip().endswith("?"):
        words[-1] += "?"
    return _cut_pending_label(f"{prefix} {' '.join(words)}",
                               MAX_PENDING_LABEL)


AUTONOMOUS_MARK = "*(autonom)*"


def marker(autonomous: bool, confirmed_at: str = "") -> str:
    return AUTONOMOUS_MARK if autonomous else f"*(bestätigt {confirmed_at})*"


def with_marker(text, mark: str, limit: int = 500) -> str:
    """`text` plus the marker, the marker never cut off by `limit`."""
    text = " ".join(str(text).split())
    if not mark:
        return text[:limit]
    room = max(0, limit - len(mark) - 1)
    return (text[:room].rstrip() + " " + mark).strip()
