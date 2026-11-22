# CoThinker - language detection for the condense block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
# language_guard.py: language detection used by the condense block's output check
"""Tiny stopword-based German / English detector. Stdlib only.

Counts common function words of each language; the language with clearly
more hits wins, so mixed text goes to its dominant language. Too little
evidence yields None, and `matches_language` treats None as a pass.
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Minimum stopword hits (both languages together) before we decide.
MIN_HITS = 3

# Winner needs this share of all hits; closer than that is undecidable.
MIN_SHARE = 0.6

# Words that occur in only one of the two languages (shared words such as
# "in", "was", "die", "man", "an" are left out on purpose).
_STOPWORDS = {
    "de": frozenset("""
        der das und ist nicht ein eine einen einem einer eines ich du er sie es wir ihr
        mit von zu den dem des auf für auch sich dass daß wie aber oder wenn dann noch
        nur sind war waren wird werden wurde haben hat hatte kann können muss müssen
        bei nach aus über unter vor durch ohne weil schon sehr mehr hier da dort
        nein ja doch mal wirklich diese dieser dieses heute gut als um im zum zur
        uns euch mich dich mir dir ihm ihn kein keine
    """.split()),
    "en": frozenset("""
        the and is are was were be been being of to for with on at by from that this
        these those it its he she they we you i not but or if then than just only
        have has had do does did would can could should must may might there
        here what which who when where why how all any some no yes very more most
        about into over after before because as our your their my his her them
        us me him today
    """.split()),
}


def detect_language(text: str) -> str | None:
    """Return "de" or "en" for the dominant language, None if undecidable."""
    words = [w.lower() for w in _WORD.findall(text or "")]
    hits = {lang: sum(1 for w in words if w in sw) for lang, sw in _STOPWORDS.items()}
    total = sum(hits.values())
    if total < MIN_HITS:
        return None
    lang = max(hits, key=lambda k: hits[k])
    if hits[lang] / total < MIN_SHARE:
        return None
    return lang


def matches_language(text: str, language: str) -> bool:
    """True if `text` is in `language`; also True when undecidable or unsupported."""
    if language not in _STOPWORDS:
        return True
    detected = detect_language(text)
    return detected is None or detected == language
