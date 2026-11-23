# CoThinker — tests of the speaker split within one STT utterance
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_speaker_split.py

Only invented placeholder names and speech; embeddings are tiny fake vectors,
no network.
"""
from __future__ import annotations

import struct
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))

# The ONE import block of the modules under test:
from stt.pipeline.speaker_labels import enroll as se  # noqa: E402
from stt.pipeline.speaker_labels import split as ss  # noqa: E402

UNKNOWN = "unbekannt"
ADA = [1.0, 0.0]
BEN = [0.0, 1.0]
NOISE = [-1.0, -1.0]


class Store:
    """Just the `_persons` attribute SpeakerSplitter reads."""

    def __init__(self, persons) -> None:
        self._persons = persons


TWO = {"Ada": ADA, "Ben": BEN}


def scores_for(labels):
    """One score dict per window label ('A', 'B' or '?')."""
    table = {"A": {"Ada": 0.9, "Ben": 0.0}, "B": {"Ada": 0.0, "Ben": 0.9},
             "?": {"Ada": 0.1, "Ben": 0.1}}
    return [dict(table[c]) for c in labels]


def lab(s):
    m = {"A": "Ada", "B": "Ben", "?": UNKNOWN}
    return [m[c] for c in s]


def make_embed_fn(timeline, calls=None):
    """embed_fn from a window-start -> vector function (relative to 100.0)."""

    def fn(t0, t1):
        if calls is not None:
            calls.append((t0, t1))
        return list(timeline(t0 - 100.0))

    return fn


def change_at(cut):
    return lambda rel: ADA if rel < cut else BEN


def splitter(embed_fn, persons=None, **kw):
    kw.setdefault("log", lambda *_: None)
    sp = ss.SpeakerSplitter(Store(TWO if persons is None else persons),
                            embed_fn=embed_fn, **kw)
    return sp


def write_wav(path, seconds, rate=8000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<h", 50) * int(seconds * rate))
    return path


# ---------------------------------------------------------------- constants
def test_constants() -> None:
    assert ss.WINDOW_S == 1.5
    assert ss.HOP_S == 0.75
    assert ss.MIN_UTTERANCE_S == 4.0
    assert ss.MIN_RUN == 2
    assert ss.DEADLINE_S == 3.0
    assert ss.SPLIT_THRESHOLD == 0.30
    assert ss.SPLIT_MARK == "~"


def test_split_threshold_below_enroll_threshold() -> None:
    assert ss.SPLIT_THRESHOLD < se.DEFAULT_THRESHOLD


# ------------------------------------------------------------ window_starts
def test_window_starts_too_short_is_empty() -> None:
    assert ss.window_starts(1.0) == []


def test_window_starts_exactly_one_window() -> None:
    assert ss.window_starts(1.5) == [0.0]


def test_window_starts_four_seconds() -> None:
    assert ss.window_starts(4.0) == [0.0, 0.75, 1.5, 2.25]


def test_window_starts_custom_window_and_hop() -> None:
    assert ss.window_starts(3.0, window_s=1.0, hop_s=1.0) == [0.0, 1.0, 2.0]


def test_window_starts_every_window_fits() -> None:
    d = 6.3
    for s in ss.window_starts(d):
        assert s + ss.WINDOW_S <= d + 1e-6


# ------------------------------------------------------------ label_windows
def test_label_windows_best_name() -> None:
    out = ss.label_windows([{"Ada": 0.8, "Ben": 0.2}, {"Ada": 0.1, "Ben": 0.7}], 0.3)
    assert out == ["Ada", "Ben"]


def test_label_windows_below_threshold_is_unknown() -> None:
    assert ss.label_windows([{"Ada": 0.29, "Ben": 0.1}], 0.3) == [UNKNOWN]


def test_label_windows_threshold_inclusive() -> None:
    assert ss.label_windows([{"Ada": 0.3}], 0.3) == ["Ada"]


def test_label_windows_empty_scores_is_unknown() -> None:
    assert ss.label_windows([{}], 0.3) == [UNKNOWN]


# -------------------------------------------------------------- change_points
def test_change_points_clean_two_speakers() -> None:
    labels = lab("AAABBB")
    assert ss.change_points(labels, scores_for("AAABBB")) == [
        ("Ada", 0, 3), ("Ben", 3, 6)]


def test_change_points_single_speaker_is_empty() -> None:
    assert ss.change_points(lab("AAAAAA"), scores_for("AAAAAA")) == []


def test_change_points_all_unknown_is_empty() -> None:
    assert ss.change_points(lab("??????"), scores_for("??????")) == []


def test_change_points_weak_run_is_dropped() -> None:
    # B appears once in the middle of A: not a strong run
    assert ss.change_points(lab("AAABAAA"), scores_for("AAABAAA")) == []


def test_change_points_same_name_gap_is_absorbed() -> None:
    labels = lab("AAA?AAA")
    assert ss.change_points(labels, scores_for("AAA?AAA")) == []


def test_change_points_edge_run_counts_unknown_neighbours() -> None:
    # B at the very end is one window but has an unknown neighbour: strong.
    segs = ss.change_points(lab("AAAA?B"), scores_for("AAAA?B"))
    assert [s[0] for s in segs] == ["Ada", "Ben"]


def test_change_points_edge_run_one_window_without_help_is_weak() -> None:
    assert ss.change_points(lab("AAAAAB"), scores_for("AAAAAB")) == []


def test_change_points_min_run_one_keeps_single_windows() -> None:
    segs = ss.change_points(lab("AAAAAB"), scores_for("AAAAAB"), min_run=1)
    assert [s[0] for s in segs] == ["Ada", "Ben"]


def test_change_points_gap_cut_follows_scores() -> None:
    # Two unknown windows between A and B; the first leans A, the second B.
    labels = lab("AA??BB")
    scores = scores_for("AA??BB")
    scores[2] = {"Ada": 0.25, "Ben": 0.05}
    scores[3] = {"Ada": 0.05, "Ben": 0.25}
    assert ss.change_points(labels, scores) == [("Ada", 0, 3), ("Ben", 3, 6)]


def test_change_points_gap_cut_moves_with_scores() -> None:
    labels = lab("AA??BB")
    scores = scores_for("AA??BB")
    scores[2] = {"Ada": 0.05, "Ben": 0.25}
    scores[3] = {"Ada": 0.05, "Ben": 0.25}
    assert ss.change_points(labels, scores) == [("Ada", 0, 2), ("Ben", 2, 6)]


def test_change_points_tie_cuts_in_gap_middle() -> None:
    labels = lab("AA??BB")
    assert ss.change_points(labels, scores_for("AA??BB")) == [
        ("Ada", 0, 3), ("Ben", 3, 6)]


def test_change_points_three_speakers() -> None:
    persons = ["Ada", "Ben", "Cleo"]
    labels = ["Ada"] * 2 + ["Ben"] * 2 + ["Cleo"] * 2
    scores = []
    for n in labels:
        scores.append({p: (0.9 if p == n else 0.0) for p in persons})
    assert ss.change_points(labels, scores) == [
        ("Ada", 0, 2), ("Ben", 2, 4), ("Cleo", 4, 6)]


def test_change_points_segments_cover_all_windows() -> None:
    segs = ss.change_points(lab("AAA?BBB"), scores_for("AAA?BBB"))
    assert segs[0][1] == 0 and segs[-1][2] == 7
    assert all(a[2] == b[1] for a, b in zip(segs, segs[1:]))


# ------------------------------------------------------------------ cut_time
def test_cut_time_default() -> None:
    assert ss.cut_time(1) == pytest.approx(1.125)
    assert ss.cut_time(4) == pytest.approx(3.375)


def test_cut_time_custom() -> None:
    assert ss.cut_time(2, hop_s=1.0, window_s=2.0) == pytest.approx(2.5)


# ---------------------------------------------------------------- split_text
def test_split_text_empty_or_nonpositive_duration() -> None:
    assert ss.split_text("", [1.0], 4.0) == []
    assert ss.split_text("   ", [1.0], 4.0) == []
    assert ss.split_text("eins zwei", [1.0], 0.0) == []


def test_split_text_proportional_marks_later_pieces() -> None:
    out = ss.split_text("aaaa bbbb cccc dddd", [2.0], 4.0)
    assert out == [("aaaa bbbb", False), ("cccc dddd", True)]


def test_split_text_proportional_two_cuts() -> None:
    out = ss.split_text("aa bb cc dd ee ff", [1.0, 2.0], 3.0)
    assert len(out) == 3
    assert [e for _p, e in out] == [False, True, True]
    assert " ".join(p for p, _ in out) == "aa bb cc dd ee ff"


def test_split_text_no_spaces_cannot_be_cut() -> None:
    assert ss.split_text("einwort", [1.0], 2.0) == []


def test_split_text_more_cuts_than_word_boundaries_is_rejected() -> None:
    assert ss.split_text("aaa bbb", [1.0, 2.0], 4.0) == []


def test_split_text_with_word_times_not_estimated() -> None:
    words = [{"start": 0.0, "end": 0.9}, {"start": 1.0, "end": 1.9},
             {"start": 2.0, "end": 2.9}, {"start": 3.0, "end": 3.9}]
    out = ss.split_text("Hallo, du da. Okay", [1.95], 4.0, words)
    assert out == [("Hallo, du", False), ("da. Okay", False)]


def test_split_text_word_times_far_cut_falls_back_to_proportional() -> None:
    words = [{"start": 0.0, "end": 0.1}, {"start": 0.2, "end": 0.3}]
    out = ss.split_text("eins zwei", [9.0], 10.0, words)
    assert [e for _p, e in out] == [False, True]


def test_split_text_word_count_mismatch_falls_back() -> None:
    words = [{"start": 0.0, "end": 1.0}]
    out = ss.split_text("aaaa bbbb cccc dddd", [2.0], 4.0, words)
    assert out[1][1] is True


def test_split_text_malformed_words_fall_back() -> None:
    words = [{"start": 0.0}, {"end": 1.0}]
    out = ss.split_text("aaaa bbbb", [2.0], 4.0, words)
    assert out == [("aaaa", False), ("bbbb", True)]


def test_split_text_two_cuts_same_word_boundary_falls_back() -> None:
    words = [{"start": 0.0, "end": 1.0}, {"start": 1.1, "end": 2.0},
             {"start": 2.1, "end": 3.0}]
    out = ss.split_text("aa bb cc", [1.05, 1.06], 3.0, words)
    assert out == []


# ------------------------------------------------------------ dataclasses
def test_turn_defaults() -> None:
    t = ss.Turn(name="Ada", t_start=0.0, t_end=1.0)
    assert t.text == "" and t.estimated is False


def test_split_stats_defaults_are_independent() -> None:
    a, b = ss.SplitStats(), ss.SplitStats()
    a.extra["x"] = 1
    assert b.extra == {} and a.windows == 0 and a.outcome == ""


# ------------------------------------------------------------ SpeakerSplitter
TEXT = "ja genau das passt und dann machen wir weiter mit dem naechsten Punkt"


def test_split_two_speakers_with_word_times() -> None:
    words = []
    toks = TEXT.split()
    for i, _w in enumerate(toks):
        words.append({"start": i * 0.4, "end": i * 0.4 + 0.3})
    # utterance 6 s long; change near 3.0 s
    sp = splitter(make_embed_fn(change_at(3.0)))
    turns, st = sp.split(TEXT, 100.0, 106.0, words)
    sp.close()
    assert st.outcome == "split"
    assert [t.name for t in turns] == ["Ada", "Ben"]
    assert " ".join(t.text for t in turns) == TEXT
    assert not any(t.estimated for t in turns)
    assert turns[0].t_start == 100.0 and turns[-1].t_end == 106.0
    assert turns[0].t_end == turns[1].t_start


def test_split_without_word_times_marks_estimate() -> None:
    sp = splitter(make_embed_fn(change_at(3.0)))
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert [t.estimated for t in turns] == [False, True]
    assert turns[1].text.startswith(ss.SPLIT_MARK + " ")
    assert not turns[0].text.startswith(ss.SPLIT_MARK)


def test_split_labels_string_in_stats() -> None:
    sp = splitter(make_embed_fn(change_at(3.0)))
    _turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert st.labels.startswith("A") and st.labels.endswith("B")
    assert st.windows == len(st.labels)
    assert st.embed_calls == st.windows


def test_split_single_speaker_returns_none() -> None:
    sp = splitter(make_embed_fn(lambda rel: ADA))
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "single"


def test_split_short_utterance_not_embedded() -> None:
    calls: list = []
    sp = splitter(make_embed_fn(change_at(1.0), calls))
    turns, st = sp.split("kurz und knapp", 100.0, 103.0)
    sp.close()
    assert turns is None and st.outcome == "short" and calls == []


def test_split_min_utterance_boundary() -> None:
    calls: list = []
    sp = splitter(make_embed_fn(change_at(2.0), calls), min_utterance_s=10.0)
    _t, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert st.outcome == "short" and calls == []


@pytest.mark.parametrize("t0,t1", [(None, 5.0), (1.0, None), (None, None)])
def test_split_missing_span_is_short(t0, t1) -> None:
    sp = splitter(make_embed_fn(lambda rel: ADA))
    turns, st = sp.split(TEXT, t0, t1)
    sp.close()
    assert turns is None and st.outcome == "short"


@pytest.mark.parametrize("persons", [{}, {"Ada": ADA}])
def test_split_needs_two_enrolled(persons) -> None:
    calls: list = []
    sp = splitter(make_embed_fn(change_at(3.0), calls), persons=persons)
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "single" and calls == []


def test_split_store_without_persons_attribute() -> None:
    sp = ss.SpeakerSplitter(object(), embed_fn=lambda a, b: ADA,
                            log=lambda *_: None)
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "single"


def test_split_embed_calls_cover_windows() -> None:
    calls: list = []
    sp = splitter(make_embed_fn(lambda rel: ADA, calls))
    sp.split(TEXT, 100.0, 106.0)
    sp.close()
    starts = sorted(round(t0 - 100.0, 3) for t0, _ in calls)
    assert starts == ss.window_starts(6.0)
    assert all(round(t1 - t0, 3) == ss.WINDOW_S for t0, t1 in calls)


def test_split_embed_exception_is_error_and_counts_fallback() -> None:
    def boom(t0, t1):
        raise RuntimeError("down")

    sp = splitter(boom)
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "error"
    assert sp.fallbacks == 1 and sp.splits == 0


def test_split_empty_embedding_is_error() -> None:
    sp = splitter(lambda t0, t1: None)
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "error"


def test_split_deadline_miss_is_timeout() -> None:
    release = threading.Event()

    def slow(t0, t1):
        release.wait(5.0)
        return ADA

    sp = splitter(slow, deadline_s=0.2)
    t0 = time.monotonic()
    turns, st = sp.split(TEXT, 100.0, 106.0)
    elapsed = time.monotonic() - t0
    release.set()
    sp.close()
    assert turns is None and st.outcome == "timeout"
    assert elapsed < 2.0
    assert sp.fallbacks == 1


def test_split_text_not_cuttable_is_single() -> None:
    sp = splitter(make_embed_fn(change_at(3.0)))
    turns, st = sp.split("einwort", 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "single"


def test_split_counters() -> None:
    sp = splitter(make_embed_fn(change_at(3.0)))
    sp.split(TEXT, 100.0, 106.0)
    sp.split(TEXT, 100.0, 103.0)  # short: not counted as utterance
    sp.close()
    assert sp.utterances == 1
    assert sp.splits == 1
    assert sp.embed_calls == len(ss.window_starts(6.0))


def test_split_stats_ms_is_int() -> None:
    sp = splitter(lambda t0, t1: ADA)
    _t, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert isinstance(st.ms, int) and st.ms >= 0


def test_split_threshold_controls_unknown_windows() -> None:
    # Vectors only weakly similar to Ada (cos ~0.45) / Ben: threshold decides.
    weak = [0.45, 0.89]
    sp = splitter(lambda a, b: weak, threshold=0.99)
    _t, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert set(st.labels) == {"?"}


def test_split_unrelated_voice_gives_unknown_windows() -> None:
    sp = splitter(lambda a, b: NOISE)
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and set(st.labels) == {"?"}


def test_split_unknown_gap_between_speakers_still_splits() -> None:
    def tl(rel):
        if rel < 2.0:
            return ADA
        if rel < 3.0:
            return NOISE
        return BEN

    sp = splitter(make_embed_fn(tl))
    turns, st = sp.split(TEXT, 100.0, 108.0)
    sp.close()
    assert st.outcome == "split"
    assert [t.name for t in turns] == ["Ada", "Ben"]


def test_split_min_run_larger_suppresses_split() -> None:
    sp = splitter(make_embed_fn(change_at(3.0)), min_run=10)
    turns, st = sp.split(TEXT, 100.0, 106.0)
    sp.close()
    assert turns is None and st.outcome == "single"


def test_split_async_and_collect_returns_turns() -> None:
    logs: list[str] = []
    sp = splitter(make_embed_fn(change_at(3.0)))
    fut = sp.split_async(TEXT, 100.0, 106.0, None)
    turns = sp.collect(fut, log=logs.append)
    sp.close()
    assert [t.name for t in turns] == ["Ada", "Ben"]
    assert len(logs) == 1 and logs[0].startswith("speaker split: split ")


def test_collect_single_outcome_logs_nothing() -> None:
    logs: list[str] = []
    sp = splitter(make_embed_fn(lambda rel: ADA))
    turns = sp.collect(sp.split_async(TEXT, 100.0, 106.0), log=logs.append)
    sp.close()
    assert turns is None and logs == []


def test_collect_error_outcome_logs_numbers_only() -> None:
    logs: list[str] = []

    def boom(a, b):
        raise ValueError("secret detail")

    sp = splitter(boom)
    turns = sp.collect(sp.split_async(TEXT, 100.0, 106.0), log=logs.append)
    sp.close()
    assert turns is None
    assert len(logs) == 1 and "error" in logs[0] and "secret detail" not in logs[0]


def test_collect_never_raises_on_broken_future() -> None:
    class Broken:
        def result(self, timeout=None):
            raise TimeoutError()

    logs: list[str] = []
    sp = splitter(lambda a, b: ADA)
    assert sp.collect(Broken(), log=logs.append) is None
    sp.close()
    assert sp.fallbacks == 1
    assert len(logs) == 1 and "TimeoutError" in logs[0]


def test_default_embed_from_wav_uses_client(tmp_path) -> None:
    wav = write_wav(tmp_path / "bbb-1.wav", 8.0)
    seen: list[bytes] = []

    class Client:
        def embed(self, clip):
            seen.append(clip)
            return ADA

    sp = ss.SpeakerSplitter(Store(TWO), client=Client(), wav_path=wav,
                            log=lambda *_: None)
    assert sp.embed_fn(0.0, 1.5) == ADA
    sp.close()
    assert len(seen) == 1 and seen[0][:4] == b"RIFF"


def test_default_embed_without_wav_or_client_is_none(tmp_path) -> None:
    sp = ss.SpeakerSplitter(Store(TWO), log=lambda *_: None)
    assert sp.embed_fn(0.0, 1.5) is None
    sp.close()


def test_default_embed_span_outside_wav_is_none(tmp_path) -> None:
    wav = write_wav(tmp_path / "bbb-1.wav", 2.0)

    class Client:
        def embed(self, clip):
            raise AssertionError("must not be called")

    sp = ss.SpeakerSplitter(Store(TWO), client=Client(), wav_path=wav,
                            log=lambda *_: None)
    assert sp.embed_fn(50.0, 51.5) is None
    sp.close()


def test_split_from_real_wav_with_missing_audio_is_error(tmp_path) -> None:
    # wav shorter than the span: windows past the end embed to None -> error
    wav = write_wav(tmp_path / "bbb-1.wav", 2.0)

    class Client:
        def embed(self, clip):
            return ADA

    sp = ss.SpeakerSplitter(Store(TWO), client=Client(), wav_path=wav,
                            log=lambda *_: None)
    turns, st = sp.split(TEXT, 0.0, 8.0)
    sp.close()
    assert turns is None and st.outcome == "error"
