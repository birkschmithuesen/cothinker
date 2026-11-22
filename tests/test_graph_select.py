# CoThinker — tests of the ring selection and its hysteresis
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Rings, the cap, and the grace list.

Measured (M5): ring 1 alone is 97-227 nodes on the real vault. Under any
usable GLOBAL cap the visible picture is seed + ring 1 and ring 2 is never
reached -- "grows in rings" would be a label without an effect. A budget
PER RING (25/20/15) fills all three: 61 nodes, 3-6 edges per node.

The source here is a stand-in, not the vault: everything downstream of
`neighbours(note)` must work for an edge source it has never seen --
that is the seam embeddings and Hindsight are meant to plug into.

    python3 -m pytest -q tests/test_graph_select.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from check_collector import Checks  # noqa: E402

_C = Checks()
check = _C.check
from graph.neighbours import Neighbour                            # noqa: E402
from graph.ring_select import (MAX_NODES, RING_BUDGETS,           # noqa: E402
                               select_nodes)


@dataclass(frozen=True)
class FakeNote:
    rel: str
    mtime: float = 0.0


class FakeSource:
    """An edge source the selection has never seen. That is the point."""

    def __init__(self, kanten: dict[str, list[tuple[str, str, float]]]):
        self.kanten = kanten
        self.notes = {rel: FakeNote(rel) for rel in kanten}

    def neighbours(self, note):
        return [Neighbour(self.notes[rel], art, gewicht)
                for rel, art, gewicht in self.kanten.get(note.rel, [])]


def stern(ring1: int, je_ring2: int, je_ring3: int = 3) -> FakeSource:
    """A seed with a huge ring 1 -- the measured shape of the real vault.

    Three levels deep, so a budget per ring has a third ring to fill.
    """
    kanten: dict[str, list[tuple[str, str, float]]] = {"saat": []}
    for i in range(ring1):
        a = f"r1-{i}"
        kanten["saat"].append((a, "wikilink", 1.0))
        kanten.setdefault(a, []).append(("saat", "wikilink", 1.0))
        for j in range(je_ring2):
            b = f"r2-{i}-{j}"
            kanten[a].append((b, "tag", 0.4))
            kanten.setdefault(b, []).append((a, "tag", 0.4))
            for m in range(je_ring3):
                c = f"r3-{i}-{j}-{m}"
                kanten[b].append((c, "tag", 0.4))
                kanten.setdefault(c, []).append((b, "tag", 0.4))
    return FakeSource(kanten)


print("rings, budgets and the hard cap")
quelle = stern(120, 4)
saat = quelle.notes["saat"]
auswahl = select_nodes(saat, quelle)
ringe = {}
for rel, ring in auswahl.items():
    ringe[ring] = ringe.get(ring, 0) + 1

check("the seed is in, and it is ring 0",
      auswahl.get("saat") == 0, str(auswahl.get("saat")))
check("every ring keeps its budget",
      all(ringe.get(i + 1, 0) <= b for i, b in enumerate(RING_BUDGETS)),
      str(ringe))
# The finding this exists for: with a global cap, ring 2 stays empty.
check("ring 2 is populated although ring 1 overflows",
      ringe.get(2, 0) == RING_BUDGETS[1], str(ringe))
check("the hard node cap is never exceeded",
      len(auswahl) <= MAX_NODES and len(auswahl) == 1 + sum(RING_BUDGETS),
      f"{len(auswahl)} nodes")

# Closeness before novelty: inside one ring a wikilink neighbour outranks
# a tag-only one, and at equal weight the newer note wins.
gemischt = FakeSource({"s": [("w", "wikilink", 1.0), ("t", "tag", 0.4)],
                       "w": [("s", "wikilink", 1.0)],
                       "t": [("s", "tag", 0.4)]})
eng = select_nodes(gemischt.notes["s"], gemischt, budgets=(1,))
check("inside a ring the stronger edge wins", set(eng) == {"s", "w"},
      str(sorted(eng)))

print("hysteresis: a node does not fall out the moment it loses a slot")
from graph.ring_select import GRACE_ROUNDS, Hysteresis            # noqa: E402

h = Hysteresis()
alle = {"a", "b", "c", "neu"}
check("the first round is just the selection",
      h.apply({"a", "b"}, alle) == {"a", "b"}, "grace invented a node")
# THE rule. Without it the graph twitches on every update.
check("a node that lost its slot is still shown",
      h.apply({"b"}, alle) == {"a", "b"}, "a took its leave immediately")
