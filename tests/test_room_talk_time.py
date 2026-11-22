# CoThinker — feedback 265: Q8 speaking time per person (room-wide feedback round)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_room_talk_time.py

Ports the pure `RoomTalkTime` cases from the archive's plain-Python test
script. The archive script also had a `hub_case()` exercising the
predecessor's `TableHub`/room speak queue, which this repository does not
have (no `workshop/hub.py`, no `workshop/speak_queue.py`); those checks are
not ported."""
from __future__ import annotations

from blocks.speaking_share.room_talk_time import RoomTalkTime


def test_holder_time_runs_while_speaking():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    assert rt.seconds("01:P1", 25.0) == 15.0


def test_same_speaker_reregistered_does_not_reset_the_span():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    assert rt.seconds("01:P1", 25.0) == 15.0


def test_done_closes_the_span():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    assert rt.seconds("01:P1", 99.0) == 20.0


def test_nobody_speaking_nothing_grows():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    rt.on_change("02:P3", None, 40.0)
    assert rt.seconds("02:P3", 99.0) == 10.0


def _present_40():
    return {f"0{t}:P{p}" for t in range(1, 9) for p in range(1, 6)}


def test_40_present():
    assert len(_present_40()) == 40


def test_average_over_all_40_present_not_over_the_2_who_spoke():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    rt.on_change("02:P3", None, 40.0)
    present = _present_40()
    avg = rt.average(present, 99.0)
    assert abs(avg - 30.0 / 40) < 1e-9


def test_silent_person_own_zero_average_0point8():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    rt.on_change("02:P3", None, 40.0)
    present = _present_40()
    pl = rt.payload_for("05:P2", present, 99.0)
    assert pl["own_seconds"] == 0.0 and pl["average_seconds"] == 0.8 and pl["participants"] == 40


def test_a_speaker_who_left_still_counts_in_the_denominator():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    rt.on_change("02:P3", None, 40.0)
    present = _present_40()
    avg = rt.average(present, 99.0)
    gone = present - {"01:P1"}
    assert rt.average(gone, 99.0) == avg


def test_round_trip_through_run_redezeit_json():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    rt.on_change("02:P3", None, 40.0)
    snap = rt.to_json(99.0)
    rt2 = RoomTalkTime.from_json(snap)
    assert rt2.seconds("01:P1", 0) == 20.0 and rt2.seconds("02:P3", 0) == 10.0


def test_broken_file_empty_never_a_crash():
    assert RoomTalkTime.from_json({"seconds": {"x": -1, 3: 2}}).spoken_keys() == set()


def test_reset_clears_everything():
    rt = RoomTalkTime()
    rt.on_change(None, "01:P1", 10.0)
    rt.on_change("01:P1", "01:P1", 20.0)
    rt.on_change("01:P1", "02:P3", 30.0)
    rt.on_change("02:P3", None, 40.0)
    present = _present_40()
    rt.reset()
    assert rt.average(present, 0) == 0.0 and rt.holder is None
