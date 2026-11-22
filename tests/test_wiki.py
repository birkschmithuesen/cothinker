# CoThinker — tests of the wiki layer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The wiki layer: which project notes a topic title belongs to.

A synthetic project ("Obstwiese") built in a temp dir -- no real vault,
no session text. Neighbours come from ONE place, HardEdges.neighbours;
this file checks the title -> transient note mapping around it, the
link suffix on a new topic block and the consumer wiring.

    python3 -m pytest -q tests/test_wiki.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from check_collector import Checks  # noqa: E402

_C = Checks()
check = _C.check
import blocks.knowledge_link.wiki as wiki  # noqa: E402
from shared.minutes_format import marker  # noqa: E402
import blocks.decisions_tasks_questions as decisions_mod  # noqa: E402
from blocks.decisions_tasks_questions import page_call_out_note  # noqa: E402


def attach_wiki(writer, layer):
    """Hands the writer's knowledge-link block a wiki layer (or a stand-in)."""
    writer.knowledge_link._wiki = layer

tmp = Path(tempfile.mkdtemp(prefix="cothinker-wiki-"))
projekt = tmp / "obstwiese"


def note(rel: str, text: str) -> None:
    p = projekt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


note("kosten.md", "---\ntags: [finanzen]\n---\nSiehe [[aufgaben]].\n")
note("aufgaben.md", "---\ntags: [orga]\n---\nListe.\n")
note("streuobst.md", "---\ntags: [streuobst, natur]\n---\nWiese.\n")
note("pflege.md", "---\ntags: [natur]\n---\nSchnitt.\n")
note("foerderung.md", "---\ntags: [finanzen]\n---\nAntrag.\n")
note("ernte.md", "Nichts.\n")
# The same stem in two folders: never linked, it cannot be resolved.
note("a/termine.md", "Eins.\n")
note("b/termine.md", "Zwei.\n")
# Too short to match by itself (< 4 chars after normalising).
note("hof.md", "---\ntags: [natur]\n---\n")
note("tee.md", "Tee.\n")
# Umlaut stem: matched through the normaliser.
note("gäste.md", "Liste.\n")


# ================================================================= slugify
print("slugify")
check("casefold + umlauts + non-alnum -> '-'",
      wiki.slugify("Kosten & Förderung für Gäste!") ==
      "kosten-foerderung-fuer-gaeste", wiki.slugify("Kosten & Förderung für Gäste!"))
check("ß -> ss, runs collapse, no edge hyphens",
      wiki.slugify("  --Straße  der   Obstwiese-- ") == "strasse-der-obstwiese")
check("capped at 60 by default, no trailing hyphen",
      len(wiki.slugify("wort " * 40)) <= 60
      and not wiki.slugify("wort " * 40).endswith("-"))
check("an explicit limit", wiki.slugify("eins zwei drei", limit=8) == "eins-zwe")
check("nothing left -> empty", wiki.slugify("!!! ...") == "")


# ================================================================= nachbarn
print("\nnachbarn: title -> transient note -> HardEdges.neighbours")
w = wiki.WikiLayer(projekt)
stems = [n.stem for n in w.nachbarn("Kosten der Streuobstwiese")]
check("a stem inside the title is a neighbour", "kosten" in stems, str(stems))
check("a stem must be a whole token sequence (streuobst != streuobstwiese)",
      "streuobst" not in stems, str(stems))
stems = [n.stem for n in w.nachbarn("Kosten und Aufgaben")]
check("two title stems, sorted by rel", stems == ["aufgaben", "kosten"],
      str(stems))
stems = [n.stem for n in w.nachbarn("Kosten und Finanzen")]
check("links rank before tag neighbours",
      stems == ["kosten", "foerderung"], str(stems))
stems = [n.stem for n in w.nachbarn("Streuobst und Natur")]
check("a title token equal to a project tag brings the tag group, "
      "the link first", stems == ["streuobst", "hof", "pflege"], str(stems))
stems = [n.stem for n in w.nachbarn("Hof und Tee")]
check("stems shorter than 4 chars do not match", stems == [], str(stems))
check("a 4-char stem does", [n.stem for n in w.nachbarn("Ernte am Hof")]
      == ["ernte"])
stems = [n.stem for n in w.nachbarn("Gäste einladen")]
check("umlaut stems match through the normaliser", "gäste" in stems, str(stems))
stems = [n.stem for n in w.nachbarn("Termine festlegen")]
check("an ambiguous stem yields nothing", stems == [], str(stems))
check("k caps the list",
      len(w.nachbarn("Kosten und Aufgaben", k=1)) == 1)
VIER = "Kosten, Aufgaben, Ernte und Finanzen"
check("four neighbours for the max-3 title",
      [n.stem for n in w.nachbarn(VIER)]
      == ["aufgaben", "ernte", "kosten", "foerderung"],
      str([n.stem for n in w.nachbarn(VIER)]))
check("empty title -> []", w.nachbarn("") == [] and w.nachbarn("   ") == [])
check("non-string title -> [] (never raises)", w.nachbarn(None) == [])  # type: ignore[arg-type]
quelle = Path(wiki.__file__).read_text(encoding="utf-8")
check("exactly one HardEdges.neighbours call site",
      quelle.count(".neighbours(") == 1, str(quelle.count(".neighbours(")))
