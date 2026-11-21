"""Tests for shared.language_guard (language detection for the condense block)."""
from __future__ import annotations

from shared.language_guard import detect_language, matches_language

GERMAN = (
    "Wir haben heute beschlossen, dass das Projekt nicht mehr im alten Format "
    "weitergeführt wird, weil die Gruppe das für zu kompliziert hält."
)
ENGLISH = (
    "We decided today that the project will not be continued in the old format, "
    "because the group thinks that it is too complicated for everyone."
)


def test_detects_german():
    assert detect_language(GERMAN) == "de"


def test_detects_english():
    assert detect_language(ENGLISH) == "en"


def test_mixed_text_dominant_language_wins():
    mixed = GERMAN + " Das ist wirklich ein guter Plan und wir sind sehr zufrieden damit. " \
        "Der Roadmap review is on Friday."
    assert detect_language(mixed) == "de"
    english_heavy = ENGLISH + " Und das war der Plan."
    assert detect_language(english_heavy) == "en"


def test_short_or_empty_text_is_undecidable():
    assert detect_language("") is None
    assert detect_language("   ") is None
    assert detect_language("ok") is None
    assert detect_language("12345 !!!") is None


def test_matches_language():
    assert matches_language(GERMAN, "de")
    assert not matches_language(GERMAN, "en")
    assert matches_language(ENGLISH, "en")
    assert not matches_language(ENGLISH, "de")


def test_matches_language_true_when_undecidable():
    assert matches_language("", "de")
    assert matches_language("ok", "en")
    # unsupported session language: nothing to check against
    assert matches_language(GERMAN, "fr")
