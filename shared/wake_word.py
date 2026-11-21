#!/usr/bin/env python3
# CoThinker — shared wake-word helpers (stdlib only)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Wake-word helpers shared by the segmenter and the policy: which words
only look like the wake word (`WAKE_NOT`) and the rule-based intent and
acknowledgement of a call (`call_intent`, `call_ack`).

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
