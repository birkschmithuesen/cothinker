# CoThinker — K2 talk share: per-role share of spoken words (blocks/speaking_share/talk_share.py)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_talk_share.py

Each test below corresponds to one check from the archive's plain-Python
test script; the mutant it would catch is noted in its docstring."""
from __future__ import annotations

from blocks.speaking_share.talk_share import (
    ANONYMOUS,
    DEFAULT_THROTTLE_S,
    DEFAULT_WINDOW,
    TalkShareTracker,
    build_event,
)


def test_speaker_label_is_mapped_to_its_person_not_used_raw():
    """Mutant: the mapping step is bypassed and the raw speaker label is
    used as the person field instead of looking it up."""
    t = TalkShareTracker({"S1": "P1", "S2": "P2"})
    t.add_final("S1", "eins zwei drei")
    t.add_final("S2", "vier fuenf")
    ev = t.event()
    by_person = {row["person"]: row["words"] for row in ev["shares"]}
    assert by_person.get("P1") == 3 and by_person.get("P2") == 2
    assert "S1" not in by_person and "S2" not in by_person


def test_add_final_first_increment_lands():
    t2 = TalkShareTracker({"S1": "P1"})
    t2.add_final("S1", "eins zwei")
    assert t2.event()["shares"][0]["words"] == 2


def test_add_final_second_increment_accumulates_not_reset_or_doubled():
    """Mutant: add_final re-derives state from scratch (e.g. resets the
    running count) instead of adding only the newly arrived words -- a
    second call would lose or double the first call's words."""
    t2 = TalkShareTracker({"S1": "P1"})
    t2.add_final("S1", "eins zwei")
    t2.add_final("S1", "drei vier fuenf")
    assert t2.event()["shares"][0]["words"] == 5


def test_build_event_is_pure_identical_snapshots_give_identical_events():
    snap = {"P1": 3, "P2": 1}
    ev_a = build_event(snap, ["P1", "P2"])
    ev_b = build_event(dict(snap), ["P1", "P2"])
    assert ev_a == ev_b


def test_total_words_sums_every_persons_words():
    t = TalkShareTracker({"S1": "P1", "S2": "P2"})
    t.add_final("S1", "eins zwei drei")
    t.add_final("S2", "vier fuenf")
    assert t.event()["total_words"] == 5


def test_share_values_sum_to_one_when_total_words_positive():
    t = TalkShareTracker({"S1": "P1", "S2": "P2"})
    t.add_final("S1", "eins zwei drei")
    t.add_final("S2", "vier fuenf")
    ev = t.event()
    assert abs(sum(row["share"] for row in ev["shares"]) - 1.0) < 1e-9


def test_window_is_the_literal_interview_label():
    t = TalkShareTracker({"S1": "P1", "S2": "P2"})
    t.add_final("S1", "eins zwei drei")
    t.add_final("S2", "vier fuenf")
    assert t.event()["window"] == "interview"


def test_default_window_constant_is_interview():
    assert DEFAULT_WINDOW == "interview"


def test_type_field_names_the_event():
    t = TalkShareTracker({"S1": "P1", "S2": "P2"})
    t.add_final("S1", "eins zwei drei")
    t.add_final("S2", "vier fuenf")
    assert t.event()["type"] == "talk_share"


def test_shares_is_empty_when_total_words_is_zero():
    empty = TalkShareTracker({"S1": "P1"})
    assert empty.event()["shares"] == [] and empty.event()["total_words"] == 0


def test_known_person_with_zero_words_still_included():
    zero_word = TalkShareTracker({"S1": "P1", "S2": "P2"})
    zero_word.add_final("S1", "ein wort hier")
    ev_zero = zero_word.event()
    by_person_zero = {row["person"]: row for row in ev_zero["shares"]}
    assert by_person_zero.get("P2") == {"person": "P2", "role_id": None, "words": 0, "share": 0.0}


def test_unmapped_speaker_label_counted_under_its_own_anonymous_row():
    anon = TalkShareTracker({"S1": "P1"})
    anon.add_final("S1", "a b")
    anon.add_final("S9-unmapped", "c d e")
    by_person_anon = {row["person"]: row["words"] for row in anon.event()["shares"]}
    assert by_person_anon.get(ANONYMOUS) == 3


def test_anonymous_is_the_literal_person_name():
    assert ANONYMOUS == "Anonymous"


def test_role_id_comes_from_role_of_mapping_per_person():
    role_t = TalkShareTracker({"S1": "P1"}, role_of={"P1": "R3"})
    role_t.add_final("S1", "hallo")
    role_row = role_t.event()["shares"][0]
    assert role_row["role_id"] == "R3"


def test_askers_own_words_are_counted_too_not_excluded():
    """Mutant: porting the asker-exclusion filter from
    workshop.bullets.BulletsHook.on_stt in here, dropping one speaker's
    words from the count."""
    both = TalkShareTracker({"ASKER": "P1", "INTERVIEWEE": "P2"})
    both.add_final("ASKER", "frage frage frage")
    both.add_final("INTERVIEWEE", "antwort antwort")
    ev_both = both.event()
    by_person_both = {row["person"]: row["words"] for row in ev_both["shares"]}
    assert by_person_both.get("P1") == 3