check("the stage-2 comment line sits at it",
      "# Stufe 2 (Embeddings): sobald graph/embeddings.py existiert, hier "
      "austauschen — HardEdges.neighbours bleibt der Vertrag" in quelle)


# ============================================================== link_suffix
print("\nlink_suffix: only existing, unique notes, max 3, fits the room")
check("format ' -> [[a]], [[b]]'",
      w.link_suffix("Kosten und Aufgaben", room=200)
      == (" -> [[aufgaben]], [[kosten]]", 2))
suffix, n = w.link_suffix(VIER, room=200)
check("at most 3 links",
      (suffix, n) == (" -> [[aufgaben]], [[ernte]], [[kosten]]", 3), suffix)
check("max_links caps it",
      w.link_suffix(VIER, room=200, max_links=1) == (" -> [[aufgaben]]", 1))
suffix, n = w.link_suffix(VIER, room=len(" -> [[aufgaben]], [[ernte]]"))
check("links are dropped from the end until it fits",
      (suffix, n) == (" -> [[aufgaben]], [[ernte]]", 2), suffix)
suffix, n = w.link_suffix(VIER, room=len(" -> [[aufgaben]]") + 3)
check("a link is never cut in half", (suffix, n) == (" -> [[aufgaben]]", 1),
      suffix)
check("no room -> no suffix", w.link_suffix("Kosten", room=5) == ("", 0))
check("no neighbours -> no suffix", w.link_suffix("Wetter morgen", room=200) == ("", 0))
check("ambiguous notes are never linked",
      "termine" not in w.link_suffix("Termine und Kosten", room=200)[0])
# A tag neighbour whose stem exists twice must not be linked either.
note("c/pflege.md", "---\ntags: [natur]\n---\nDoppelt.\n")
w_neu = wiki.WikiLayer(projekt)
check("an ambiguous tag neighbour is dropped from the suffix",
      "[[pflege]]" not in w_neu.link_suffix("Streuobst und Natur", room=200)[0],
      w_neu.link_suffix("Streuobst und Natur", room=200)[0])
(projekt / "c" / "pflege.md").unlink()
(projekt / "c").rmdir()


# ============================================================ cache / rescan
print("\ncache: one scan per RESCAN_S, injectable clock")
jetzt = [1000.0]
scans = [0]
original_scan = wiki.scan_vault


def zaehlender_scan(*a, **kw):
    scans[0] += 1
    return original_scan(*a, **kw)


wiki.scan_vault = zaehlender_scan
try:
    wc = wiki.WikiLayer(projekt, clock=lambda: jetzt[0])
    check("RESCAN_S is 60", wiki.RESCAN_S == 60)
    wc.nachbarn("Kosten")
    wc.nachbarn("Aufgaben")
    check("two calls inside the window scan once", scans[0] == 1, str(scans))
    note("wetter.md", "Neu.\n")
    check("a new note is not seen inside the window",
          [n.stem for n in wc.nachbarn("Wetter heute")] == [])
    jetzt[0] += 61
    check("after the window it is",
          [n.stem for n in wc.nachbarn("Wetter heute")] == ["wetter"]
          and scans[0] == 2, str(scans))
    kurz = wiki.WikiLayer(projekt, clock=lambda: jetzt[0], rescan_s=5)
    kurz.nachbarn("Kosten")
    jetzt[0] += 6
    kurz.nachbarn("Kosten")
    check("rescan_s is injectable", scans[0] == 4, str(scans))

    def kaputt(*a, **kw):
        raise OSError("disk gone")

    wiki.scan_vault = kaputt
    wk = wiki.WikiLayer(projekt, clock=lambda: jetzt[0])
    check("a scan error yields no neighbours, never an exception",
          wk.nachbarn("Kosten") == [])
    check("and no suffix", wk.link_suffix("Kosten", room=100) == ("", 0))
finally:
    wiki.scan_vault = original_scan
check("a missing project folder yields []",
      wiki.WikiLayer(tmp / "gibt-es-nicht").nachbarn("Kosten") == [])


# ================================================================ topic_title
print("\ntopic_title: title + links + marker in 120 chars")
wt = wiki.WikiLayer(projekt)
titel, n = wiki.topic_title("Kosten und Finanzen", "*(autonom)*", wt)
check("links before the marker",
      titel == "Kosten und Finanzen -> [[kosten]], [[foerderung]] *(autonom)*"
      and n == 2, titel)
lang = "Kosten Aufgaben Ernte " + "x" * 60
titel, n = wiki.topic_title(lang, "*(bestätigt 14:05)*", wt)
check("fits 120 chars, marker intact, no half link",
      len(titel) <= 120 and titel.endswith("*(bestätigt 14:05)*")
      and titel.count("[[") == titel.count("]]"), f"{len(titel)} {titel}")
check("links were dropped from the end, not all", 0 < n < 3, str(n))
sehr_lang = "Kosten " + "y" * 200
titel, n = wiki.topic_title(sehr_lang, "*(autonom)*", wt)
check("no room: no links, the title is cut, the marker never",
      n == 0 and len(titel) <= 120 and titel.endswith(" *(autonom)*"), titel)
check("without a marker",
      wiki.topic_title("Kosten", "", wt) == ("Kosten -> [[kosten]]", 1))
check("without a wiki layer: title (+ marker) only",
      wiki.topic_title("Kosten", "*(autonom)*", None) == ("Kosten *(autonom)*", 0))


