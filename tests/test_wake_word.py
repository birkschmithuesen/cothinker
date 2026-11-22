# CoThinker — tests of call-lane intent detection (wake word helpers)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_wake_word.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.wake_word import call_ack, call_intent  # noqa: E402


def test_structure_intent() -> None:
    assert call_intent("Robo, strukturiere das Meeting") == "structure"


def test_summarize_intent() -> None:
    assert call_intent("Robo, fass zusammen") == "summarize"


def test_question_intent() -> None:
    assert call_intent("Robo, was ist offen?") == "question"


def test_research_intent() -> None:
    assert call_intent("Robo, recherchiere das bitte") == "research"


def test_generic_intent_falls_back() -> None:
    assert call_intent("Robo, mach weiter") == "generic"


def test_ack_template_matches_intent() -> None:
    assert call_ack("Robo, fass zusammen") == "Robo: Zusammenfassung läuft …"


def test_question_beaten_by_structure_keyword() -> None:
    assert call_intent("Robo, kannst du das strukturieren?") == "structure"
