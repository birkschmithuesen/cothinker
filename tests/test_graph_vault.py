# CoThinker — tests of the vault scan and the note parser
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The scan is the one place that reads the vault.

Measured on the real vault (architect, 2026-09-15): `Path.rglob('*.md')`
and `os.walk(followlinks=False)` find 4 notes, `os.walk(followlinks=True)`
finds 869. Five directory symlinks carry the whole vault, and a scan that
misses them reports success on an empty graph.

    python3 -m pytest -q tests/test_graph_vault.py
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from check_collector import Checks  # noqa: E402

_C = Checks()
check = _C.check
from graph.vault_notes import NoteIndex, scan_vault  # noqa: E402

# realpath, not just mkdtemp: TMPDIR itself may be a symlink (e.g. a
# `.hermes` symlink), and the cycle test below must behave the same way
# regardless of whether the path to the temp dir already contains one.
tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="cothinker-vault-")))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)


def note(p: Path, text: str = "# x\n") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# A vault in the shape of the real one: a stem with one real note, and
# the body of the collection behind a directory symlink.
wurzel = tmp / "vault"
aussen = tmp / "outside" / "entities"
note(wurzel / "Home.md")
for i in range(5):
    note(aussen / f"note-{i}.md")
note(aussen / "entity-x" / "network" / "person-a.md")
(wurzel / "30-Entities").symlink_to(aussen, target_is_directory=True)

print("the scan follows the symlinks")
index = scan_vault(wurzel)
check("the scan follows the symlink", len(index.notes) == 6,
      f"{len(index.notes)}: {sorted(index.notes)}")
check("rglob alone would have missed it",
      len([p for p in wurzel.rglob('*.md')]) == 1,
      "rglob suddenly follows symlinks -- re-measure before trusting it")

# A second symlink into the SAME tree, plus one that points at an ancestor:
# today the five real symlinks are disjoint (measured: 869 raw, 869 unique
# realpaths, 0 reached twice), but a later one could build a cycle. The
# dedupe costs nothing and makes the scan independent of that.
(wurzel / "31-Nochmal").symlink_to(aussen, target_is_directory=True)
(aussen / "zurueck").symlink_to(wurzel, target_is_directory=True)


class _ScanTimeout(Exception):
    """Not OSError: os.walk catches OSError internally (e.g. for a
    permission error on one subdirectory) and just skips that branch,
    which would silently swallow a TimeoutError-based alarm instead of
    interrupting the walk.
    """


def _timeout(signum, frame):
    raise _ScanTimeout("scan_vault did not return within 20s -- directory cycle not caught")


signal.signal(signal.SIGALRM, _timeout)
signal.alarm(20)
try:
    begin = time.monotonic()
    index2 = scan_vault(wurzel)
    dauer = time.monotonic() - begin
except _ScanTimeout as exc:
    fehler = str(exc)
    index2 = NoteIndex([])
    dauer = None
finally:
    signal.alarm(0)
# Break the cycle now that it has been exercised: the old scan_vault
# does eventually finish even without a timeout (ELOOP bounds it), just
# far too slowly -- leaving the cycle in place would poison every later
# scan_vault(wurzel) call below with that same slowness.
(aussen / "zurueck").unlink()
check("the scan with the cycle returned instead of timing out",
      dauer is not None, fehler if dauer is None else "")
check("a cycle does not hang the scan and adds nothing",
      dauer is not None and len(index2.notes) == 6,
      fehler if dauer is None else f"{len(index2.notes)}")
check("the cycle scan finishes in under 5 s",
      dauer is not None and dauer < 5,
      fehler if dauer is None else f"{dauer:.1f}s")
check("with the cycle broken the scan still finds the same notes",
      len(scan_vault(wurzel).notes) == 6)
check("the same file is listed once, by realpath",
      sorted(n.stem for n in index2.notes.values())
      == ["Home", "note-0", "note-1", "note-2", "note-3", "note-4"],
      str(sorted(index2.notes)))

print("the privacy exclusion")
check("network notes are excluded by default",
      not any("/network/" in rel for rel in index2.notes), str(sorted(index2.notes)))
offen = scan_vault(wurzel, exclude=())
check("and the switch lets them in",
      any("/network/" in rel for rel in offen.notes), str(sorted(offen.notes)))

print("wikilinks")
from graph.vault_notes import extract_links                      # noqa: E402

check("a plain wikilink is found",
      extract_links("siehe [[ars-electronica-2026]] dazu")
      == ["ars-electronica-2026"], str(extract_links("siehe [[a]]")))