class Kaputt:
    def link_suffix(self, *a, **kw):
        raise RuntimeError("boom")


check("a failing wiki costs the links, never the title",
      wiki.topic_title("Kosten", "", Kaputt()) == ("Kosten", 0))


# ================================================================ consumer
print("\nconsumer: a topic action writes the links into the v2 protocol")
from gateway import consumer  # noqa: E402
from shared import events as _events  # noqa: E402
from stage_state import StageState  # noqa: E402

sitzung = projekt / "sitzungen" / "2026-09-26-obstwiese"
sitzung.mkdir(parents=True)
_HEAD = dict(title="Synthetischer Termin", entity="obstwiese", day="2026-09-26",
             pslug="obstwiese", participants="A, B", start="10:00")
p_prot = sitzung / "protokoll.md"
p_prot.write_text(consumer.protocol_header(**_HEAD, mode="sitzung"),
                  encoding="utf-8")
prot = consumer.Protocol(p_prot, tmp)
check("fixture protocol is v2", prot.is_v2)
zst = StageState()
schreiber = consumer.StageWriter(sitzung, zst, log=lambda *a: None,
                                 proposals=sitzung / "vorschlag.jsonl",
                                 events=_events.EventLog(sitzung / "events.jsonl"))
schreiber.protocol = prot
attach_wiki(schreiber, wiki.WikiLayer(projekt))
with (sitzung / "vorschlag.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"action": "topic", "title": "Kosten und Finanzen",
                         "markdown": "## Kosten"}) + "\n")
schreiber.ingest()
text = p_prot.read_text(encoding="utf-8")
check("### HH:MM Titel -> [[kosten]] … in the protocol",
      re.search(r"^### \d\d:\d\d Kosten und Finanzen -> \[\[kosten\]\], "
                r"\[\[foerderung\]\]$", text, re.M) is not None, text[-400:])
check("the stage title is unchanged", zst.main.title == "Kosten und Finanzen",
      zst.main.title)
spur = [json.loads(z) for z in
        (sitzung / "events.jsonl").read_text(encoding="utf-8").splitlines()]
links = [e for e in spur if e.get("event") == "wiki_link"]
check("one wiki_link event with a number",
      len(links) == 1 and links[0].get("links") == 2, str(links))
check("and no text in it",
      len(links) == 1 and set(links[0]) == {"at", "event", "links"}
      and "Kosten" not in json.dumps(links[0]), str(links))

print("\nconsumer: a failing wiki costs the links, not the block")
zst2 = StageState()
d2 = sitzung / "w2"
d2.mkdir()
p2 = d2 / "protokoll.md"
p2.write_text(consumer.protocol_header(**_HEAD, mode="sitzung"), encoding="utf-8")
schreiber2 = consumer.StageWriter(d2, zst2, log=lambda *a: None,
                                  proposals=d2 / "vorschlag.jsonl",
                                  events=_events.EventLog(d2 / "events.jsonl"))
schreiber2.protocol = consumer.Protocol(p2, tmp)
attach_wiki(schreiber2, Kaputt())
with (d2 / "vorschlag.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"action": "topic", "title": "Kosten",
                         "markdown": "## Kosten"}) + "\n")
schreiber2.ingest()
check("the block is written without links",
      re.search(r"^### \d\d:\d\d Kosten$", p2.read_text(encoding="utf-8"),
                re.M) is not None)
check("the writer's block has no vault by default",
      consumer.StageWriter(d2, StageState(), log=lambda *a: None,
                           proposals=d2 / "vorschlag.jsonl").knowledge_link.has_vault is False)
check("the consumer builds the wiki layer from the project folder",
      "knowledge_link.use_project(folder)" in Path(consumer.__file__).read_text(encoding="utf-8"))
check("`links` is a numeric event field",
      "links" in _events.NUMERIC_FIELDS)


# ============================================================ topic pages
print("\npage_links: unique neighbours, topped up with the index note")
from datetime import datetime  # noqa: E402

wp = wiki.WikiLayer(projekt)
check("two unique neighbours from the fixture",
      wp.page_links("Kosten und Aufgaben") == ["aufgaben", "kosten"],
      str(wp.page_links("Kosten und Aufgaben")))
check("no ambiguous stem among them (termine exists twice)",
      "termine" not in wp.page_links("Termine und Kosten"),
      str(wp.page_links("Termine und Kosten")))
check("k caps them", len(wp.page_links("Kosten Aufgaben Ernte", k=2)) == 2)
check("fewer than 2 and no index note: what there is, nothing invented",
      wp.page_links("Ernte") == ["ernte"], str(wp.page_links("Ernte")))
note("obstwiese.md", "Startseite.\n")          # the project's index note
wp2 = wiki.WikiLayer(projekt)
check("fewer than 2: topped up with the index note",
      wp2.page_links("Ernte") == ["ernte", "obstwiese"],
      str(wp2.page_links("Ernte")))
check("nothing found: the index note alone",
      wp2.page_links("Regen morgen") == ["obstwiese"],
      str(wp2.page_links("Regen morgen")))
check("enough found: no top-up",
      "obstwiese" not in wp2.page_links("Kosten und Aufgaben"))
check("taken(): an existing stem, any case", wp2.taken("Kosten")
      and not wp2.taken("kosten-der-wiese"))