# Additive, never a swap: a brand new node must appear the moment it is
# chosen, even while somebody else is under grace.
check("grace never displaces a newly chosen node",
      h.apply({"b", "neu"}, alle) == {"a", "b", "neu"}, "the new one was blocked")
h2 = Hysteresis()
h2.apply({"a", "b"}, alle)
for _ in range(GRACE_ROUNDS):
    letzte = h2.apply({"b"}, alle)
check("the grace period lapses on its own", h2.apply({"b"}, alle) == {"b"},
      f"still holding after {GRACE_ROUNDS} rounds: {letzte}")

print("the payload")
import atexit                                                     # noqa: E402
import json                                                       # noqa: E402
import shutil                                                     # noqa: E402
import tempfile                                                   # noqa: E402
from graph.graph_build import GraphSource, write_graph_json       # noqa: E402

vt = Path(tempfile.mkdtemp(prefix="cothinker-build-"))
atexit.register(shutil.rmtree, vt, ignore_errors=True)


def bau(rel: str, text: str) -> None:
    p = vt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


bau("saat.md", "---\ntags: [thema]\n---\n[[ziel]] [[gibtsnicht]]\n")
bau("ziel.md", "---\ntags: [thema]\n---\nnichts\n")
bau("fremd.md", "---\ntags: [anderes]\n---\nnichts\n")
# Only the shared tag connects this one -- `saat.md` and `ziel.md` are
# ALSO wikilinked, and `HardEdges` keeps the stronger kind per note, so
# without a tag-only neighbour the payload could never show both kinds.
bau("nachbar.md", "---\ntags: [thema]\n---\nnichts\n")

quelle2 = GraphSource(vt)
nutzlast = quelle2.build("saat.md")
knoten = {n["id"] for n in nutzlast["nodes"]}
check("the payload carries seed, nodes, edges and counts",
      nutzlast["seed"] == "saat.md"
      and knoten == {"saat.md", "ziel.md", "nachbar.md"}
      and nutzlast["counts"]["notes"] == 4,
      json.dumps(nutzlast)[:300])
check("no node without a file on disk",
      all((vt / n["id"]).is_file() for n in nutzlast["nodes"]),
      str(sorted(knoten)))
check("both edge kinds are distinguishable in the payload",
      {e["kind"] for e in nutzlast["edges"]} == {"wikilink", "tag"},
      str(nutzlast["edges"]))
check("edges only run between visible nodes",
      all(e["source"] in knoten and e["target"] in knoten
          for e in nutzlast["edges"]), str(nutzlast["edges"]))

ziel_datei = vt / "out" / "graph.json"
write_graph_json(nutzlast, ziel_datei)
check("graph.json is written and reads back as the same payload",
      json.loads(ziel_datei.read_text(encoding="utf-8")) == nutzlast,
      "written file differs")

print("the seed note is always a real file")
from graph.seed_note import find_or_create_seed                   # noqa: E402
from graph.vault_notes import scan_vault as scan2                 # noqa: E402

sitz = vt / "30-Entities" / "projekt" / "sessions" / "2026-09-15-graph"
sitz.mkdir(parents=True, exist_ok=True)
(sitz / "protokoll.md").write_text("# Protokoll\n", encoding="utf-8")
bau("30-Entities/projekt/vorhandenes-thema.md", "# Vorhanden\n")
ix2 = scan2(vt)

rel, lage = find_or_create_seed(
    ix2, "Vorhandenes Thema", session_dir=sitz, vault_root=vt,
    file_name="vorhandenes-thema", datum="2026-09-15",
    teilnehmende="the operator, Robo")
check("an existing note is used, not duplicated",
      lage == "found" and rel == "30-Entities/projekt/vorhandenes-thema.md",
      f"{lage} {rel}")

# I2 (reviewer): a real stem with an underscore.
# `slugify()` turns it into "01-attractor" (measured pattern: `_` -> `-`,
# 117 of 655 real vault stems, 17.9 %) -- a lookup with ONLY the slug
# never finds this note. `raw_name` (the pre-slugify candidate, i.e. the
# seed/title exactly as the agent said it) is searched first and matches
# the stem as written.
bau("30-Entities/projekt/01_attractor.md", "# Attractor\n")
ix2b = scan2(vt)
rel_us, lage_us = find_or_create_seed(
    ix2b, "01_attractor", session_dir=sitz, vault_root=vt,
    file_name="01-attractor", raw_name="01_attractor",
    datum="2026-09-15", teilnehmende="the operator, Robo")
