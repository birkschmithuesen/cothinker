#!/usr/bin/env python3
# CoThinker - the seed note of a graph surface
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The seed of the graph is a FILE, always (operator, 2026-09-14).

The first draft had a virtual node for a topic without a note. The operator:
"Warum nicht bei jedem neuen Projekt einfach neue Datei anlegen und dann
anzeigen?" -- a node without a file is a special case that runs through
display, history, export and click target, the session produces knowledge
that needs a place anyway, and a note can be opened in Obsidian during
the meeting while a virtual node is only pixels.

THE ONLY WRITE this whole feature performs on the vault. It happens in
the consumer's own session folder, and the consumer is that folder's only
writer (AGENTS.md) -- which is precisely why no other process may do it.

The note's text is GERMAN: it lands in the vault, which the operator reads.
"""
from __future__ import annotations

from pathlib import Path


def find_or_create_seed(index, titel: str, *, session_dir, vault_root,
                        file_name: str, datum: str, teilnehmende: str,
                        protokoll: str = "protokoll.md",
                        raw_name: str | None = None) -> tuple[str, str]:
    """`(rel, "found" | "created" | "ambiguous")` for the seed note.

    Ambiguous means: the title matches MORE than one existing note.
    Creating one then would duplicate, picking one would invent -- so it
    does neither and lets the caller refuse the surface.

    I2 (reviewer, decided by Robo in the operator's
    stead 2026-09-15, Variante A): `index.by_stem` keys are the vault's
    own file stems, casefolded but otherwise untouched
    (graph/vault_notes.py) -- real stems carry underscores, long titles,
    numbers ("01_attractor", "0004-fork-patch-...-streichen"). `file_name`
    used to be the only candidate searched here, and it arrives already
    `slugify()`'d from the caller -- slugify both substitutes characters
    (`_` -> `-`, measured on 117 of 655 real stems, 17.9 %) and CUTS at
    MAX_SLUG_CHARS. Neither operation is undone by `by_stem`, so the
    slugified name simply is not a key there 17.9 % of the time, and a
    slug that also isn't any OTHER stem finds nothing at all (116/655,
    17.7 %, measured). `raw_name` is the pre-slugify candidate (the seed
    or title exactly as the agent said it) -- searched FIRST, because it
    is what the vault's own stems actually look like. `file_name` (the
    slug) is tried second, for the rarer case where the slug happens to
    equal a real stem, and is what a NEW note is written to either way:
    a created file's name must stay a safe path component, which is
    exactly what slugify is for -- this function does not touch that.
    """
    for kandidat in dict.fromkeys(
            [k for k in (raw_name, file_name) if k]):
        treffer = index.by_stem.get(kandidat.casefold(), [])
        if len(treffer) == 1:
            return treffer[0].rel, "found"
        if len(treffer) > 1:
            return "", "ambiguous"

    ziel = Path(session_dir) / f"{file_name}.md"
    # The vault-relative path is worked out BEFORE anything is written:
    # on the real vault every session folder hangs off a symlink whose
    # target lies outside the resolved vault root, so this raises every
    # time. Writing the note first would leave a stub file behind on
    # every rejected proposal, once per retry.
    rel = Path(ziel).resolve().relative_to(
        Path(vault_root).resolve()).as_posix()
    if not ziel.exists():
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(
            "---\n"
            f"datum: {datum}\n"
            f"teilnehmende: {teilnehmende}\n"
            "tags: [cothinker, sitzung]\n"
            "---\n\n"
            f"# {titel}\n\n"
            f"Sitzungsnotiz, angelegt von CoThinker am {datum}.\n"
            f"Mitschrift: [Protokoll]({protokoll})\n",
            encoding="utf-8")
    return rel, "created"