check("a failing scan: no links, nothing taken",
      wiki.WikiLayer(tmp / "fehlt").page_links("Kosten") == []
      and wiki.WikiLayer(tmp / "fehlt").taken("kosten") is False)

print("\ncreate_topic_page: skeleton, exclusive, collisions")
JETZT = datetime(2026, 9, 26, 14, 7)
seiten = tmp / "seiten-sitzung"
pfad = wiki.create_topic_page(
    seiten, projekt, "Kosten und Aufgaben", ["aufgaben", "kosten"],
    "*(autonom)*", JETZT, projekt / "sitzungen" / "x" / "protokoll.md")
erwartet = ("---\ntype: topic\ncreated: 2026-09-26 14:07\n"
            "sources: [sitzungen/x/protokoll.md]\ncontributors: [robo]\n"
            "---\n\n# Kosten und Aufgaben\n\n*(autonom)*\n\n"
            "Verwandt: [[aufgaben]], [[kosten]]\n\n")
check("in <session>/entwuerfe/<slug>.md",
      pfad == seiten / "entwuerfe" / "kosten-und-aufgaben.md", str(pfad))
check("frontmatter, title, marker, links -- exactly",
      pfad is not None and pfad.read_text("utf-8") == erwartet,
      pfad.read_text("utf-8") if pfad else "")
zweite = wiki.create_topic_page(seiten, projekt, "Kosten und Aufgaben", [],
                                "", JETZT, "p.md")
check("same name again: -2, the first untouched, no empty Verwandt line",
      zweite == seiten / "entwuerfe" / "kosten-und-aufgaben-2.md"
      and pfad.read_text("utf-8") == erwartet
      and "Verwandt" not in zweite.read_text("utf-8"))
check("no title: seite-HHMM and a heading of its own",
      (lambda q: q is not None and q.name == "seite-1407.md"
       and "# Themenseite 14:07" in q.read_text("utf-8"))(
          wiki.create_topic_page(seiten, projekt, "", [], "", JETZT, "p.md")))
belegt = wiki.create_topic_page(seiten, projekt, "Kosten", [], "", JETZT,
                                "p.md", taken=lambda st: st == "kosten")
check("a stem a project note carries is skipped (-2)",
      belegt is not None and belegt.name == "kosten-2.md")
gruende: list = []
voll = wiki.create_topic_page(seiten, projekt, "Kosten", [], "", JETZT,
                              "p.md", taken=lambda st: True,
                              report=gruende.append)
check("every name taken: None, reported name_taken",
      voll is None and gruende == ["name_taken"], str(gruende))
im_weg = tmp / "im-weg"
im_weg.mkdir()
(im_weg / "entwuerfe").write_text("Datei\n", encoding="utf-8")
gruende = []
check("unwritable folder: None, never raises, a token reported",
      wiki.create_topic_page(im_weg, projekt, "Kosten", [], "", JETZT,
                             "p.md", report=gruende.append) is None
      and len(gruende) == 1 and re.fullmatch(r"[A-Za-z_]+", gruende[0]),
      str(gruende))

print("\nZuruf regex: zero-LLM, either order")
for satz in ("Robo, leg dafür eine Seite an",
             "Robo, mach mal eine Seite dazu",
             "Robo, erstelle eine Seite",
             "Robo, leg dafuer eine Seite an",
             "Robo, lege eine Seite dazu an",
             "Robo, eine Seite dazu anlegen bitte",
             "ROBO ERSTELLT EINE SEITE",
             "Robo, mach mal 'ne neue Seite",
             "Robo, mach mal ’ne Seite dazu",
             "Robo, leg die Seite dafuer an"):
    check(f"yes: {satz}", wiki.wants_page(satz))
for satz in ("die Seite drei im Antrag",
             "Robotik-Seite",
             "Robo, zeig uns die Liste",
             "Robo, mach weiter, die Seite drei im Antrag ist schon da, "
             "das wissen wir alle",
             "Robo mach weiter die Seite drei",
             "Robo, mach mal die Seite zwei auf",
             "Robo, leg keine Seite an",
             "Robo, leg bitte nicht eine Seite an",
             "Robo, mach keine Seite",
             "Robo, eine Seite nicht anlegen",
             "Robo, mach bitte nicht eine Seite dazu",
             "Robo, leg das zur Webseite",
             ""):
    check(f"no: {satz!r}", not wiki.wants_page(satz))
check("not a string: no", not wiki.wants_page(None))

print("\nconsumer: the Zuruf creates the page (no level 2)")
from segmenter import Block  # noqa: E402

z_dir = projekt / "sitzungen" / "2026-09-26-zuruf"
z_dir.mkdir(parents=True)
z_prot = z_dir / "protokoll.md"
z_prot.write_text(consumer.protocol_header(**_HEAD, mode="sitzung"),
                  encoding="utf-8")
z_st = StageState()
z_w = consumer.StageWriter(z_dir, z_st, log=lambda *a: None,
                           proposals=z_dir / "vorschlag.jsonl",
                           events=_events.EventLog(z_dir / "events.jsonl"))
z_w.protocol = consumer.Protocol(z_prot, tmp)
attach_wiki(z_w, wiki.WikiLayer(projekt))
logs: list = []


def zlog(*a):
    logs.append(a)


def blk(text, wake):
    return Block(sentences=[text], reason="wake" if wake else "pause",
                 wake_word=wake)


