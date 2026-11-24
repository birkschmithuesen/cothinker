# CoThinker — tests of blocks/voice_attribution/voice.py (VoiceAttribution)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure unit tests: no I/O, every timestamp is an explicit float argument,
no fake clock needed since the state machine never reads one itself
(issue #164).

    python3 -m pytest -q tests/test_voice_attribution.py
"""
from __future__ import annotations

from blocks.voice_attribution import voice as va


def test_fresh_state_has_nobody_speaking():
    v = va.VoiceAttribution()
    assert v.speaking is None
    assert v.since is None
    assert v.recent == []


def test_first_utterance_sets_speaking_and_since():
    v = va.VoiceAttribution()
    changed = v.on_utterance("Mira", at=10.0)
    assert changed is True
    assert v.speaking == "Mira"
    assert v.since == 10.0


def test_repeated_utterance_from_same_speaker_is_not_a_change():
    v = va.VoiceAttribution()
    v.on_utterance("Mira", at=10.0)
    changed = v.on_utterance("Mira", at=11.0)
    assert changed is False
    assert v.speaking == "Mira" and v.since == 10.0


def test_new_speaker_switches_speaking_and_pushes_previous_onto_recent():
    v = va.VoiceAttribution()
    v.on_utterance("Mira", at=10.0)
    changed = v.on_utterance("Jonas", at=15.0)
    assert changed is True
    assert v.speaking == "Jonas" and v.since == 15.0
    assert v.recent == [("Mira", 15.0)]


def test_recent_dedups_by_name_moving_it_to_the_front():
    v = va.VoiceAttribution()
    v.on_utterance("Mira", at=0.0)
    v.on_utterance("Jonas", at=5.0)
    v.on_utterance("Mira", at=10.0)
    assert v.recent == [("Jonas", 10.0)]


def test_recent_keeps_only_the_last_5_distinct_speakers():
    v = va.VoiceAttribution()
    v.on_utterance("A", at=0.0)
    for i, name in enumerate(["B", "C", "D", "E", "F"], start=1):
        v.on_utterance(name, at=float(i))
    assert [name for name, _ in v.recent] == ["E", "D", "C", "B", "A"]


def test_empty_speaker_label_is_not_an_utterance():
    v = va.VoiceAttribution()
    changed = v.on_utterance(None, at=10.0)
    assert changed is False
    assert v.speaking is None


def test_tick_within_hold_window_does_not_clear_speaking():
    v = va.VoiceAttribution(hold_s=2.0)
    v.on_utterance("Mira", at=10.0)
    changed = v.on_tick(now=11.5)
    assert changed is False
    assert v.speaking == "Mira"


def test_tick_past_hold_window_clears_speaking_and_pushes_recent():
    v = va.VoiceAttribution(hold_s=2.0)
    v.on_utterance("Mira", at=10.0)
    changed = v.on_tick(now=12.5)
    assert changed is True
    assert v.speaking is None and v.since is None
    assert v.recent == [("Mira", 10.0)]


def test_tick_with_nobody_speaking_is_a_noop():
    v = va.VoiceAttribution()
    changed = v.on_tick(now=100.0)
    assert changed is False


def test_tick_after_a_fresh_utterance_resets_the_hold_window():
    v = va.VoiceAttribution(hold_s=2.0)
    v.on_utterance("Mira", at=10.0)
    v.on_utterance("Mira", at=11.9)
    changed = v.on_tick(now=12.5)
    assert changed is False
    assert v.speaking == "Mira"
