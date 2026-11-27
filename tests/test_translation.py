# CoThinker — tests for the translation block (LLM faked, no network)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_translation.py

The scene translation fixtures below are hand-crafted deterministic
fixtures, not a golden recording against the frozen tag's private
`skript_uebersetzung.py` (no access to that source): a small dict-based
fake LLM stands in for a real model so the quote-preservation and
version-storage behaviour stay fully deterministic and offline.
"""
from __future__ import annotations

import re
from pathlib import Path

from blocks.translation import make_block
from blocks.translation.scene import record_version, translate_scene_text, versions_for
from blocks.translation.ui import translate_operator_text
from shared.blocks import BlockContext
from shared.modules import StateStore

GOLDEN = Path(__file__).parent / "golden" / "translation" / "scene_de_en.txt"

WORDS = {"Hallo": "Hello", "Welt": "World", "sie": "she", "sagte": "said",
         "und": "and", "ging": "went"}

SCENE_TEXT_DE = 'Hallo Welt, sie sagte: "Hallo Welt" und ging.'


class DictLlm:
    """Translates whole words through a fixed dict; a quote's placeholder
    token (not a word character on either side) passes through untouched,
    the same as any word the dict does not know."""

    def __init__(self, words):
        self.words = words
        self.calls = []

    def complete(self, system, user, *, timeout_s=60):
        self.calls.append((system, user))
        return re.sub(r"\w+", lambda m: self.words.get(m.group(0), m.group(0)), user)


class FixedLlm:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, system, user, *, timeout_s=60):
        self.calls += 1
        return self.reply


def _ctx(tmp_path, llm, **config):
    return BlockContext(
        instance=None, session_dir=tmp_path, state=StateStore(tmp_path / ".blocks"),
        agent=None, llm=llm, language="de", clock=lambda: 0.0,
        emit=lambda intent: None, artefact=lambda name, text: None)


def test_translate_scene_matches_golden():
    llm = DictLlm(WORDS)
    result = translate_scene_text(SCENE_TEXT_DE, "en", llm, quote_translation=False)
    assert result == GOLDEN.read_text(encoding="utf-8").strip()
    assert len(llm.calls) == 1


def test_quote_translation_rule():
    llm_kept = DictLlm(WORDS)
    kept = translate_scene_text(SCENE_TEXT_DE, "en", llm_kept, quote_translation=False)
    assert '"Hallo Welt"' in kept          # the quote stays as the group wrote it

    llm_translated = DictLlm(WORDS)
    translated = translate_scene_text(SCENE_TEXT_DE, "en", llm_translated, quote_translation=True)
    assert '"Hello World"' in translated   # quote_translation=True also translates quotes
    assert '"Hallo Welt"' not in translated


def test_translation_never_overwrites_manual_text(tmp_path):
    state = StateStore(tmp_path / ".blocks")
    record_version(state, "scene-1", "en", "HAND WRITTEN BY THE GROUP", source="group")

    block = make_block()
    ctx = _ctx(tmp_path, DictLlm(WORDS))
    block.start(ctx, {"quote_translation": False})
    tools = {t.name: t for t in block.tools()}
    result = tools["translate_scene"].fn(
        scene={"id": "scene-1", "text": SCENE_TEXT_DE}, to="en")

    assert result["ok"] is True
    assert result["data"]["translated"]["scene_id"] == "scene-1"

    versions = versions_for(state, "scene-1", "en")
    manual = [v for v in versions if v["source"] == "group"]
    auto = [v for v in versions if v["source"] == "translation"]
    assert manual == [{"rev": 1, "language": "en", "text": "HAND WRITTEN BY THE GROUP",
                       "source": "group"}]
    assert len(auto) == 1
    assert auto[0]["text"] == result["data"]["translated"]["text"]


def test_cache_hit_avoids_second_call(tmp_path):
    state = StateStore(tmp_path / ".blocks")
    llm = FixedLlm("Hello")
    first = translate_operator_text("Hallo", "en", llm, state)
    second = translate_operator_text("Hallo", "en", llm, state)
    assert first == second == "Hello"
    assert llm.calls == 1