def _page_call_out(writer, runner, b, *, session_dir, project_dir, log,
                   clock):
    """Mirrors `DecisionsBlock._page_call_out` (blocks/decisions_tasks_questions)
    for a bare writer/runner stand-in, without going through `attach()`."""
    try:
        if (not getattr(b, "wake_word", False) or writer.protocol is None
                or not writer.knowledge_link.wants_page(getattr(b, "text", ""))):
            return None
        if runner is not None:
            seite = runner.page_order(b.text)
        else:
            jetzt = clock()
            seite = writer.file_topic_page(
                writer.page_title(), session_dir=session_dir,
                project_dir=project_dir,
                mark=marker(False, f"{jetzt:%H:%M}"), created=jetzt)
        return seite if seite is not None else writer.topic_page()
    except Exception as e:                # noqa: BLE001 -- agent thread
        log("topic page call-out skipped:", repr(e))
        return None


def zuruf(writer, runner, text, wake=True):
    return _page_call_out(writer, runner, blk(text, wake),
                          session_dir=z_dir, project_dir=projekt,
                          log=zlog, clock=lambda: JETZT)


# The page title: the protocol's CURRENT block, not the stage.
check("page_title: no topic block -> the stage's main title",
      z_w.page_title() == "" and not z_st.apply(
          {"action": "topic", "title": "Etwas ganz anderes",
           "markdown": "- x"})
      and z_w.page_title() == "Etwas ganz anderes", z_w.page_title())
z_w.protocol.open_topic("Kosten und Aufgaben -> [[aufgaben]], [[kosten]] "
                        "*(autonom)*")
check("page_title: the last protocol block, links and marker stripped",
      z_w.page_title() == "Kosten und Aufgaben", z_w.page_title())
class _RoherTitel:
    """A protocol whose current block title is not a clean line."""
    path = z_prot

    @staticmethod
    def current_topic_title():
        return "## Obst\x07wiese\n  Kosten " + "x" * 200


class _Stub:
    protocol = _RoherTitel()
    state = z_st
    knowledge_link = z_w.knowledge_link


roh = consumer.StageWriter.page_title(_Stub())
check("review 8: page_title is cleaned ONCE (the guard's slug, the links "
      "and the page all see the same string)",
      roh == consumer.clean_line(roh, wiki.TITLE_MAX)
      and roh.startswith("Obstwiese Kosten x")
      and len(roh) == wiki.TITLE_MAX, repr(roh))
check("bare_title strips the bestätigt marker too",
      wiki.bare_title("Kosten -> [[kosten]] *(bestätigt 14:05)*") == "Kosten"
      and wiki.bare_title("A -> B") == "A -> B")

ohne = zuruf(z_w, None, "Robo, zeig die Liste")
ohne_weck = zuruf(z_w, None, "mach mal eine Seite dazu", wake=False)
check("no page without the order, nor without the wake word",
      ohne is None and ohne_weck is None
      and not (z_dir / "entwuerfe").exists())
ja = zuruf(z_w, None, "Robo, leg dafür eine Seite an")
z_seite = z_dir / "entwuerfe" / "kosten-und-aufgaben.md"
z_text = z_seite.read_text("utf-8") if z_seite.exists() else ""
check("a page in entwuerfe/ named after the PROTOCOL block, bestätigt, "
      ">= 2 links",
      ja == z_seite and "# Kosten und Aufgaben\n" in z_text
      and "*(bestätigt 14:07)*" in z_text
      and "Verwandt: [[aufgaben]], [[kosten]]" in z_text
      and "sources: [sitzungen/2026-09-26-zuruf/protokoll.md]" in z_text,
      z_text)
z_pt = z_prot.read_text("utf-8").split("## Rohtranskript")[0]
check("the backlink entry in the current topic block",
      re.search(r"### \d\d:\d\d Kosten und Aufgaben -> .*\n"
                r"- \d\d:\d\d \[notiz\] Themenseite "
                r"\[\[kosten-und-aufgaben\]\] \*\(bestätigt 14:07\)\*",
                z_pt) is not None, z_pt[-300:])
z_spur = [json.loads(z) for z in
          (z_dir / "events.jsonl").read_text("utf-8").splitlines()]
z_ev = [e for e in z_spur if e.get("event") == "wiki_create"]
check("one wiki_create event, a number, no text",
      len(z_ev) == 1 and z_ev[0].get("links") == 2
      and set(z_ev[0]) == {"at", "event", "links"}, str(z_ev))

def spur(event, **felder):
    return [e for e in (json.loads(z) for z in (z_dir / "events.jsonl")
                        .read_text("utf-8").splitlines())
            if e.get("event") == event
            and all(e.get(k) == v for k, v in felder.items())]


# Fix pass 2, A: at most ONE page per protocol topic block per run.
vorher = sorted((z_dir / "entwuerfe").iterdir())
nochmal = zuruf(z_w, None, "Robo, erstelle eine Seite")
check("A: the same block again -> no second page, no second backlink, "
      "policy_refused page_exists; the call-out names the existing page",
      nochmal == z_seite
      and sorted((z_dir / "entwuerfe").iterdir()) == vorher
      and z_prot.read_text("utf-8").count("Themenseite [[") == 1
      and len(spur("policy_refused", kind="document",
                   reason="page_exists")) == 1
      and z_w.topic_page() == z_seite, str(spur("policy_refused")))