check("a stem slugify() would rewrite (underscore -> dash) is still "
      "found via the raw candidate (I2)",
      lage_us == "found" and rel_us == "30-Entities/projekt/01_attractor.md",
      f"{lage_us} {rel_us}")
# Without `raw_name` (the pre-fix call shape) the slugified name alone
# does not match the real stem -- a fresh, WRONG note would be created.
rel_slug_only, lage_slug_only = find_or_create_seed(
    ix2b, "01_attractor", session_dir=sitz, vault_root=vt,
    file_name="01-attractor", datum="2026-09-15", teilnehmende="the operator, Robo")
check("the slug alone (no raw_name) would have missed it -- the bug I2 "
      "describes, reproduced",
      lage_slug_only == "created", f"{lage_slug_only} {rel_slug_only}")
if lage_slug_only == "created":
    (vt / rel_slug_only).unlink()

rel2, lage2 = find_or_create_seed(
    ix2, "Graph-Schirm bauen", session_dir=sitz, vault_root=vt,
    file_name="graph-schirm-bauen", datum="2026-09-15",
    teilnehmende="the operator, Robo")
neu = vt / rel2
text = neu.read_text(encoding="utf-8")
check("a missing note is created in the session folder with frontmatter",
      lage2 == "created" and neu.is_file()
      and text.startswith("---\n") and "datum: 2026-09-15" in text
      and "teilnehmende: the operator, Robo" in text, f"{lage2}: {text[:200]}")
# `[[protokoll]]` is ambiguous 46 times over in the real vault (M3) and
# would produce no edge at all. A relative link is unambiguous.
check("the protocol is referenced relatively, not as an ambiguous wikilink",
      "(protokoll.md)" in text and "[[protokoll]]" not in text, text)
check("the created note shows up in a rescan as a node",
      scan2(vt).notes.get(rel2) is not None, rel2)

import os                                                          # noqa: E402

# The relative path has to be worked out BEFORE the file is written:
# on the real vault every session folder hangs off a symlink whose
# target lies outside the resolved root, so this raises every time --
# and a note written first would be left behind on every rejected
# proposal, once per attempt.
extern_ziel = Path(tempfile.mkdtemp(prefix="cothinker-outside-"))
atexit.register(shutil.rmtree, extern_ziel, ignore_errors=True)
sitz_verlinkt = vt / "30-Entities" / "projekt" / "sessions" / "verlinkte-sitzung"
sitz_verlinkt.parent.mkdir(parents=True, exist_ok=True)
os.symlink(extern_ziel, sitz_verlinkt)

fehler = None
try:
    find_or_create_seed(
        ix2, "Ein Ganz Neues Thema", session_dir=sitz_verlinkt,
        vault_root=vt, file_name="ein-ganz-neues-thema",
        datum="2026-09-15", teilnehmende="the operator, Robo")
except ValueError:
    fehler = "ValueError"
check("a session folder symlinked outside the vault root still refuses, "
      "but leaves no stub note behind",
      fehler == "ValueError" and os.listdir(extern_ziel) == [],
      f"{fehler} {os.listdir(extern_ziel)}")

bau("30-Entities/x/doppelt.md", "# d\n")
bau("30-Entities/y/doppelt.md", "# d\n")
ix3 = scan2(vt)
rel3, lage3 = find_or_create_seed(
    ix3, "Doppelt", session_dir=sitz, vault_root=vt, file_name="doppelt",
    datum="2026-09-15", teilnehmende="the operator")
check("an ambiguous title creates nothing and says so",
      lage3 == "ambiguous" and rel3 == "" and not (sitz / "doppelt.md").exists(),
      f"{lage3} {rel3}")

