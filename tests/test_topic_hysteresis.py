# CoThinker — tests of the topic-change hysteresis
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Topic-change hysteresis — simulated clock, run in milliseconds.

    python3 -m pytest -q tests/test_topic_hysteresis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blocks.topics.hysteresis import TopicHysteresis  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def _fresh(**kw):
    clock = Clock()
    return clock, TopicHysteresis(now=clock, **kw)


# a single block >= threshold, but topic too young: not confirmed
def test_strong_signal_alone_too_young():
    clock, h = _fresh()
    clock.advance(10)
    d = h.on_result(0.95, None)
    assert d.changed is False


# a single block >= threshold AND old enough: confirmed (FINAL rule, no
# consecutive-block requirement)
def test_confirmed_on_the_very_first_qualifying_block():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.70, None)
    assert d.changed is True


def test_confirmed_as_a_new_topic_not_a_return():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.70, None)
    assert d.returned is False


def test_coach_becomes_eligible_on_a_genuine_new_topic():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.70, None)
    assert d.coach_eligible is True


def test_topic_age_resets_to_0_on_confirmation():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.70, None)
    assert d.topic_age_s == 0.0


# just below threshold never confirms, however long it runs
def test_just_under_0_70_never_confirms():
    clock, h = _fresh()
    clock.advance(1000)
    d = h.on_result(0.69, None)
    assert d.changed is False


# rueckkehr_thema reactivates without a coach contribution
def test_return_is_a_topic_change():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(None, 0.70)
    assert d.changed is True


def test_return_is_marked_as_a_return():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(None, 0.70)
    assert d.returned is True


def test_a_return_never_makes_the_coach_eligible():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(None, 0.70)
    assert d.coach_eligible is False


# rueckkehr_thema is checked only after the main rule -- it is not a veto: a
# block with BOTH signals high confirms as a NEW topic, never as a return
def test_the_main_rule_wins_when_both_fire_on_the_same_block():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.9, 0.9)
    assert d.changed is True


def test_it_is_a_new_topic_not_a_return():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.9, 0.9)
    assert d.returned is False


def test_coach_becomes_eligible():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(0.9, 0.9)
    assert d.coach_eligible is True


# rueckkehr_thema never needs the 90s hold -- a return fires immediately even
# on a very young topic
def test_a_return_is_not_gated_by_topic_age():
    clock, h = _fresh()
    clock.advance(1)
    d = h.on_result(None, 0.70)
    assert d.changed is True and d.returned


# a return resets the age clock for the reactivated topic
def test_age_counts_from_the_return():
    clock, h = _fresh()
    clock.advance(95)
    h.on_result(None, 0.8)
    clock.advance(10)
    d = h.on_result(None, None)
    assert abs(d.topic_age_s - 10.0) < 0.001


# a missing answer (None) never confirms
def test_a_gap_is_not_evidence_of_a_topic_change():
    clock, h = _fresh()
    clock.advance(95)
    d = h.on_result(None, None)
    assert d.changed is False


# the threshold and the min topic age are both configurable
def test_custom_threshold_age_too_young_still_blocks():
    clock, h = _fresh(threshold=0.5, min_topic_age_s=5)
    clock.advance(4)
    d1 = h.on_result(0.6, None)
    assert d1.changed is False


def test_custom_threshold_age_confirms_once_both_custom_conditions_hold():
    clock, h = _fresh(threshold=0.5, min_topic_age_s=5)
    clock.advance(4)
    h.on_result(0.6, None)
    clock.advance(2)
    d2 = h.on_result(0.6, None)
    assert d2.changed is True
