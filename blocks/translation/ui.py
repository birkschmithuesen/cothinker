# CoThinker — blocks/translation/ui.py: operator-view text translation cache
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Translates small pieces of operator-view text (dashboard labels, status
lines) into `operator_language` and caches the result, keyed by the exact
source text and target language, so the same label is never sent to the
model twice. The cache lives in the block's own `StateStore`."""
from __future__ import annotations

from shared.llm import LlmPort
from shared.modules import StateStore

CACHE_FILE = "operator-cache.json"
SYSTEM = "Translate the given operator-view text into {to}. Reply with the translation only."


def _cache_key(text: str, to: str) -> str:
    return f"{to}\x00{text}"


def translate_operator_text(text: str, to: str, llm: LlmPort, state: StateStore) -> str:
    cache = state.read(CACHE_FILE)
    key = _cache_key(text, to)
    cached = cache.get(key)
    if cached is not None:
        return cached
    translated = llm.complete(SYSTEM.format(to=to), text)

    def add(data: dict) -> dict:
        data[key] = translated
        return data
    state.update(CACHE_FILE, add)
    return translated