z_w.protocol.open_topic("Kosten und Aufgaben")      # a NEW block, same title
check("A: a new block has no page yet", z_w.topic_page() is None)
z_w.link_names = {"kosten"}      # the project list never has the page
zuruf(z_w, None, "Robo, erstelle eine Seite")
check("the backlink survives the dead-link check (page is in the session)",
      "[[kosten-und-aufgaben-2]]" in z_prot.read_text("utf-8"))


class LevelZwei:
    def __init__(self):
        self.orders = []

    def page_order(self, block):
        self.orders.append(block)
        return z_dir / "entwuerfe" / "stub.md"


l2 = LevelZwei()
vorher = sorted((z_dir / "entwuerfe").iterdir())
check("with level 2: the page goes through it (fill order)",
      zuruf(z_w, l2, "Robo, mach mal eine Seite dazu")
      == z_dir / "entwuerfe" / "stub.md"
      and l2.orders == ["Robo, mach mal eine Seite dazu"]
      and sorted((z_dir / "entwuerfe").iterdir()) == vorher)
z_w.protocol = None
check("v1 protocol: no page",
      zuruf(z_w, None, "Robo, leg eine Seite an") is None)


class Kaputt2:
    def page_order(self, block):
        raise RuntimeError("kaputt")


z_w.protocol = consumer.Protocol(z_prot, tmp)
check("a failing level 2 never raises into the turn",
      zuruf(z_w, Kaputt2(), "Robo, leg eine Seite an") is None)
check("turn_orders runs page_call_out before policy_turn",
      (lambda src: 0 < src.index("seite = self._page_call_out(segment)")
       < src.index("self._policy_turn(fmt, segment)"))(
          Path(decisions_mod.__file__).read_text(encoding="utf-8")))

print("\nconsumer: the prompt of a Zuruf turn")
import blocks.decisions_tasks_questions.policy_runner as _pr  # noqa: E402

ZURUF = "Robo, leg dafür eine Seite an"


def _turn_orders(writer, runner, fmt, b, *, session_dir, project_dir, log,
                 clock):
    """Mirrors `DecisionsBlock.turn_orders` for a bare writer/runner
    stand-in, without going through `attach()`."""
    seite = _page_call_out(writer, runner, b, session_dir=session_dir,
                           project_dir=project_dir, log=log, clock=clock)
    if runner is None:
        extra_note, gesprochen = "", b.text
    else:
        auftraege = runner.take_turn()
        extra_note = auftraege.note
        if auftraege.wake and not b.wake_word:
            extra_note += consumer.order_note_for(fmt, True)
        gesprochen = b.text + (" " + auftraege.spoken if auftraege.spoken else "")
    return (extra_note + page_call_out_note(seite, runner is not None),
            gesprochen)


def zuruf_prompt(runner):
    b = blk(ZURUF, True)
    extra, gesprochen = _turn_orders(
        z_w, runner, 2, b, session_dir=z_dir, project_dir=projekt,
        log=zlog, clock=lambda: JETZT)
    return consumer.build_prompt(
        z_prot, z_dir / "vorschlag.jsonl", b, z_st,
        order_note=consumer.order_note_for(2, True) + extra,
        project_dir=projekt, fmt=2), gesprochen


z_w.protocol.open_topic("Zuruf ohne Stufe zwei")
vorher = set((z_dir / "entwuerfe").iterdir())
prompt, gesprochen = zuruf_prompt(None)
neu = sorted(set((z_dir / "entwuerfe").iterdir()) - vorher)
check("without level 2: one new page, named in the prompt as done",
      len(neu) == 1 and f"`{neu[0]}`" in prompt
      and f"[[{neu[0].stem}]] steht schon im" in prompt
      and "ZURUF SEITE" in prompt, prompt[-1500:])
check("... it overrides the order note: no further file, no entry",
      "DIREKTER AUFTRAG" in prompt
      and "KEINE weitere Datei und KEINEN Protokolleintrag" in prompt
      and prompt.index("DIREKTER AUFTRAG") < prompt.index("ZURUF SEITE"))
check("... and asks the agent to fill it, once",
      prompt.count("Ergaenze die Seite") == 1)
check("the spoken text is the block", gesprochen == ZURUF)
ohne_seite = neu[0]
vorher = set((z_dir / "entwuerfe").iterdir())
prompt, _ = zuruf_prompt(None)
check("A: the Zuruf again in the same block -> no new page, the note names "
      "the EXISTING one",
      set((z_dir / "entwuerfe").iterdir()) == vorher
      and f"`{ohne_seite}`" in prompt and "ZURUF SEITE" in prompt
      and "KEINE weitere Datei" in prompt, prompt[-1500:])

runner = _pr.PolicyRunner(z_w, mode="sitzung", session_dir=z_dir,
                          backend=None, decide_fn=lambda *a, **k: None,
                          log=zlog, clock=lambda: JETZT, project_dir=projekt)
z_w.policy = runner
z_w.protocol.open_topic("Zuruf mit Stufe zwei")
vorher = set((z_dir / "entwuerfe").iterdir())
prompt, _ = zuruf_prompt(runner)
neu = sorted(set((z_dir / "entwuerfe").iterdir()) - vorher)
check("with level 2: one new page, the fill order AND the done note",
      len(neu) == 1 and "AUFTRAG A1: Die Themenseite" in prompt
      and prompt.count(f"`{neu[0]}`") == 2 and "ZURUF SEITE" in prompt,
      prompt[-1500:])
