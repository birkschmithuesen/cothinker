#!/usr/bin/env python3
# CoThinker - the running session as a graph (graph tab, MM-J)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The session graph: one pure function from what the stage already has
to the payload the graph tab draws. No I/O, no clock, no vault writes.

    session  ->  topics (the stage's history)  ->  entries (the protocol)
             ->  ring 1: notes the session links to
             ->  ring 2: their `neighbours()` -- the ONLY similarity source

TWO STEPS, ON PURPOSE. `topics_from_stage()` reads `buehne.json` and the
protocol and knows nothing about graphs; `build_session_graph()` knows
nothing about files. The first is THE SEAM for MM-C: once the protocol
carries its own topic blocks and tags, `topics_from_protocol_v2()`
replaces the first step and the second does not change.

THE ROHTRANSKRIPT RULE. Everything below `## Rohtranskript` is spoken
word, unreviewed, and the graph tab ships to whoever holds the link. It
is cut off by `protocol_head()` BEFORE anything else reads the protocol:
no entry, no wikilink and no label comes out of it -- not even a link to
a note that exists. A test plants a canary there and greps the payload.

STABLE IDS. The page diffs payloads every poll and must not relayout, so
an id is derived from content that does not move between polls: a
history row id, a hash of tag + label, a vault path.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph.neighbours import Neighbour                          # noqa: E402
from graph.ring_select import select_nodes                      # noqa: E402
from graph.vault_notes import extract_links                     # noqa: E402

TAGS = ("beschluss", "aufgabe", "frage", "idee", "recherche")
MAX_VISIBLE = 40          # nodes drawn at once; the rest is hidden, expandable
MAX_TOTAL = 200           # emergency brake, equals stage.MAX_GRAPH_NODES
RING_BUDGETS = (15, 12)   # ring 1 (wikilinks), ring 2 (neighbours())
# OPEN QUESTION, belongs to the operator (privacy, not technical): entry labels are
# up to LABEL_MAX characters of protocol text from above `## Rohtranskript`
# (decisions, tasks, open questions) -- the same class of text the stage
# already shows as decisions, but more of it, on a tab with the stage's
# exposure (token in the URL, plain HTTP). Kept as is until decided; see
# docs/entwurf-graph-schirm.md, Nachtrag 2026-09-26, open point (b).
LABEL_MAX = 80

# The protocol headings the consumer writes (gateway/consumer.py HEADER),
# plus the two MM-C adds. Only these sections produce entries by default.
SECTION_TAGS = {"Entscheidungen": "beschluss", "Aufgaben": "aufgabe",
                "Offene Fragen": "frage", "Ideen": "idee",
                "Recherche": "recherche"}

_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_SET_AT_RE = re.compile(r"T(\d\d):(\d\d)")
_INLINE_TAG_RE = re.compile(r"(?<![\w#])#(" + "|".join(TAGS) + r")\b", re.I)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.*)$")
_WIKI_ALIAS_RE = re.compile(r"!?\[\[[^\]|]*\|([^\]]*)\]\]")
_WIKI_RE = re.compile(r"!?\[\[([^\]]*)\]\]")
_MDLINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")

SESSION_SEED = "__session__"


def _text(wert) -> str:
    """Foreign JSON: a field that is not a string counts as empty."""
    return wert if isinstance(wert, str) else ""


def protocol_head(text: str) -> str:
    """Everything above the first `## Rohtranskript` line; all if absent."""
    text = _text(text)
    zeilen = text.splitlines(keepends=True)
    for i, zeile in enumerate(zeilen):
        if zeile.strip() == "## Rohtranskript":
            return "".join(zeilen[:i])
    return text


