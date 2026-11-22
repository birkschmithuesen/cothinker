# CoThinker — tests of blocks/speaker_queue/queue.py (SpeakQueue)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure unit tests for the speaking queue, no I/O: a fake clock stands in for
`time.monotonic`, no real sockets, no real files, no real time.

Each test is independent (no shared, mutated queue across tests): a helper
rebuilds the exact sequence of calls the scenario needs, then the test
makes its one assertion. The helper chain mirrors the original narrative
(request, duplicate request, done, done, done) one step at a time.

    python3 -m pytest -q tests/test_speak_queue.py
"""
from __future__ import annotations

from blocks.speaker_queue import queue as sq


class FakeClock:
    """A clock that only moves when the test tells it to."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, by: float) -> None:
        self.t += by


# --------------------------------------------------------------- request: first speaker

def _after_fresh():
    return sq.SpeakQueue(clock=FakeClock(100.0))


def _after_p1_requests():
    q = _after_fresh()
    q.request("P1")
    return q


def _after_p1_p2_request():
    q = _after_p1_requests()
    q.request("P2")
    return q


def _after_p1_p2_p3_request():
    q = _after_p1_p2_request()
    q.request("P3")
    return q


def _after_duplicate_requests():
    q = _after_p1_p2_p3_request()
    q.request("P1")
    q.request("P2")
    return q


def _after_done_p1():
    q = _after_duplicate_requests()
    q.done("P1")
    return q


def _after_noop_done_p4():
    q = _after_done_p1()
    q.done("P4")
    return q


def _after_done_p2():
    q = _after_noop_done_p4()
    q.done("P2")
    return q


def _after_done_p3():
    q = _after_done_p2()
    q.done("P3")
    return q


def test_fresh_queue_has_nobody_speaking():
    assert _after_fresh().speaking is None


def test_fresh_queue_is_empty():
    assert _after_fresh().queue == []


def test_first_request_becomes_speaker_immediately():
    assert _after_p1_requests().speaking == "P1"


def test_first_request_leaves_queue_empty():
    assert _after_p1_requests().queue == []


def test_event_contract_matches_c2_shape():
    assert _after_p1_requests().event() == {"speaking": "P1", "queue": []}


# --------------------------------------------------------------- request: joins queue

def test_second_request_does_not_preempt_speaker():
    assert _after_p1_p2_request().speaking == "P1"


def test_second_request_queued_behind_nobody():
    assert _after_p1_p2_request().queue == ["P2"]


def test_p2_is_next_position_1():
    assert _after_p1_p2_request().position("P2") == 1


def test_third_request_appended_not_inserted():
    assert _after_p1_p2_p3_request().queue == ["P2", "P3"]


def test_p3_is_2nd_in_line_position_2():
    assert _after_p1_p2_p3_request().position("P3") == 2


def test_speaker_unaffected_by_further_requests():
    assert _after_p1_p2_p3_request().speaking == "P1"


# --------------------------------------------------------------- request: duplicates ignored

def test_current_speaker_requesting_again_is_noop():
    q = _after_duplicate_requests()
    assert q.speaking == "P1" and q.queue == ["P2", "P3"]


def test_seat_already_queued_requesting_again_does_not_duplicate():
    assert _after_duplicate_requests().queue == ["P2", "P3"]


def test_position_of_seat_with_no_pending_request_is_none():
    assert _after_duplicate_requests().position("P1") is None


def test_position_of_seat_that_never_asked_is_none():
    assert _after_duplicate_requests().position("P4") is None


# --------------------------------------------------------------- done: hands off to next in line

def test_done_by_current_speaker_advances_to_head_of_queue():
    assert _after_done_p1().speaking == "P2"


def test_done_removes_new_speaker_from_queue():
    assert _after_done_p1().queue == ["P3"]


def test_new_speakers_position_is_gone():
    assert _after_done_p1().position("P2") is None


def test_next_in_line_is_now_1st():
    assert _after_done_p1().position("P3") == 1


# --------------------------------------------------------------- done: by someone who isn't speaking

def test_done_by_non_speaker_is_noop_on_speaker():
    assert _after_noop_done_p4().speaking == "P2"


def test_done_by_non_speaker_leaves_queue_untouched():
    assert _after_noop_done_p4().queue == ["P3"]


# --------------------------------------------------------------- done: hands off the last seat in the queue

def test_done_hands_off_to_last_seat_in_queue():
    assert _after_done_p2().speaking == "P3"


def test_queue_is_now_empty_after_done():
    assert _after_done_p2().queue == []


# --------------------------------------------------------------- done: empty queue leaves nobody speaking

def test_done_with_empty_queue_leaves_nobody_speaking():
    assert _after_done_p3().speaking is None


def test_queue_stays_empty_after_final_done():
    assert _after_done_p3().queue == []


# --------------------------------------------------------------- takeover

