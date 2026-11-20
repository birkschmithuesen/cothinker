#!/usr/bin/env python3
# CoThinker — correction stage: a rule-based prefilter
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Remove filler words and stutter repeats BY RULE, before any model.

A bench step for now, not a live one: nothing in
the consumer calls this. Pure function, no I/O, no model -- what it
removes is written down in two sets below, and a round can read them.

Its filler set is deliberately NOT the guard's `FILLERS`: the guard's set
decides what the MODEL may drop unseen, and "halt" or "sozusagen" are
words a model must not be allowed to delete on its own judgement.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from correction import FILLERS  # noqa: E402

PREFILTER_FILLERS = frozenset(FILLERS | {"halt", "sozusagen"})
# Words that repeat GRAMMATICALLY in German: "die Leute, die die Kaesten
# bauen", "das, das das Problem ist". The guard rejects a stutter
# removal by the model for the same reason ("das das" is often a misheard
# "dass das"); the prefilter leaves these alone instead.
REPEAT_KEEP = frozenset({"der", "die", "das", "den", "dem", "des", "dass",
                         "sie", "es"})

# Word chars, plus combining marks (U+0300-U+036F): an NFD-encoded umlaut
# is a base letter followed by a combining mark, which plain `\w` does not
# match -- without this the word would split in two and the wrong half
# would be compared/spliced. Matching is done on the ORIGINAL string (never
# a normalised copy of it) so every word that is not removed keeps its
# original bytes; only the matched word text is normalised, for comparison.
_WORD = re.compile(r"(?:\w|[̀-ͯ])+")
_MARK = "\x00"
# A removed word framed by punctuation (or the start of the text) on one
# side and punctuation on the other: keep exactly one mark, chosen by
# `_merge` below, instead of leaving both (a doubled ".." or a stray
# leading "!").
_FRAMED = re.compile(r"(^|[,.!?])\s*" + _MARK + r"\s*([,.!?])")
_REST = re.compile(r"[ \t]*" + _MARK)


def _norm(word: str) -> str:
    """Normalise a single matched word for set membership/equality checks
    only -- the surrounding text is never touched by this."""
    return unicodedata.normalize("NFC", word).casefold()


def _merge(m: "re.Match[str]") -> str:
    """Which mark survives when a removed word sits between two punctuation
    marks (or the start of the text) and a following one: start-of-text
    keeps neither; a preceding comma keeps whichever mark follows (a comma
    stays a comma, a stronger mark wins); a preceding `.!?` always wins."""
    prev, nxt = m.group(1), m.group(2)
    if not prev:
        return ""
    if prev == ",":
        return nxt
    return prev


def _keeps_halt(text: str, m) -> bool:
    """`halt` stays as the imperative: sentence-initial, or before `!`."""
    vorher = text[:m.start()].rstrip()
    if not vorher or vorher[-1] in ".!?":
        return True
    return text[m.end():].lstrip().startswith("!")


def prefilter(text: str) -> tuple[str, dict]:
    """(filtered text, {"fillers": n, "repeats": n}). Never raises."""
    quelltext = str(text or "")
    weg, fuell, wdh, letztes = [], 0, 0, None
    for m in _WORD.finditer(quelltext):
        wort = _norm(m.group(0))
        if wort in PREFILTER_FILLERS and not (
                wort == "halt" and _keeps_halt(quelltext, m)):
            weg.append(m.span())
            fuell += 1
            continue
        if (letztes is not None and wort not in REPEAT_KEEP
                and wort == _norm(letztes.group(0))
                and not quelltext[letztes.end():m.start()].strip()):
            weg.append(m.span())
            wdh += 1
            # Advance to the removed occurrence, not just skip it: a third
            # "ich" in a row must compare against the second (just removed),
            # not the first, or the gap between them looks non-empty.
            letztes = m
            continue
        letztes = m
    if not weg or letztes is None:
        # Nothing to remove, or nothing would be left: the raw line stands.
        return quelltext, {"fillers": 0, "repeats": 0}
    teile, pos = [], 0
    for a, b in weg:
        teile.append(quelltext[pos:a])
        teile.append(_MARK)
        pos = b
    teile.append(quelltext[pos:])
    neu = "".join(teile)
    while True:
        kurz = _FRAMED.sub(_merge, neu, count=1)
        if kurz == neu:
            break
        neu = kurz
    neu = _REST.sub("", neu)
    if weg[0][0] == _WORD.search(quelltext).start():
        # The first word went: no leading space, comma or stray mark, and
        # a capital the utterance had moves on to the new first word --
        # but only when that new first character is actually a letter.
        neu = neu.lstrip(" \t,")
        if neu and neu[0].isalpha() and quelltext[:1].isupper():
            neu = neu[0].upper() + neu[1:]
    return neu, {"fillers": fuell, "repeats": wdh}