print("the candidate order is bound: raw_name before file_name (R1 class)")
# R1 (widened widened again ): a stem
# 'raw_name' whose slug collides with an UNRELATED, already existing
# stem. raw_name must win -- picking file_name (the slug) first would
# silently return the wrong note, no error, no "ambiguous".
#
# Review: two fixtures are not a class. A
# mutant (M10) that reorders candidates only when raw_name is ASCII
# AND carries no underscore survived BOTH prior fixtures -- the emoji
# stem is non-ASCII, Panel_View has an underscore, neither enters the
# slug-first branch M10 flips. Both axes are covered below.
#
# Review: all four cases above share a
# property none of them varies -- every raw_name carries an uppercase
# letter, so all four trigger slugify() step T3 (lower()) and hold it
# CONSTANT. Four mutants at the candidate loop (graph/seed_note.py:56)
# survived on that gap: reverse the candidate list unless raw is
# lower-cased (M16), unless raw has a digit (M17), unless raw has a
# '-' (M18), unless raw[0] is lower-case (M19) -- 29 ok, 0 failed each,
# M16 alone wrong on 99 of 117 real R1 stems. The slugify() steps
# (gateway/consumer.py:309) a case can vary or must hold constant:
#   T1 UMLAUTS-Ersetzung        ae/oe/ue/ss
#   T2 NFKD + combining strip   Akzente
#   T3 lower()                  Grossbuchstaben
#   T4 [^a-z0-9]+ -> '-'        '_', ' ', '.', Emoji, alles andere
#   T5 strip('-')               fuehrende/schliessende Trennzeichen
#   T6 Kuerzung > MAX_SLUG_CHARS (48)
#   T7 fallback                 leeres Ergebnis
# Covered below: T3 (now varied -- both new cases are raw already
# lower-case), T4, T5, T6. NOT covered, left open on purpose: T1, T2
# (the real vault has no colliding umlaut/accent stem -- a mutant on
# either axis flips 0 of 117 real stems) and T7 (fallback only fires
# on an empty slug, which is not an R1 shape at all). If a stem with a
# real umlaut/accent collision shows up in the vault, add a case here.
#
# Measured on the real vault (/home/<user>/vault, scan_vault + slugify,
# imported, MAX_SLUG_CHARS=48, DEFAULT_EXCLUDE=('*/network/*',)):
#   notes=720  unique stems=675 (not 657 -- digit transposition in an
#   earlier count)  by_stem keys=675
#   [raw.casefold() != slug.casefold()] (R1 class, CASEFOLD basis):
#     abweichend=117  ASCII-ohne-'_'=28  Kuerzung=26  Rest=2
#   [raw != slug] (LITERAL basis, a stricter/different count -- do not
#   compare the two numbers above against this row):
#     abweichend=140  ASCII-ohne-'_'=51  Kuerzung=26  Rest=25
# The "Rest=2" above (not 1) is two distinct real stems, both ASCII,
# no '_', not from the length cut: 'Coding notes' (a bare space) and
# 'protokoll-2026-09-07.abgeloest' (a '.' folded to '-') -- 26 + 1 + 1
# = 28.
#
# Every case satisfies all three R1 conditions: raw.casefold() !=
# slug.casefold(), BOTH are by_stem keys, and they point at TWO
# DIFFERENT notes -- so a wrong pick is observable, not equivalent.
# Each case gets its own subfolder (by index) so the cumulative vault
# built by bau()/scan2() never lets two cases' stems collide with each
# other or with the FX_editor/stabil fixtures below (those test R2 and
# the order-doesn't-matter control, not the raw/slug collision). Stem
# names are chosen fresh -- '01_attractor' etc. already occur earlier
# in this file (by_stem is keyed across the whole cumulative vault, not
# per-subfolder) and would make the fixture fail unmutated as
# "ambiguous" instead of measuring anything.
R1_CASES = [
    dict(
        achse="nicht-ASCII",
        origin=", real vault: '🏠 Rosa' vs the unrelated 'rosa'",
        raw_name="🏠 Rosa",
        file_name="rosa",  # slugify('🏠 Rosa') == 'rosa', measured
    ),
    dict(
        achse="ASCII mit Unterstrich",
        origin="review -- M-non-ASCII-only survives without this",
        raw_name="Panel_View",
        file_name="panel-view",  # slugify('Panel_View') == 'panel-view'
    ),
    dict(
        achse="ASCII mit slugify-KUERZUNG (MAX_SLUG_CHARS=48)",
        origin="-- M10 survives without this, 26/28 real cases",
        raw_name="Graph Schirm Fuer Die Sitzungsbegleitung Mit Nachbarn Und Ringen",
        file_name="graph-schirm-fuer-die-sitzungsbegleitung-mit",  # measured
    ),
    dict(
        achse="ASCII mit Leerzeichen",
        origin="-- M10 survives without this, 1/28 real cases",
        raw_name="Deck Plan",
        file_name="deck-plan",  # slugify('Deck Plan') == 'deck-plan'
    ),
    dict(
        achse="klein geschrieben (T3 konstant halten aufgehoben)",
        origin="review -- M16/M17 survive without this, "
               "99/117 bzw. 73/117 real R1-Staemme falsch",
        raw_name="07_resonator",
        file_name="07-resonator",  # slugify('07_resonator'), measured
    ),
    dict(
        achse="klein, mit '-', ueber MAX_SLUG_CHARS (T3+T4+T6 zusammen)",
        origin="review -- M18/M19 survive the fifth case alone",
        raw_name="ae26-lange-liste-mit-vielen-attributen-und-verweisen-"
                  "auf-andere-dinge",
        # slugify(raw) == 'ae26-lange-liste-mit-vielen-attributen-und',
        # measured: 42 chars after the >48-char cut on a word boundary.
        file_name="ae26-lange-liste-mit-vielen-attributen-und",
    ),
]

