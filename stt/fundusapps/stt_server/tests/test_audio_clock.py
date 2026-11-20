"""The audio clock, the span registry and the enricher -- all three pure.

No audio, no threads with a lifetime of their own, no device: these are
the pieces that decide what a timestamp MEANS, so they are tested on
numbers alone. Task 7 proves the same arithmetic against a real
recognizer and a real resampler.
"""
import pytest

from fundusapps.stt_server.audio_clock import (
    AudioClock, SpanRegistry, TimestampEnricher,
)
from fundusapps.stt_server.events import TranscriptionEvent


def _final(**kw):
    base = dict(recognizer_id="0", type="final", text="Platzhaltersatz",
                timestamp=0.0, backend="whisper")
    base.update(kw)
    return TranscriptionEvent(**base)


# --- AudioClock -------------------------------------------------------

def test_advance_returns_the_position_of_the_first_sample():
    """The recognizer needs the offset of the frame it is ABOUT to hand
    on, not the one after it."""
    clock = AudioClock()
    assert clock.advance(1486, now=1000.0) == 0
    assert clock.advance(1486, now=1000.1) == 1486


def test_the_sample_count_is_the_sum_of_everything_that_passed():
    clock = AudioClock()
    for _ in range(10):
        clock.advance(1486, now=1000.0)
    assert clock.samples == 14860


def test_the_epoch_is_anchored_on_the_first_processed_frame():
    clock = AudioClock()
    clock.advance(16000, now=1000.0)
    assert clock.epoch == pytest.approx(999.0, abs=1e-9)


def test_later_frames_do_not_move_the_epoch():
    clock = AudioClock()
    clock.advance(16000, now=1000.0)
    clock.advance(16000, now=1001.0)
    assert clock.epoch == pytest.approx(999.0, abs=1e-9)


def test_reset_epoch_re_anchors_after_a_pause():
    """Muted frames are dropped, so the audio clock stands still while the
    wall clock runs on. Without re-anchoring, every utterance after a
    30-second pause would report itself 30 seconds too early."""
    clock = AudioClock()
    clock.advance(16000, now=1000.0)      # epoch 999.0
    clock.reset_epoch()                    # muted for 30 s
    clock.advance(16000, now=1031.0)       # 2 s of audio, 32 s of wall clock
    assert clock.epoch == pytest.approx(1029.0, abs=1e-9)


def test_seconds_rounds_to_milliseconds():
    clock = AudioClock()
    assert clock.seconds(16213) == 1.013


# --- SpanRegistry -----------------------------------------------------

def test_the_registry_returns_what_was_recorded():
    reg = SpanRegistry()
    reg.record("TURN-A", 16208, 39792)
    assert reg.get("TURN-A") == (16208, 39792)
    assert reg.get("TURN-B") is None
    assert reg.get(None) is None


def test_the_registry_drops_the_oldest_entry_beyond_its_bound():
    """A meeting is hours long and every utterance adds an entry. Bounded
    on purpose; the oldest turn is also the one nothing will ask for."""
    reg = SpanRegistry(max_entries=4)
    for i in range(6):
        reg.record(f"TURN-{i}", i * 1000, i * 1000 + 500)
    assert len(reg) == 4
    assert reg.get("TURN-0") is None
    assert reg.get("TURN-1") is None
    assert reg.get("TURN-5") == (5000, 5500)


# --- TimestampEnricher ------------------------------------------------

def _wired():
    clock = AudioClock()
    clock.advance(16000, now=1000.0)
    reg = SpanRegistry()
    reg.record("TURN-A", 16208, 39792)
    enr = TimestampEnricher()
    enr.register("0", clock, reg)
    return enr


def test_the_enricher_fills_the_three_fields_on_a_final():
    ev = _final(turn_id="TURN-A")
    _wired()(ev)
    assert ev.t_start == 1.013
    assert ev.t_end == 2.487
    assert ev.audio_epoch == pytest.approx(999.0)


def test_the_enricher_does_not_overwrite_a_backend_s_own_timestamps():
    """The ElevenLabs path computes t_start itself, from word timings the
    server sends. The enricher only adds what is missing -- it must never
    replace a better number with a coarser one."""
    ev = _final(turn_id="TURN-A", t_start=7.5, t_end=9.0)
    _wired()(ev)
    assert (ev.t_start, ev.t_end) == (7.5, 9.0)
    assert ev.audio_epoch == pytest.approx(999.0)


def test_the_enricher_leaves_everything_else_alone():
    enr = _wired()
    partial = _final(type="partial", turn_id="TURN-A")
    other_recognizer = _final(recognizer_id="9", turn_id="TURN-A")
    unknown_turn = _final(turn_id="TURN-Z")
    for ev in (partial, other_recognizer, unknown_turn):
        enr(ev)
        assert ev.t_start is None
        assert ev.t_end is None
    assert partial.audio_epoch is None
    assert other_recognizer.audio_epoch is None
    # A final from a KNOWN recognizer still gets its epoch, even when the
    # registry has no span for that turn -- the two answers are
    # independent, and half an answer beats none.
    assert unknown_turn.audio_epoch == pytest.approx(999.0)
