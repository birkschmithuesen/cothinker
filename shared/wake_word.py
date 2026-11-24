#!/usr/bin/env python3
# CoThinker — shared wake-word helpers (stdlib only)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Wake-word helpers shared by the segmenter and the policy: which words
only look like the wake word (`WAKE_NOT`), the two-word wake PHRASE
matcher (`wake_phrase_pattern`, `WAKE_PHRASE_RE`, `strip_wake`), and the
rule-based intent and acknowledgement of a call (`call_intent`,
`call_ack`).

Pure Python, no I/O, no model call.
"""
from __future__ import annotations

import re

# German words that start like the wake word but are NOT one. Without this
# list, `\brobo[a-zäöüß]*` turns every sentence about robots into an order --
# and robots are a topic in these rounds. Subtracted AFTER the prefix match,
# so "Roboter" alone stays harmless while "Roboterzeuge" (recognition welded
# "Robo, erzeuge" into one word) still fires.
WAKE_NOT = re.compile(r"^(robert|roboti\w*|roboter|robotern|roboters)$",
                      re.IGNORECASE)

# Leading quotes/brackets stripped before checking whether a clause STARTS
# with the wake phrase -- a clause may open with a quotation mark without
# that counting against the "phrase must start a clause" rule.
CLAUSE_LEAD = re.compile(r"^[\s\"'„“‚‘»«(\[]*")

# A single bare word ("Robo") fired on every sentence ABOUT the assistant
# ("Robo hat vorhin gesagt ...") and the STT server mishears a single word
# far more often than a two-word phrase (measured live, GitHub issue #150:
# "Bobo", "Hobo", "Grobo", "Wo, wo", "Wobei"). So the wake word is a
# two-word PHRASE by default: a greeting, then the name -- "Hey Robo".
#
# Greeting spellings the STT server produces for "hey" (live incidents,
# issue #150). Deliberately a fixed, small set -- not every German
# greeting, only the ones actually measured.
_GREETING_ALT = r"hey|hej|hei|he|hallo|ey"

# Misheard "Robo" (same measurement). Unlike the old single-word rule,
# these only count right after a greeting -- a bare "Bobo" without one is
# just as ambiguous as a bare "Robo" was, so it stays silent on its own.
_ROBO_VARIANT_ALT = r"robo|bobo|hobo|probo|rubo|grobo"

# The bare name, any of its misheard variants, as a PREFIX -- used by
# `shared/minutes_format.py`'s `content_words()` to drop the wake name
# from a block's summary regardless of whether a greeting preceded it
# (the greeting itself is dropped by PENDING_STOPWORDS already).
ROBO_LIKE = re.compile(rf"^(?:{_ROBO_VARIANT_ALT})", re.IGNORECASE)


def wake_phrase_pattern(tokens: list[str], group: str) -> str:
    """One `wake_word_re` alternative for a single configured phrase.

    Two tokens ("hey robo"): greeting, optional comma/hyphen/space, then
    the name as a PREFIX match (catches a welded order, "Robo, erzeuge"
    heard as "Roboterzeuge") -- the built-in mishearing lists above only
    apply when the configured word IS the default "hey"/"robo", so a
    custom phrase keeps literal matching instead of silently inheriting
    them. Anything else (a single configured word, same as before this
    phrase mechanism existed): matched as a prefix, no greeting needed --
    this is the escape hatch `--wake-word` always had.
    """
    if len(tokens) == 2:
        greeting, name = tokens
        greeting_pat = _GREETING_ALT if greeting.lower() == "hey" \
            else re.escape(greeting)
        name_pat = _ROBO_VARIANT_ALT if name.lower() == "robo" \
            else re.escape(name)
        return (rf"\b(?:{greeting_pat})\b[,\-\s]*"
                rf"(?P<{group}>(?:{name_pat})[a-zäöüß]*)")
    word = " ".join(tokens)
    return rf"\b(?P<{group}>{re.escape(word)}[a-zäöüß]*)"


# The default phrase's matcher, exported for `shared/minutes_format.py` and
# `gateway/call_lane.py` -- same shape `BlockBuilder.__init__` builds for
# the default `wake_word="hey robo"`, kept as one module-level constant so
# the two never drift apart.
WAKE_PHRASE_RE = re.compile(wake_phrase_pattern(["hey", "robo"], "name"),
                            re.IGNORECASE)


def strip_wake(text: str) -> str:
    """The order text after a leading "Hey Robo, ..." -- what the call
    lane should see, not the address.

    A call welded to the phrase in one utterance ("Hey Robo, fass
    zusammen") would otherwise hand the call lane the address along with
    the order. Only strips a match at the very start (clause-start, same
    as `gateway.segmenter.BlockBuilder._is_wake`) -- a mention of the
    phrase anywhere else is content, not the address, and stays.
    """
    stripped = CLAUSE_LEAD.sub("", text)
    m = WAKE_PHRASE_RE.match(stripped)
    if not m or WAKE_NOT.match(m.group("name")):
        return text
    rest = re.sub(r"^[\s,.\-–:;]+", "", stripped[m.end():])
    return rest or text

# Call-lane intent detection (konzept-spuren-architektur.md, "Entscheider
# ohne LLM"): rule-based keywords only, no model call -- the acknowledgement
# has to be on stage in < 2 s, well before any agent could even start. Order
# matters: checked top to bottom, first match wins. "question" is deliberately
# last -- a structuring/summarizing/research request can itself be phrased as
# a question ("kannst du strukturieren?") and should still get the specific
# template.
CALL_INTENTS: tuple[tuple[str, re.Pattern], ...] = (
    ("structure", re.compile(r"strukturier", re.IGNORECASE)),
    ("summarize", re.compile(r"fass\w*\s+.*zusammen|zusammenfass",
                             re.IGNORECASE)),
    ("research", re.compile(r"recherchier", re.IGNORECASE)),
    ("question", re.compile(r"\?|\bwas ist\b|\bwie ist\b|\bwer ist\b"
                            r"|\bwarum\b|\bwieso\b|\bweshalb\b",
                            re.IGNORECASE)),
)

# One quittance per intent, shown on stage before any agent runs. The lane
# worker's real result later replaces this -- see consumer wiring.
CALL_ACK_TEMPLATES: dict[str, str] = {
    "structure": "Robo: Strukturierung läuft …",
    "summarize": "Robo: Zusammenfassung läuft …",
    "research": "Robo: Recherche läuft …",
    "question": "Robo: Antwort kommt …",
    "generic": "Robo: kommt gleich …",
}


def call_intent(text: str) -> str:
    """Which acknowledgement template a call's text asks for.

    Rule-based, no model call -- this has to run in well under the 2 s the
    acceptance test measures for call → acknowledgement.
    """
    for name, pattern in CALL_INTENTS:
        if pattern.search(text):
            return name
    return "generic"


def call_ack(text: str) -> str:
    return CALL_ACK_TEMPLATES[call_intent(text)]