check("alias and anchor are stripped",
      extract_links("[[projekt#Ziel|das Projekt]] und [[a^b2c]]")
      == ["projekt", "a"],
      str(extract_links("[[projekt#Ziel|das Projekt]] und [[a^b2c]]")))
# Measured (M3): `schild-01.jpg` 14x and `ansicht-01.jpg` 14x are among
# the five most frequent "unresolved links" -- they are image embeds, not
# note references, and a naive reading turns them into exactly the
# file-less nodes the operator forbade on 2026-09-14.
check("an image embed is not a link",
      extract_links("![[schild-01.jpg]] [[echte-notiz]]") == ["echte-notiz"],
      str(extract_links("![[schild-01.jpg]] [[echte-notiz]]")))
check("a path prefix is reduced to the file name",
      extract_links("[[../NewBauhaus]] [[ordner/tief/x.md]]")
      == ["NewBauhaus", "x"],
      str(extract_links("[[../NewBauhaus]] [[ordner/tief/x.md]]")))
# Every OCCURRENCE, not every target: M3 counts 2590 occurrences against
# 828 distinct targets, and the ranking in the ring selection weighs a
# note that is linked three times higher than one linked once.
check("every occurrence is counted, not deduplicated",
      extract_links("[[a]] [[a]] [[b]]") == ["a", "a", "b"],
      str(extract_links("[[a]] [[a]] [[b]]")))

note(wurzel / "verlinkt.md", "Text mit [[note-0]] und ![[bild.png]]\n")
index3 = scan_vault(wurzel)
check("the scan fills the links of a note",
      index3.notes["verlinkt.md"].links == ("note-0",),
      str(index3.notes["verlinkt.md"].links))

print("tags, from the frontmatter and from the text")
from graph.vault_notes import extract_tags                       # noqa: E402

FM_LISTE = "---\ntags: [projekt-x, conf-demo]\ntitel: x\n---\n\nText\n"
FM_SKALAR = "---\ntags: kontakt\n---\n\nText\n"
FM_BLOCK = "---\ntags:\n  - leader\n  - festival\n---\n\nText\n"
check("frontmatter inline list", extract_tags(FM_LISTE)
      == ["projekt-x", "conf-demo"], str(extract_tags(FM_LISTE)))
check("frontmatter scalar", extract_tags(FM_SKALAR) == ["kontakt"],
      str(extract_tags(FM_SKALAR)))
check("frontmatter block list", extract_tags(FM_BLOCK)
      == ["leader", "festival"], str(extract_tags(FM_BLOCK)))
check("an inline hash tag counts", extract_tags("Dazu #newbauhaus mehr")
      == ["newbauhaus"], str(extract_tags("Dazu #newbauhaus mehr")))
check("a markdown heading is not a tag", extract_tags("# Ueberschrift\n") == [],
      str(extract_tags("# Ueberschrift\n")))
check("a hash inside a word is not a tag", extract_tags("Sprache C# ist alt")
      == [], str(extract_tags("Sprache C# ist alt")))

print("resolution: no node without a file (the operator, 2026-09-14)")
zwei = tmp / "zwei"
note(zwei / "a" / "protokoll.md", "x")
note(zwei / "b" / "protokoll.md", "y")
note(zwei / "eindeutig.md", "[[protokoll]] [[protokoll]] [[gibtsnicht]]\n")
ix = scan_vault(zwei)

check("a unique stem resolves to its note",
      getattr(ix.resolve("eindeutig"), "rel", None) == "eindeutig.md",
      str(ix.resolve("eindeutig")))
check("resolution ignores case", ix.resolve("EINDEUTIG") is not None,
      "a differently cased link found nothing")
# Measured (M3): `protokoll` occurs 46 times and there are two or more
# files for it. A reference to a name that is handed out more than once
# has NO unique target -- it must not invent one.
check("an ambiguous name resolves to nothing", ix.resolve("protokoll") is None,
      str(ix.resolve("protokoll")))
check("and it is counted for the report", ix.ambiguous.get("protokoll") == 2,
      str(ix.ambiguous))
check("an unresolvable name resolves to nothing and is counted",
      ix.resolve("gibtsnicht") is None and ix.unresolved.get("gibtsnicht") == 1,
      f"{ix.resolve('gibtsnicht')} / {ix.unresolved}")
check("the report carries both counters and the note count",
      ix.report() == {"notes": 3, "unresolved": 1, "ambiguous": 2,
                      "tags": 0}, str(ix.report()))


@pytest.mark.parametrize("name,ok,info", _C.params())
def test_graph_vault_check(name, ok, info):
    assert ok, f"{name}: {info}"