check("... the filling is asked for once, not twice",
      prompt.count("Ergaenze die Seite") == 1)
mit_seite = neu[0]
runner.end_turn(True)
vorher = set((z_dir / "entwuerfe").iterdir())
prompt, _ = zuruf_prompt(runner)
check("A with level 2: same block again -> no page, no second fill order, "
      "the note names the existing page",
      set((z_dir / "entwuerfe").iterdir()) == vorher
      and "AUFTRAG A2" not in prompt and f"`{mit_seite}`" in prompt
      and "ZURUF SEITE" in prompt, prompt[-1500:])
z_w.policy = None
check("no page: no note", page_call_out_note(None, False) == "")

print("\npage_links: the page's own session folder is not a candidate")
note("sitzungen/2026-09-26-zuruf/eigene-liste.md",
     "---\ntags: [ausgaben]\n---\nHier.\n")
note("sitzungen/2026-09-01-alt/finanzplan.md",
     "---\ntags: [ausgaben]\n---\nFrueher.\n")
wz = wiki.WikiLayer(projekt)
alle = wz.page_links("Ausgaben")
eigene = wz.page_links("Ausgaben", session_dir=z_dir)
check("without a session folder both are candidates",
      alle == ["finanzplan", "eigene-liste"]
      or sorted(alle) == ["eigene-liste", "finanzplan"], str(alle))
check("with it: another session's note stays, the own one is gone",
      "finanzplan" in eigene and "eigene-liste" not in eigene, str(eigene))
check("the project folder itself as session (flat layout) excludes nothing",
      sorted(wz.page_links("Ausgaben", session_dir=projekt))
      == sorted(alle))

print("\nlink_suffix / topic_title: the same session-folder exclusion")


def _or_error(fn, *a, **kw):
    """The result, or the exception's type name -- a missing keyword on
    the old code must read as a failed check, not end the file."""
    try:
        return fn(*a, **kw)
    except Exception as e:                # noqa: BLE001
        return type(e).__name__


s_alle = wz.link_suffix("Ausgaben", room=200)
s_eigene = _or_error(wz.link_suffix, "Ausgaben", room=200, session_dir=z_dir)
check("link_suffix without a session folder: both are candidates",
      "[[eigene-liste]]" in s_alle[0] and "[[finanzplan]]" in s_alle[0],
      str(s_alle))
check("link_suffix with it: the own session's note is gone, another "
      "session's stays -- as in page_links",
      s_eigene == (" -> [[finanzplan]]", 1), str(s_eigene))
check("link_suffix: the project folder itself (flat layout) excludes nothing",
      _or_error(wz.link_suffix, "Ausgaben", room=200, session_dir=projekt)
      == s_alle)
check("topic_title passes the session folder on",
      _or_error(wiki.topic_title, "Ausgaben", "", wz, session_dir=z_dir)
      == ("Ausgaben -> [[finanzplan]]", 1))
# The consumer: a topic block's links leave out the notes of the session
# the protocol lives in, like the page that block may get.
s_w = consumer.StageWriter(z_dir, StageState(), log=lambda *a: None,
                           proposals=z_dir / "vorschlag-suffix.jsonl",
                           events=_events.EventLog(z_dir / "events-suffix.jsonl"))
s_w.protocol = consumer.Protocol(z_prot, tmp)
attach_wiki(s_w, wiki.WikiLayer(projekt))
with (z_dir / "vorschlag-suffix.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"action": "topic", "title": "Ausgaben",
                         "markdown": "## Ausgaben"}) + "\n")
s_w.ingest()
z_text = z_prot.read_text("utf-8").split("## Rohtranskript")[0]
check("consumer: the block title skips the own session's note",
      re.search(r"^### \d\d:\d\d Ausgaben -> \[\[finanzplan\]\]"
                r"(?: \*\([^)]*\)\*)?$", z_text, re.M) is not None,
      z_text[-300:])
check("a failing create reports a token (events.is_token)",
      "is_token(name)" in Path(wiki.__file__).read_text(encoding="utf-8"))


# ======================================================= end-to-end (MM-G)
# Card MM-G, task 5 (acceptance): one fresh session in the SAME synthetic
# project, run through the real pieces in meeting order -- a topic action,
# then the Zuruf -- with `scripts/wiki-lint.py` counting before and after.
# The project already carries everything the sections above left in it
# (other sessions, other drafts); the lint assertions are therefore about
# DELTAS, which is exactly what one session changes.
print("\nend-to-end: topic -> links, Zuruf -> page + backlink, lint deltas")
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "wiki_lint", Path(__file__).resolve().parents[1] / "scripts" / "wiki-lint.py")
wiki_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wiki_lint)

e_dir = projekt / "sitzungen" / "2026-09-27-ende-zu-ende"
e_dir.mkdir(parents=True)
e_prot = e_dir / "protokoll.md"
e_prot.write_text(consumer.protocol_header(**_HEAD, mode="sitzung"),
                  encoding="utf-8")
e_st = StageState()
e_w = consumer.StageWriter(e_dir, e_st, log=lambda *a: None,
                           proposals=e_dir / "vorschlag.jsonl",
                           events=_events.EventLog(e_dir / "events.jsonl"))
