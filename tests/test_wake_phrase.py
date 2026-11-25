# CoThinker — acceptance table for the "Hey Robo" wake phrase (issue #150)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""GitHub issue #150's acceptance table, run against the real seam
(`gateway.segmenter.BlockBuilder._is_wake`) and against `strip_wake()`'s
address-stripping plus a mutation check on the greeting requirement.

    python3 -m pytest -q tests/test_wake_phrase.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.segmenter import BlockBuilder  # noqa: E402
from shared.wake_word import END_PHRASE_RE, WAKE_NOT, strip_wake  # noqa: E402


def _end_fires(text: str) -> bool:
    m = END_PHRASE_RE.match(text)
    if not m:
        return False
    name = next((v for v in m.groupdict().values() if v is not None), None)
    return not (name and WAKE_NOT.match(name))


def make_bb(wake_word: str = "hey robo") -> BlockBuilder:
    return BlockBuilder(output=lambda b: None, wake_word=wake_word)


# ------------------------------------------------------- acceptance table
FIRES = [
    "Hey Robo, fass zusammen",
    "Hey, Robo. Was ist offen?",
    "hej robo strukturiere das",
    "He Robo-erzeuge eine Seite",
    "Hey Bobo, nächster Punkt",
]

DOES_NOT_FIRE = [
    "Robo, fass zusammen",
    "Robo hat vorhin gesagt …",
    "ein Roboter",
    "Hey Robert",
    "Wobei ich finde …",
    "hey, wo bist du",
]


def test_fires_on_acceptance_transcripts():
    bb = make_bb()
    for text in FIRES:
        assert bb._is_wake(text), f"expected wake phrase to fire on: {text!r}"


def test_does_not_fire_on_acceptance_transcripts():
    bb = make_bb()
    for text in DOES_NOT_FIRE:
        assert not bb._is_wake(text), \
            f"expected wake phrase NOT to fire on: {text!r}"


# ----------------------------------------------- mutation check (greeting)
def test_mutation_bare_name_without_greeting_never_fires():
    """If the greeting-required gate is ever dropped (e.g. the robo-variant
    alternation becomes reachable on its own again), these must start
    firing -- that is exactly the regression this test exists to catch."""
    bb = make_bb()
    for text in ("Robo, fass zusammen", "Bobo, nächster Punkt",
                 "Hobo, naechster Punkt", "Grobo, mach weiter"):
        assert not bb._is_wake(text), \
            f"a bare name without a greeting must not fire: {text!r}"


# --------------------------------------------------------------- bare wake
def test_bare_wake_phrase_alone_is_heard_not_an_order():
    bb = make_bb()
    assert bb._is_bare_wake("Hey Robo.")
    assert bb._is_bare_wake("Hey Robo")


def test_bare_wake_requires_the_whole_phrase():
    bb = make_bb()
    assert not bb._is_bare_wake("Robo.")


# --------------------------------------------------------- segmenter wiring
def test_welded_order_opens_a_call_without_the_phrase_text_lost():
    """Issue #180: the wake word opens a call window instead of cutting
    the block immediately -- the order's text still survives, closed here
    by the call-silence timeout."""
    seen = []
    clock = [0.0]
    bb = BlockBuilder(output=seen.append, wake_word="hey robo", pause_s=2.5,
                      now=lambda: clock[0])
    bb.sentence("Hey Robo, fass zusammen")
    clock[0] += 8.0
    bb.tick()
    assert len(seen) == 1
    block = seen[0]
    assert block.wake_word is True
    assert block.reason == "silence"
    assert block.text == "Hey Robo, fass zusammen"


def test_default_wake_word_is_hey_robo():
    bb = BlockBuilder(output=lambda b: None)
    assert bb._is_wake("Hey Robo, fass zusammen")
    assert not bb._is_wake("Robo, fass zusammen")


# ------------------------------------------------------------ strip_wake()
def test_strip_wake_drops_the_whole_phrase():
    assert strip_wake("Hey Robo, fass zusammen") == "fass zusammen"


def test_strip_wake_keeps_text_without_a_leading_phrase():
    assert strip_wake("fass zusammen") == "fass zusammen"


def test_strip_wake_keeps_a_false_friend():
    assert strip_wake("Hey Robert, mach das") == "Hey Robert, mach das"


# ------------------------------------------------------------ END_PHRASE_RE
END_FIRES = ["Robo, Ende", "Robo Ende", "Danke, Robo", "Danke Robo",
             "Bobo, Ende", "Robo-Ende"]
END_DOES_NOT_FIRE = ["Roboter, Ende", "Hey Roboter, Ende", "Ende der Diskussion",
                     "Danke, Robert", "Danke schoen"]


def test_end_phrase_fires():
    for text in END_FIRES:
        assert _end_fires(text), f"expected end phrase to fire on: {text!r}"


def test_end_phrase_does_not_fire_on_false_friends():
    for text in END_DOES_NOT_FIRE:
        assert not _end_fires(text), \
            f"expected end phrase NOT to fire on: {text!r}"