def _clean_label(text: str) -> str:
    """Markdown off, whitespace collapsed, at most LABEL_MAX characters."""
    text = _WIKI_ALIAS_RE.sub(r"\1", text)
    text = _WIKI_RE.sub(r"\1", text)
    text = _MDLINK_RE.sub(r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    text = " ".join(text.split())
    if len(text) > LABEL_MAX:
        text = text[:LABEL_MAX - 1].rstrip() + "…"
    return text


def _first_time(text: str) -> str:
    treffer = _TIME_RE.search(text)
    return f"{int(treffer.group(1)):02d}:{treffer.group(2)}" if treffer else ""


def _block_for(blocks: list[dict], zeit: str) -> str:
    """The latest block set at or before `zeit`; no time -> the last one.

    "HH:MM" strings compare correctly because both are zero-padded. A
    block without a time never wins a comparison. An entry older than
    every block goes to the first one: it was said before the stage had
    a topic, and the first topic is the nearest thing to it.
    """
    if not blocks:
        return ""
    if not zeit:
        return blocks[-1]["id"]
    treffer = ""
    for block in blocks:
        if block["time"] and block["time"] <= zeit:
            treffer = block["id"]
    return treffer or blocks[0]["id"]


def topics_from_stage(buehne: dict, protocol_text: str) -> dict:
    """Blocks, entries and links out of `buehne.json` and the protocol.

    THE SEAM for MM-C: `topics_from_protocol_v2()` will replace exactly
    this function once the protocol carries its own topic blocks and
    tags; a merge touches nothing else in this module. Until then the
    blocks are the stage's history -- the only topic structure that
    exists today -- and the entries are the bullets of the protocol's
    tagged sections.

    Robust against foreign JSON: rows that are not dicts are skipped,
    fields that are not strings count as empty.
    """
    buehne = buehne if isinstance(buehne, dict) else {}
    kopf = protocol_head(protocol_text)
    blocks: list[dict] = []
    links: list[dict] = []
    gesehen_links: set[tuple[str, str]] = set()

    def link(block: str, ziel: str) -> None:
        if (block, ziel) not in gesehen_links:
            gesehen_links.add((block, ziel))
            links.append({"block": block, "target": ziel})

    rows = buehne.get("history")
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    vorher = None
    for row in rows:
        titel = " ".join(_text(row.get("title")).split())
        if vorher is None or titel != vorher["title"]:
            zeit = _SET_AT_RE.search(_text(row.get("set_at")))
            roh_id = _text(row.get("id")).strip() or hashlib.sha1(
                (titel + _text(row.get("set_at"))).encode()).hexdigest()[:8]
            block_id = "T:" + roh_id
            while any(b["id"] == block_id for b in blocks):
                block_id += "'"
            vorher = {"id": block_id, "title": titel,
                      "time": f"{zeit.group(1)}:{zeit.group(2)}" if zeit else ""}
            blocks.append(vorher)
        for ziel in extract_links(_text(row.get("markdown")) + "\n"
                                  + _text(row.get("collapsed_markdown"))):
            link(vorher["id"], ziel)
    if not blocks:
        main = buehne.get("main") if isinstance(buehne.get("main"), dict) else {}
        titel = " ".join((_text(main.get("title"))
                          or _text(buehne.get("headline"))).split())
        if titel:
            zeit = _SET_AT_RE.search(_text(main.get("set_at"))
                                     or _text(buehne.get("updated")))
            blocks.append({"id": "T:" + (_text(main.get("id")).strip() or "main"),
                           "title": titel,
                           "time": f"{zeit.group(1)}:{zeit.group(2)}" if zeit else ""})

    entries: list[dict] = []
    gesehen_entries: set[str] = set()
    abschnitt = None
    for zeile in kopf.splitlines():
        if zeile.startswith("#"):
            ueberschrift = re.match(r"^(#+)\s+(.*?)\s*$", zeile)
            if ueberschrift and len(ueberschrift.group(1)) <= 2:
                abschnitt = SECTION_TAGS.get(ueberschrift.group(2))
        zeit = _first_time(zeile)
        for ziel in extract_links(zeile):
            link(_block_for(blocks, zeit), ziel)
        punkt = _BULLET_RE.match(zeile)
        if not punkt:
            continue
        inline = _INLINE_TAG_RE.search(punkt.group(1))
        tag = inline.group(1).lower() if inline else abschnitt
        if tag is None:
            continue
        label = _clean_label(_INLINE_TAG_RE.sub("", punkt.group(1)))
        if not label:
            continue
        eid = "E:" + hashlib.sha1((tag + label).encode()).hexdigest()[:10]
        if eid in gesehen_entries:
            continue
        gesehen_entries.add(eid)
        entries.append({"id": eid, "tag": tag, "label": label,
                        "block": _block_for(blocks, zeit), "time": zeit})
    return {"blocks": blocks, "entries": entries, "links": links}


class _SessionSource:
    """`neighbours()` for `select_nodes`: the virtual session seed's
    neighbours are the ring-1 vault notes, one per link occurrence (so a
    note several topics link to ranks first); everything else is the
    real edge source."""

    def __init__(self, ring1: list, edges) -> None:
        self.ring1 = ring1
        self.edges = edges

    def neighbours(self, note) -> list:
        if note.rel == SESSION_SEED:
            return [Neighbour(n, "wikilink", 1.0) for n in self.ring1]
        return self.edges.neighbours(note) if self.edges is not None else []


class _Seed:
    rel = SESSION_SEED
    mtime = 0.0


def _href(node_id: str) -> str:
    art, _, rest = node_id.partition(":")
    return f"{art}/{rest}" if art in ("file", "vault") and rest else ""


def _vault_note(index, node_id: str):
    """The vault Note behind a `vault:<rel>` node id, else None."""
    if index is None or not node_id.startswith("vault:"):
        return None
    return index.notes.get(node_id[len("vault:"):])


def _list(topics: dict, key: str) -> list[dict]:
    wert = topics.get(key) if isinstance(topics, dict) else None
    return [x for x in wert if isinstance(x, dict)] if isinstance(wert, list) else []


def build_session_graph(topics: dict, *, session_id: str, session_label: str,
                        resolve, index=None, edges=None, hysteresis=None,
                        max_visible: int = MAX_VISIBLE) -> dict:
    """`{"seed", "nodes", "edges", "hidden"}` for the graph tab.

    `ring` on a node: 0 for the session and its own structure (topics,
    entries), 1 for notes the session links to, 2 for their neighbours.
    Every edge whose two ends are in `nodes` is emitted, hidden ends
    included -- the page un-hides children locally and must find their
    edges then; it draws only the edges between nodes it shows.

    `resolve(name) -> node_id | None` maps a wikilink target to its node
    id (`file:<rel>` or `vault:<rel>`), None when the target is missing
    or ambiguous. Only the id comes back: the href is always derived
    from the id here (`_href`), so the two can never disagree.

    `hidden` is the cap by insertion order and changes as the session
    grows; the page keeps a node it has drawn once regardless (see
    `sgSichtbar` in static/session_graph.js).
    """
    bloecke = _list(topics, "blocks")
    knoten: list[dict] = []
    kanten: list[dict] = []
    ids: set[str] = set()

    def node(nid, label, kind, ring, *, href="", time="", tag="", parent="",
             grace=False) -> bool:
        if not nid or nid in ids or len(knoten) >= MAX_TOTAL:
            return False
        ids.add(nid)
        knoten.append({"id": nid, "label": _clean_label(_text(label)) or nid,
                       "kind": kind, "ring": ring, "href": href,
                       "time": _text(time), "tag": _text(tag),
                       "parent": parent, "hidden": False, "grace": grace})
        return True

    def edge(eid, source, target, kind, ring) -> None:
        kanten.append({"id": eid, "source": source, "target": target,
                       "kind": kind, "ring": ring})

    node(session_id, session_label or session_id, "session", 0,
         href=_href(session_id))
    # Newest topic first: it is what the room is talking about now, and
    # the visibility cap cuts from the end.
    block_ids = set()
    for b in reversed(bloecke):
        bid = _text(b.get("id"))
        if node(bid, b.get("title") or b.get("time") or bid, "topic", 0,
                time=b.get("time"), parent=session_id):
            block_ids.add(bid)

    def owner(bid: str) -> str:
        # No blocks at all (session start, empty history): the session
        # itself carries entries and links instead of dropping them.
        return bid if bid in block_ids else session_id

    for e in _list(topics, "entries"):
        if e.get("tag") not in TAGS:
            continue
        eid, eltern = _text(e.get("id")), owner(_text(e.get("block")))
        if node(eid, e.get("label"), "entry", 0, time=e.get("time"),
                tag=e.get("tag"), parent=eltern):
            edge("e:" + eid, eltern, eid, "entry", 0)
    for bid in [n["id"] for n in knoten if n["kind"] == "topic"]:
        edge("b:" + bid, session_id, bid, "block", 0)

    # Ring 1: every resolvable wikilink of the session, in link order.
    ring1: dict[str, str] = {}              # node id -> first topic
    ring1_links: list[tuple[str, str]] = []
    vault_ring1: list = []
    for l in _list(topics, "links"):
        nid = _text(resolve(_text(l.get("target"))))
        if not nid:
            continue                        # unresolved: no node, no edge
        ring1_links.append((owner(_text(l.get("block"))), nid))
        ring1.setdefault(nid, ring1_links[-1][0])
        note = _vault_note(index, nid)
        if note is not None:
            vault_ring1.append(note)

    # Ring 2 through the one selection the vault graph uses too.
    gewaehlt = select_nodes(_Seed(), _SessionSource(vault_ring1, edges),
                            budgets=RING_BUDGETS, max_nodes=MAX_TOTAL)
    gewaehlt.pop(SESSION_SEED, None)
    alle = set(gewaehlt)
    if hysteresis is not None and index is not None:
        alle = hysteresis.apply(set(gewaehlt), set(index.notes))

    # Neighbour lists are asked once per note and reused below.
    cache: dict[str, list] = {}

    def nachbarn(rel: str) -> list:
        if rel not in cache:
            note = index.notes.get(rel) if index is not None else None
            cache[rel] = (edges.neighbours(note)
                          if note is not None and edges is not None else [])
        return cache[rel]

    for nid, eltern in ring1.items():
        note = _vault_note(index, nid)
        if note is not None:
            if gewaehlt.get(note.rel) != 1:
                continue                    # over the ring-1 budget
            label = note.stem
        else:
            # A project file (or a vault link without an index): a node,
            # but no note to ask for neighbours -- it feeds no ring 2.
            label = nid.partition(":")[2].split("/")[-1] or nid
        node(nid, label, "note", 1, href=_href(nid), parent=eltern)
    ring1_rels = [n["id"][len("vault:"):] for n in knoten
                  if n["ring"] == 1 and n["id"].startswith("vault:")]
    # Ring 2 in the selection's own ranking (dict order), then whatever
    # only the grace list still holds, sorted for a stable payload.
    ring2 = [rel for rel, ring in gewaehlt.items() if ring >= 2]
    ring2 += sorted(alle - set(gewaehlt))
    for rel in ring2:
        if index is None or rel not in index.notes:
            continue
        eltern = next((("vault:" + r1) for r1 in ring1_rels
                       if any(x.note.rel == rel for x in nachbarn(r1))),
                      session_id)
        node("vault:" + rel, index.notes[rel].stem, "note", 2,
             href="vault/" + rel, parent=eltern,
             grace=rel not in gewaehlt)

    paare: set[str] = set()
    for quelle, nid in ring1_links:
        eid = "w:" + quelle + "|" + nid
        if nid in ids and quelle in ids and eid not in paare:
            paare.add(eid)
            edge(eid, quelle, nid, "wikilink", 1)
    for n in [k for k in knoten if k["ring"] == 2]:
        for x in nachbarn(n["id"][len("vault:"):]):
            ziel = "vault:" + x.note.rel
            if ziel not in ids or ziel == n["id"]:
                continue
            a, b = sorted((n["id"], ziel))
            eid = "n:" + x.kind[0] + ":" + a + "|" + b
            if eid not in paare:
                paare.add(eid)
                edge(eid, a, b, x.kind, 2)

    for i, n in enumerate(knoten):
        n["hidden"] = i >= max_visible
    return {"seed": session_id, "nodes": knoten, "edges": kanten,
            "hidden": sum(1 for n in knoten if n["hidden"])}