def test_total_includes_both_speakers_words_no_phase_filter():
    both = TalkShareTracker({"ASKER": "P1", "INTERVIEWEE": "P2"})
    both.add_final("ASKER", "frage frage frage")
    both.add_final("INTERVIEWEE", "antwort antwort")
    assert both.event()["total_words"] == 5


def test_default_throttle_constant_is_3s():
    """The operator 2026-10-30 20:40: bars moved too slowly at 10s."""
    assert DEFAULT_THROTTLE_S == 3.0


def test_first_call_at_t100_emits():
    th = TalkShareTracker({"S1": "P1"})
    th.add_final("S1", "a")
    assert th.maybe_emit(100.0) is not None


def test_second_call_2s_later_under_3s_default_is_suppressed():
    """Mutant: should_emit always returns True (the throttle check is
    removed) -- a second call right after the first would also emit."""
    th = TalkShareTracker({"S1": "P1"})
    th.add_final("S1", "a")
    th.maybe_emit(100.0)
    assert th.maybe_emit(102.0) is None


def test_call_4s_after_the_first_emits_again():
    th = TalkShareTracker({"S1": "P1"})
    th.add_final("S1", "a")
    th.maybe_emit(100.0)
    th.maybe_emit(102.0)
    assert th.maybe_emit(104.0) is not None


def test_custom_throttle_s_emits_on_the_first_call():
    """Mutant: the throttle_s constructor argument is ignored and the
    default is hardcoded instead."""
    fast = TalkShareTracker({"S1": "P1"}, throttle_s=1.0)
    fast.add_final("S1", "a")
    assert fast.maybe_emit(0.0) is not None


def test_custom_throttle_s_emits_again_after_1point5s():
    fast = TalkShareTracker({"S1": "P1"}, throttle_s=1.0)
    fast.add_final("S1", "a")
    fast.maybe_emit(0.0)
    assert fast.maybe_emit(1.5) is not None


def test_given_name_carried_in_its_own_field_next_to_role_id():
    """Mutant: given_of is ignored, so the talk-share panel keeps showing
    the role title alone instead of given + role."""
    given_t = TalkShareTracker({"S1": "P1"}, role_of={"P1": "Metalwork apprentice"},
                                given_of={"P1": "Suzan"})
    given_t.add_final("S1", "hallo")
    given_row = given_t.event()["shares"][0]
    assert given_row == {"person": "P1", "role_id": "Metalwork apprentice", "given": "Suzan",
                          "words": 1, "share": 1.0}


def test_person_with_no_given_of_mapping_has_no_given_key_at_all():
    """Mutant: a person with no given_of entry gets a "given" key anyway
    (e.g. given=None), breaking the "optional, defaults to absent" field
    contract build_event's other fields already rely on."""
    no_given_t = TalkShareTracker({"S1": "P1"}, role_of={"P1": "Metalwork apprentice"})
    no_given_t.add_final("S1", "hallo")
    no_given_row = no_given_t.event()["shares"][0]
    assert "given" not in no_given_row


def test_build_event_given_of_is_optional_and_defaults_to_no_given_keys():
    assert build_event({"P1": 2}, ["P1"], {"P1": "R1"}) == {
        "type": "talk_share",
        "shares": [{"person": "P1", "role_id": "R1", "words": 2, "share": 1.0}],
        "total_words": 2, "window": "interview"}


def test_t3s_person_as_first_words_emit():
    """Live finding 2026-10-31, Tisch 01: an empty tick must not use up
    the throttle slot, or a person's first words can sit unseen for up to
    a full throttle_s window."""
    fw = TalkShareTracker({"S1": "P1", "S2": "P2"})
    fw.add_final("S1", "hallo")
    assert fw.maybe_emit(3.0) is not None


def test_t5s_more_words_from_same_announced_person_do_not_emit():
    fw = TalkShareTracker({"S1": "P1", "S2": "P2"})
    fw.add_final("S1", "hallo")
    fw.maybe_emit(3.0)
    fw.add_final("S1", "weiter")
    assert fw.maybe_emit(5.0) is None


def test_t6s_second_persons_first_words_emit():
    """3s since last emit, at/above the 2s FIRST_WORDS_MIN_S gate for an
    unannounced person."""
    fw = TalkShareTracker({"S1": "P1", "S2": "P2"})
    fw.add_final("S1", "hallo")
    fw.maybe_emit(3.0)
    fw.add_final("S1", "weiter")
    fw.maybe_emit(5.0)
    fw.add_final("S2", "hi")
    assert fw.maybe_emit(6.0) is not None


def test_t17s_person_a_speaks_again_and_emits():
    fw = TalkShareTracker({"S1": "P1", "S2": "P2"})
    fw.add_final("S1", "hallo")
    fw.maybe_emit(3.0)
    fw.add_final("S1", "weiter")
    fw.maybe_emit(5.0)
    fw.add_final("S2", "hi")
    fw.maybe_emit(6.0)
    fw.add_final("S1", "nochmal")
    assert fw.maybe_emit(17.0) is not None


def test_empty_snapshot_never_emits_even_on_the_first_call():
    empty_tick = TalkShareTracker({"S1": "P1"})
    assert empty_tick.maybe_emit(0.0) is None


def test_empty_snapshot_never_consumes_the_slot():
    empty_tick = TalkShareTracker({"S1": "P1"})
    empty_tick.maybe_emit(0.0)
    empty_tick.add_final("S1", "erstes wort")
    assert empty_tick.maybe_emit(0.0) is not None
