"""The span: where inside a speech frame the speech actually is.

The DECISION is unchanged and test_vad.py (untouched) proves it. What is
new is that the chunker also remembers, per finalized turn, the first and
last sample of speech -- refined to 10 ms, because a 93 ms recognizer
frame cannot carry a 50 ms promise.

Every signal here is synthesised at test runtime. Nothing recorded, and
nothing anybody said.
"""
import numpy as np

from fundusapps.stt_server.audio_clock import SpanRegistry
from fundusapps.stt_server.vad import VadChunker
from fundusapps.stt_server.vad_detectors import RmsDetector, SUB_WINDOW, rms

SR = 16000


def _noise(n, amp, seed=3):
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(0.0, amp, size=n) * 32767.0,
                   -32768, 32767).astype(np.int16)


def _frame(silence_windows, speech_windows, trailing_windows, amp=0.25):
    """A frame built out of whole 10 ms sub-windows, so the expected
    positions are exact integers rather than something to round."""
    return np.concatenate([
        np.zeros(silence_windows * SUB_WINDOW, dtype=np.int16),
        _noise(speech_windows * SUB_WINDOW, amp),
        np.zeros(trailing_windows * SUB_WINDOW, dtype=np.int16),
    ])


def test_the_decision_is_still_the_whole_frame_rms():
    det = RmsDetector(0.01)
    loud = _noise(1600, 0.2)
    quiet = _noise(1600, 0.001)
    assert det.analyse(loud, 0).is_speech is (rms(loud) >= 0.01)
    assert det.analyse(quiet, 0).is_speech is (rms(quiet) >= 0.01)
    assert det.analyse(loud, 0).is_speech is True
    assert det.analyse(quiet, 0).is_speech is False


def test_the_span_starts_at_the_first_loud_sub_window():
    det = RmsDetector(0.02)
    frame = _frame(5, 10, 0)               # 50 ms silence, then 100 ms speech
    d = det.analyse(frame, 32000)          # this frame starts at 2.000 s
    assert d.is_speech is True
    assert d.first == 32000 + 5 * SUB_WINDOW


def test_the_span_ends_at_the_last_loud_sub_window():
    det = RmsDetector(0.02)
    frame = _frame(2, 8, 5)                # speech ends 100 ms into the frame
    d = det.analyse(frame, 0)
    assert d.last == (2 + 8) * SUB_WINDOW


def test_the_chunker_records_the_span_of_the_turn_it_finalizes():
    registry = SpanRegistry()
    chunker = VadChunker(sample_rate=SR, min_silence_ms=500, min_speech_ms=250,
                         energy_threshold=0.02, span_registry=registry)
    out = []
    chunker.feed(np.zeros(1600, dtype=np.int16))       # 0.00-0.10 silence
    chunker.feed(_frame(5, 5, 0))                       # speech opens at 0.15
    for _ in range(4):
        chunker.feed(_noise(1600, 0.25))                # 0.20-0.60 speech
    chunker.feed(_frame(0, 5, 5))                       # speech ends at 0.65
    for _ in range(6):
        out.extend(chunker.feed(np.zeros(1600, dtype=np.int16)))
    assert len(out) == 1
    assert registry.get(chunker.current_turn_id()) == (2400, 10400)


def test_feed_honours_an_explicit_start_sample():
    """The recognizer passes the audio-clock offset of each frame, so a
    span is a position in the whole stream, not in this chunker's own
    private count."""
    registry = SpanRegistry()
    chunker = VadChunker(sample_rate=SR, min_silence_ms=300, min_speech_ms=100,
                         energy_threshold=0.02, span_registry=registry)
    base = 16000 * 7
    out = []
    chunker.feed(_noise(1600, 0.25), start_sample=base)
    for i in range(5):
        out.extend(chunker.feed(np.zeros(1600, dtype=np.int16),
                                start_sample=base + (i + 1) * 1600))
    assert len(out) == 1
    assert registry.get(chunker.current_turn_id())[0] == base


def test_feed_counts_its_own_samples_when_no_offset_is_given():
    """Every existing caller -- and test_vad.py -- calls feed(frame) with
    one argument, and has to keep working."""
    registry = SpanRegistry()
    chunker = VadChunker(sample_rate=SR, min_silence_ms=300, min_speech_ms=100,
                         energy_threshold=0.02, span_registry=registry)
    chunker.feed(np.zeros(1600, dtype=np.int16))
    chunker.feed(np.zeros(1600, dtype=np.int16))
    chunker.feed(_noise(1600, 0.25))
    for _ in range(3):
        chunker.feed(np.zeros(1600, dtype=np.int16))
    assert registry.get(chunker.current_turn_id())[0] == 3200