def _takeover_queue():
    q2 = sq.SpeakQueue(clock=FakeClock())
    q2.request("P1")
    q2.request("P2")
    q2.request("P3")
    return q2


def _after_takeover_by_head():
    q2 = _takeover_queue()
    q2.takeover("P2")
    return q2


def test_takeover_by_non_head_is_noop():
    q2 = _takeover_queue()
    result = q2.takeover("P3")
    assert result is None and q2.speaking == "P1" and q2.queue == ["P2", "P3"]


def test_takeover_by_head_ends_current_speakers_contribution():
    assert _after_takeover_by_head().speaking == "P2"


def test_takeover_removes_taking_over_seat_from_queue():
    assert _after_takeover_by_head().queue == ["P3"]


def test_ended_speaker_not_requeued():
    assert _after_takeover_by_head().position("P1") is None


def test_takeover_with_empty_queue_is_noop():
    q3 = sq.SpeakQueue(clock=FakeClock())
    q3.request("P1")
    q3.takeover("P2")
    assert q3.speaking == "P1" and q3.queue == []


# --------------------------------------------------------------- no automatic end on silence

def _no_silence_end_queue():
    clock2 = FakeClock(0.0)
    q4 = sq.SpeakQueue(clock=clock2)
    q4.request("P1")
    clock2.advance(10_000.0)
    return q4


def test_advancing_clock_alone_never_ends_contribution():
    assert _no_silence_end_queue().speaking == "P1"


def test_advancing_clock_alone_never_touches_queue():
    assert _no_silence_end_queue().queue == []


# --------------------------------------------------------------- speaking_since / injectable clock

def _speaking_since_queue_step1():
    clock3 = FakeClock(42.0)
    q5 = sq.SpeakQueue(clock=clock3)
    q5.request("P1")
    return q5, clock3


def _speaking_since_queue_step2():
    q5, clock3 = _speaking_since_queue_step1()
    clock3.advance(5.0)
    q5.request("P2")
    return q5, clock3


def _speaking_since_queue_step3():
    q5, clock3 = _speaking_since_queue_step2()
    clock3.advance(5.0)
    q5.done("P1")
    return q5, clock3


def _speaking_since_queue_step4():
    q5, clock3 = _speaking_since_queue_step3()
    q5.done("P2")
    return q5


def test_speaking_since_reads_injected_clock():
    q5, _ = _speaking_since_queue_step1()
    assert q5.speaking_since == 42.0


def test_speaking_since_unaffected_by_join():
    q5, _ = _speaking_since_queue_step2()
    assert q5.speaking_since == 42.0


def test_speaking_since_updates_on_handoff():
    q5, _ = _speaking_since_queue_step3()
    assert q5.speaking_since == 52.0


def test_speaking_since_cleared_when_nobody_speaks():
    assert _speaking_since_queue_step4().speaking_since is None


# --------------------------------------------------------------- disconnect: speaker leaves, advances like done

def _disconnect_speaker_queue():
    q7 = sq.SpeakQueue(clock=FakeClock())
    q7.request("P1")
    q7.request("P2")
    q7.request("P3")
    q7.disconnect("P1")
    return q7


def test_disconnect_of_speaker_advances_to_head():
    assert _disconnect_speaker_queue().speaking == "P2"


def test_disconnect_removes_new_speaker_from_queue():
    assert _disconnect_speaker_queue().queue == ["P3"]


# --------------------------------------------------------------- disconnect: speaker leaves, empty queue

def _disconnect_only_speaker_queue():
    q8 = sq.SpeakQueue(clock=FakeClock())
    q8.request("P1")
    q8.disconnect("P1")
    return q8


def test_disconnect_of_only_speaker_leaves_nobody_speaking():
    assert _disconnect_only_speaker_queue().speaking is None


def test_disconnect_with_empty_queue_leaves_queue_empty():
    assert _disconnect_only_speaker_queue().queue == []


# --------------------------------------------------------------- disconnect: queued (not speaking) seat leaves

def _disconnect_queued_seat_queue():
    q9 = sq.SpeakQueue(clock=FakeClock())
    q9.request("P1")
    q9.request("P2")
    q9.request("P3")
    q9.disconnect("P2")
    return q9


def test_disconnect_of_queued_seat_does_not_touch_speaker():
    assert _disconnect_queued_seat_queue().speaking == "P1"


def test_disconnect_removes_only_that_seat_from_queue():
    assert _disconnect_queued_seat_queue().queue == ["P3"]


# --------------------------------------------------------------- disconnect: unknown seat is a no-op

def test_disconnect_of_unknown_seat_is_noop():
    q10 = sq.SpeakQueue(clock=FakeClock())
    q10.request("P1")
    q10.disconnect("P4")
    assert q10.speaking == "P1" and q10.queue == []


# --------------------------------------------------------------- default clock (no I/O triggered by default)

def test_default_clock_produces_real_timestamp():
    q6 = sq.SpeakQueue()
    q6.request("P1")
    assert isinstance(q6.speaking_since, float)
