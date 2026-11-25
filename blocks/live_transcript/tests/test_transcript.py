# CoThinker — tests of blocks/live_transcript/transcript.py (LiveTranscript)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure unit tests: no I/O, every timestamp is an explicit float argument
(issue #181).

    python3 -m pytest -q blocks/live_transcript/tests/test_transcript.py
"""
from __future__ import annotations

from blocks.live_transcript import transcript as lt


def test_fresh_state_has_no_recent_and_no_partial():
    t = lt.LiveTranscript()
    assert t.recent == []
    assert t.partial_turn_id is None
    assert t.partial_text == ""


def test_final_with_text_appends_to_recent_oldest_first():
    t = lt.LiveTranscript()
    changed, cleared = t.on_final("Mira", "hallo", "T1", at=10.0)
    assert changed is True
    assert cleared is None
    changed, cleared = t.on_final("Jonas", "servus", "T2", at=12.0)
    assert changed is True
    assert t.recent == [("Mira", 10.0, "hallo"), ("Jonas", 12.0, "servus")]


def test_final_with_empty_text_does_not_change_recent():
    t = lt.LiveTranscript()
    changed, cleared = t.on_final("Mira", "   ", "T1", at=10.0)
    assert changed is False
    assert cleared is None
    assert t.recent == []


def test_recent_caps_at_ten_oldest_dropped_first():
    t = lt.LiveTranscript()
    for i in range(12):
        t.on_final("Mira", f"line {i}", f"T{i}", at=float(i))
    assert len(t.recent) == 10
    assert t.recent[0] == ("Mira", 2.0, "line 2")
    assert t.recent[-1] == ("Mira", 11.0, "line 11")


def test_partial_sets_turn_id_speaker_text_and_started_at():
    t = lt.LiveTranscript()
    emit = t.on_partial("T1", "Mira", "hal", at=5.0, now=5.0)
    assert emit is True
    assert t.partial_turn_id == "T1"
    assert t.partial_speaker == "Mira"
    assert t.partial_text == "hal"
    assert t.partial_started_at == 5.0


def test_partial_updates_for_the_same_turn_keep_the_original_started_at():
    t = lt.LiveTranscript()
    t.on_partial("T1", "Mira", "hal", at=5.0, now=5.0)
    t.on_partial("T1", "Mira", "hallo", at=5.5, now=5.5)
    assert t.partial_started_at == 5.0
    assert t.partial_text == "hallo"


def test_partial_is_throttled_within_the_minimum_gap():
    t = lt.LiveTranscript()
    assert t.on_partial("T1", "Mira", "h", at=0.0, now=0.0) is True
    assert t.on_partial("T1", "Mira", "ha", at=0.05, now=0.05) is False
    # text/started_at still update even when the re-emit is suppressed
    assert t.partial_text == "ha"


def test_partial_emits_again_once_the_minimum_gap_has_passed():
    t = lt.LiveTranscript()
    t.on_partial("T1", "Mira", "h", at=0.0, now=0.0)
    t.on_partial("T1", "Mira", "ha", at=0.05, now=0.05)
    assert t.on_partial("T1", "Mira", "hal", at=0.3, now=0.3) is True


def test_a_new_turn_is_never_throttled_even_right_after_the_last_one():
    t = lt.LiveTranscript()
    t.on_partial("T1", "Mira", "h", at=0.0, now=0.0)
    assert t.on_partial("T2", "Jonas", "s", at=0.01, now=0.01) is True
    assert t.partial_turn_id == "T2"
    assert t.partial_started_at == 0.01


def test_partial_with_no_turn_id_is_ignored():
    t = lt.LiveTranscript()
    assert t.on_partial(None, "Mira", "hal", at=5.0, now=5.0) is False
    assert t.partial_turn_id is None


def test_final_for_the_open_turn_clears_the_partial():
    t = lt.LiveTranscript()
    t.on_partial("T1", "Mira", "hal", at=5.0, now=5.0)
    changed, cleared = t.on_final("Mira", "hallo", "T1", at=5.4)
    assert changed is True
    assert cleared == {"turn_id": "T1", "speaker": "Mira", "started_at": 5.0}
    assert t.partial_turn_id is None
    assert t.partial_text == ""


def test_final_for_a_different_turn_leaves_the_open_partial_untouched():
    t = lt.LiveTranscript()
    t.on_partial("T1", "Mira", "hal", at=5.0, now=5.0)
    changed, cleared = t.on_final("Jonas", "servus", "T2", at=5.4)
    assert changed is True
    assert cleared is None
    assert t.partial_turn_id == "T1"
    assert t.partial_text == "hal"