e_w.protocol = consumer.Protocol(e_prot, tmp)
attach_wiki(e_w, wiki.WikiLayer(projekt))
lint_0 = wiki_lint.lint_project(projekt)

# 1. The topic action: the protocol's block title carries the links.
with (e_dir / "vorschlag.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"action": "topic", "title": "Ernte und Pflege",
                         "markdown": "## Ernte und Pflege"}) + "\n")
e_w.ingest()
e_text = e_prot.read_text("utf-8").split("## Rohtranskript")[0]
check("e2e topic: ### HH:MM Ernte und Pflege -> [[ernte]], [[pflege]]",
      re.search(r"^### \d\d:\d\d Ernte und Pflege -> \[\[ernte\]\], "
                r"\[\[pflege\]\]$", e_text, re.M) is not None, e_text[-300:])
lint_1 = wiki_lint.lint_project(projekt)
# The lint's definition (scripts/wiki-lint.py docstring): a session HAS a
# backlink only when a project file outside every session names it,
# `[[<session-id>]]` -- the debrief's line. The block title's links point
# OUT of the session; they are not that line.
check("e2e lint: the session is counted, and the block title's links are "
      "not its backlink",
      lint_1["sessions"] == lint_0["sessions"]
      and lint_1["sessions_without_backlink"]
      == lint_0["sessions_without_backlink"], f"{lint_0} -> {lint_1}")
check("e2e lint: the links are live -- no dead, no ambiguous link added",
      lint_1["dead_links"] == lint_0["dead_links"]
      and lint_1["ambiguous_links"] == lint_0["ambiguous_links"],
      f"{lint_0} -> {lint_1}")

# 2. The Zuruf, without level 2: skeleton + backlink by the consumer.
e_seite = _page_call_out(
    e_w, None, blk("Robo, leg dafuer eine Seite an", True),
    session_dir=e_dir, project_dir=projekt, log=zlog, clock=lambda: JETZT)
e_page = e_dir / "entwuerfe" / "ernte-und-pflege.md"
e_page_text = e_page.read_text("utf-8") if e_page.exists() else ""
e_verwandt = re.search(r"^Verwandt: (.*)$", e_page_text, re.M)
e_links = re.findall(r"\[\[([^\]]+)\]\]", e_verwandt.group(1)) if e_verwandt else []
check("e2e Zuruf: the page in <session>/entwuerfe/, named after the block",
      e_seite == e_page and e_page.exists(), str(e_seite))
check("e2e Zuruf: type topic, the session protocol as source",
      "type: topic\n" in e_page_text
      and "sources: [sitzungen/2026-09-27-ende-zu-ende/protokoll.md]"
      in e_page_text, e_page_text)
check("e2e Zuruf: >= 2 links, each an existing unique project note",
      len(e_links) >= 2
      and all(e_w.knowledge_link._wiki._edges()[0].resolve(s) is not None for s in e_links),
      str(e_links))
e_text = e_prot.read_text("utf-8").split("## Rohtranskript")[0]
check("e2e Zuruf: the backlink sits in the CURRENT topic block",
      re.search(r"^### \d\d:\d\d Ernte und Pflege -> .*\n(?:- .*\n)*"
                r"- \d\d:\d\d \[notiz\] Themenseite \[\[ernte-und-pflege\]\] "
                r"\*\(bestätigt 14:07\)\*$", e_text, re.M) is not None,
      e_text[-400:])
lint_2 = wiki_lint.lint_project(projekt)
check("e2e lint: one page more, and it adds 0 to pages_under_2_links",
      lint_2["pages"] == lint_1["pages"] + 1
      and lint_2["pages_under_2_links"] == lint_1["pages_under_2_links"],
      f"{lint_1} -> {lint_2}")
check("e2e lint: the backlink [[ernte-und-pflege]] is live, nothing "
      "else moved but the page itself",
      lint_2["dead_links"] == lint_1["dead_links"]
      and lint_2["ambiguous_links"] == lint_1["ambiguous_links"]
      and lint_2["notes"] == lint_1["notes"] + 1
      and lint_2["sessions"] == lint_1["sessions"]
      and lint_2["sessions_without_backlink"]
      == lint_1["sessions_without_backlink"], f"{lint_1} -> {lint_2}")

# (StageWriter.open_protocol_topic does not exist in this repo: its four
# archive checks are left out.)

# 3. The debrief (docs/after-meeting-check.md section E): a decision goes
# into the journal with `[[<session-id>]]` -- now, and only now, the
# session has its backlink.
journal = projekt / "status" / "entscheidungsjournal.md"
journal.parent.mkdir(parents=True, exist_ok=True)
journal.write_text("- Beschluss: Ernte gemeinsam [[2026-09-27-ende-zu-ende]]\n",
                   encoding="utf-8")
lint_3 = wiki_lint.lint_project(projekt)
check("e2e lint: the debrief's [[<session-id>]] line is the backlink -- "
      "exactly one session fewer without, the link is live",
      lint_3["sessions_without_backlink"]
      == lint_2["sessions_without_backlink"] - 1
      and lint_3["dead_links"] == lint_2["dead_links"],
      f"{lint_2} -> {lint_3}")


@pytest.mark.parametrize("name,ok,info", _C.params())
def test_wiki_check(name, ok, info):
    assert ok, f"{name}: {info}"