for _i, _fall in enumerate(R1_CASES):
    _raw, _slug = _fall["raw_name"], _fall["file_name"]
    _richtig = f"30-Entities/r1/{_i}/richtig/{_raw}.md"
    _falsch = f"30-Entities/r1/{_i}/falsch/{_slug}.md"
    bau(_richtig, f"# {_fall['achse']}, die richtige Notiz\n")
    bau(_falsch, f"# {_fall['achse']}, die andere Notiz\n")
    _ix = scan2(vt)
    _rel, _lage = find_or_create_seed(
        _ix, _raw, session_dir=sitz, vault_root=vt,
        file_name=_slug, raw_name=_raw,
        datum="2026-09-15", teilnehmende="the operator, Robo")
    check(f"R1 [{_fall['achse']}]: raw_name wins over the slug "
          f"({_fall['origin']})",
          _lage == "found" and _rel == _richtig,
          f"{_lage} {_rel}")
# What this loop cannot see: it cannot tell WHY raw_name won -- a mutant
# that always tries slug first except for these exact stem names would
# also pass. It only proves the order is not conditioned on the six
# character-set axes named above. It also cannot see a THIRD candidate
# order (e.g. alphabetical) that happens to put every raw_name before
# its slug by coincidence -- that residual is accepted, not claimed
# closed.

# Positive control: when raw_name and file_name are the SAME candidate
# (a slug-stable, already-lowercase stem), `dict.fromkeys` collapses them
# to one entry and the order cannot matter. This proves the checks above
# need the raw/slug split to see anything -- a fixture that doesn't
# split them would pass even with the order reversed.
bau("30-Entities/user/stabil.md", "# Stabil\n")
ix_stabil = scan2(vt)
rel_stabil, lage_stabil = find_or_create_seed(
    ix_stabil, "stabil", session_dir=sitz, vault_root=vt,
    file_name="stabil", raw_name="stabil",
    datum="2026-09-15", teilnehmende="the operator, Robo")
check("control: a slug-stable lowercase stem is found no matter the "
      "order (raw_name == file_name here, so this fixture cannot see R1)",
      lage_stabil == "found" and rel_stabil == "30-Entities/user/stabil.md",
      f"{lage_stabil} {rel_stabil}")

print("casefold() on the candidate lookup is bound (R2)")
# Measured on the real vault: raw_name arrives from the
# agent exactly as typed, uppercase and all. slugify() lowercases AND
# swaps '_' for '-', so an uppercase, underscored stem needs casefold()
# on the raw candidate to be found at all -- without it neither raw_name
# nor file_name matches the (casefolded) index key.
bau("30-Entities/user/FX_editor.md", "# FX editor\n")
ix_fx = scan2(vt)
rel_fx, lage_fx = find_or_create_seed(
    ix_fx, "FX_editor", session_dir=sitz, vault_root=vt,
    file_name="fx-editor", raw_name="FX_editor",
    datum="2026-09-15", teilnehmende="the operator, Robo")
check("an uppercase raw seed (as the agent typed it) still finds the "
      "existing stem via casefold() on the lookup",
      lage_fx == "found" and rel_fx == "30-Entities/user/FX_editor.md",
      f"{lage_fx} {rel_fx}")

# Positive control: an already-lowercase stem is found with or without
# casefold() on the lookup -- proving the check above needs the case
# mismatch to see anything.
bau("30-Entities/user/fx-lower.md", "# fx lower\n")
ix_fx_lower = scan2(vt)
rel_fxl, lage_fxl = find_or_create_seed(
    ix_fx_lower, "fx-lower", session_dir=sitz, vault_root=vt,
    file_name="fx-lower", raw_name="fx-lower",
    datum="2026-09-15", teilnehmende="the operator, Robo")
check("control: an already-lowercase stem is found regardless of "
      "casefold() (this fixture cannot see R2)",
      lage_fxl == "found" and rel_fxl == "30-Entities/user/fx-lower.md",
      f"{lage_fxl} {rel_fxl}")


@pytest.mark.parametrize("name,ok,info", _C.params())
def test_graph_select_check(name, ok, info):
    assert ok, f"{name}: {info}"
