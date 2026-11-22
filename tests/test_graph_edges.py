# CoThinker — tests of the hard edge source
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Two hard edge kinds, and the cap that makes them comparable.

Measured (M4): without a cap the shared-tag edges outnumber the wikilink
edges 31:1 (38436 against 1234) -- one tag is a clique, `#projekt-x`
with 180 notes alone is 16110 edges. A smaller WEIGHT cannot fix a count.

    python3 -m pytest -q tests/test_graph_edges.py
"""
from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from check_collector import Checks  # noqa: E402

_C = Checks()
check = _C.check
from graph.vault_notes import scan_vault                          # noqa: E402
from graph.neighbours import HardEdges, TAG_GROUP_MAX, WEIGHTS    # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cothinker-edges-"))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)


def note(rel: str, text: str) -> None:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


note("saat.md", "[[ziel]] [[gibtsnicht]] [[protokoll]]\n")
note("ziel.md", "---\ntags: [gemeinsam]\n---\nnichts\n")
note("nachbar.md", "---\ntags: [gemeinsam]\n---\nnichts\n")
note("a/protokoll.md", "x")
note("b/protokoll.md", "y")
for i in range(TAG_GROUP_MAX + 1):
    note(f"gross/g{i}.md", "---\ntags: [zugross]\n---\n")
for i in range(TAG_GROUP_MAX):
    note(f"genau/e{i}.md", "---\ntags: [genaudrauf]\n---\n")

index = scan_vault(tmp)
quelle = HardEdges(index)
saat = index.notes["saat.md"]

print("wikilinks")
nachbarn = quelle.neighbours(saat)
check("a resolvable wikilink is a neighbour",
      [(n.note.rel, n.kind, n.weight) for n in nachbarn if n.kind == "wikilink"]
      == [("ziel.md", "wikilink", WEIGHTS["wikilink"])],
      str([(n.note.rel, n.kind) for n in nachbarn]))
check("the edge is undirected -- the target knows the source too",
      any(n.note.rel == "saat.md"
          for n in quelle.neighbours(index.notes["ziel.md"])),
      str([n.note.rel for n in quelle.neighbours(index.notes["ziel.md"])]))
check("an unresolvable wikilink produces no neighbour",
      all(n.note.rel != "gibtsnicht.md" for n in nachbarn),
      "a file-less node got in")
check("an ambiguous wikilink produces no neighbour",
      all(not n.note.rel.endswith("protokoll.md") for n in nachbarn),
      str([n.note.rel for n in nachbarn]))

print("shared tags, and the cap")
tag_nachbarn = [n for n in quelle.neighbours(index.notes["ziel.md"])
                if n.kind == "tag"]
check("a shared tag is a neighbour, and weaker than a link",
      [n.note.rel for n in tag_nachbarn] == ["nachbar.md"]
      and tag_nachbarn[0].weight < WEIGHTS["wikilink"],
      str([(n.note.rel, n.weight) for n in tag_nachbarn]))
check("a tag group over the cap is no edge source at all",
      quelle.neighbours(index.notes["gross/g0.md"]) == [],
      str([n.note.rel for n in quelle.neighbours(index.notes["gross/g0.md"])]))
check("a group exactly at the cap still is",
      len(quelle.neighbours(index.notes["genau/e0.md"])) == TAG_GROUP_MAX - 1,
      str(len(quelle.neighbours(index.notes["genau/e0.md"]))))


@pytest.mark.parametrize("name,ok,info", _C.params())
def test_graph_edges_check(name, ok, info):
    assert ok, f"{name}: {info}"
