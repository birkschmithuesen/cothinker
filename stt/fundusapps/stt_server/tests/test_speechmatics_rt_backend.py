"""speechmatics-rt backend: utterance collection, speaker label, timestamps.

Offline only -- a fake stream object stands in for SpeechmaticsStream.
Every text is an invented placeholder.
"""
import asyncio
import time
from types import SimpleNamespace

import numpy as np

from fundusapps.stt_server.backends import get_backend_class
from fundusapps.stt_server.backends.speechmatics_rt_backend import (
    SpeechmaticsRtBackend, _UtteranceCollector,
)
from fundusapps.stt_server.events import EventBus


def _drain(sub, deadline_s=0.3):
    out = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            out.append(sub.get(timeout=0.05))
        except Exception:
            continue
    return out


def test_registered():
    assert get_backend_class("speechmatics-rt") is SpeechmaticsRtBackend


def test_fragments_become_one_final_per_utterance():
    bus = EventBus()
    sub = bus.subscribe()
    col = _UtteranceCollector("0", bus)
    col.session_base_samples = 16000  # session started 1 s into the clock
    col.on_partial("eins", "S1", 0.2, 0.5)
    col.on_final("eins zwei", "S1", 0.2, 0.9)
    col.on_final("drei.", "S1", 1.0, 1.4)
    col.on_eou()
    finals = [e for e in _drain(sub) if e.type == "final"]
    assert len(finals) == 1
    f = finals[0]
    assert f.text == "eins zwei drei."
    assert f.recognizer_id == "S1"
    assert (f.t_start, f.t_end) == (1.2, 2.4)
    d = f.to_dict()
    assert d["speaker"] == "S1" and d["channel"] == "0"
    assert d["backend"] == "speechmatics-rt"


def test_speaker_change_splits_without_eou():
    bus = EventBus()
    sub = bus.subscribe()
    col = _UtteranceCollector("0", bus)
    col.on_final("eins", "S1", 0.0, 0.4)
    col.on_final("zwei", "S2", 0.6, 1.0)
    col.on_eou()
    finals = [e for e in _drain(sub) if e.type == "final"]
    assert [(f.text, f.recognizer_id) for f in finals] == [("eins", "S1"), ("zwei", "S2")]


def test_missing_label_falls_back_to_channel():
    bus = EventBus()
    sub = bus.subscribe()
    col = _UtteranceCollector("0", bus)
    col.on_final("eins", None, None, None)
    col.flush()
    (f,) = [e for e in _drain(sub) if e.type == "final"]
    assert f.recognizer_id == "0" and f.t_start is None and f.t_end is None


def test_max_utt_forces_a_final():
    bus = EventBus()
    sub = bus.subscribe()
    col = _UtteranceCollector("0", bus, max_utt_s=5.0)
    col.on_final("a", "S1", 0.0, 3.0)
    col.on_final("b", "S1", 3.0, 5.5)
    assert [e.text for e in _drain(sub) if e.type == "final"] == ["a b"]


class _FakeStream:
    """The SpeechmaticsStream surface: start, send_pcm, events, close."""

    def __init__(self, script):
        self._script = script
        self.sent = 0
        self._q = asyncio.Queue()

    async def start(self):
        pass

    async def send_pcm(self, chunk):
        # Like the real service: results only follow audio.
        if not self.sent:
            for ev in self._script:
                self._q.put_nowait(ev)
        self.sent += len(chunk)

    async def events(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self._q.put_nowait(None)


def _ev(kind, text="", label=None, a=None, b=None):
    return SimpleNamespace(kind=kind, text=text, speaker_label=label, start_s=a, end_s=b)


def test_enricher_finds_the_clock_via_channel():
    from fundusapps.stt_server.audio_clock import AudioClock, SpanRegistry, TimestampEnricher
    enr = TimestampEnricher()
    clock = AudioClock()
    clock.advance(16000)
    enr.register("0", clock, SpanRegistry())
    bus = EventBus()
    col = _UtteranceCollector("0", bus)
    ev = col._event("final", "eins", "S1", 0.0, 0.5)
    enr(ev)
    assert ev.audio_epoch == clock.epoch and ev.audio_epoch is not None


def test_backend_end_to_end_with_fake_stream():
    script = [_ev("partial", "hallo", "S1", 0.0, 0.3),
              _ev("final", "hallo welt", "S1", 0.0, 0.6),
              _ev("eou"),
              _ev("final", "und du", "S2", 1.0, 1.4),
              _ev("eou")]
    streams = []

    def factory():
        s = _FakeStream(script)
        streams.append(s)
        return s

    bus = EventBus()
    sub = bus.subscribe()
    be = SpeechmaticsRtBackend(bus, api_key="fake", stream_factory=factory)
    try:
        be.process_chunk(np.zeros(1600, dtype=np.int16), "0", audio_offset=32000)
        evs = _drain(sub, 0.8)
    finally:
        be.close()
    finals = [e for e in evs if e.type == "final"]
    assert [(f.text, f.recognizer_id, f.t_start) for f in finals] == [
        ("hallo welt", "S1", 2.0), ("und du", "S2", 3.0)]
    assert any(e.type == "partial" for e in evs)
    assert streams and streams[0].sent == 3200


def test_from_args_reads_key_from_env_only(monkeypatch):
    monkeypatch.setenv("SM_TEST_KEY", "k")
    args = SimpleNamespace(api_key_env="SM_TEST_KEY", language="de", max_delay=0.7,
                           eou_silence=0.5, max_utt=20.0, url="",
                           max_speakers=7, prefer_current_speaker=True)
    be = SpeechmaticsRtBackend.from_args(EventBus(), args)
    try:
        assert be._max_delay_s == 0.7 and be._url.startswith("wss://eu.rt.speechmatics.com")
        assert be._max_speakers == 7 and be._prefer_current_speaker is True
    finally:
        be.close()
    monkeypatch.delenv("SM_TEST_KEY")
    try:
        SpeechmaticsRtBackend.from_args(EventBus(), args)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "SM_TEST_KEY" in str(e)


def test_meeting_defaults_are_the_measured_ones():
    """26.10. meeting audio (docs/stt.md, "Speechmatics defaults"): max_delay
    2.0, max_speakers 10, prefer_current_speaker off."""
    from fundusapps.stt_server.backends import speechmatics_rt_backend as m
    assert m.DEFAULT_MAX_DELAY_S == 2.0 and m.DEFAULT_MAX_SPEAKERS == 10
    be = SpeechmaticsRtBackend(EventBus(), api_key="fake")
    try:
        assert be._max_delay_s == 2.0 and be._max_speakers == 10
        assert be._prefer_current_speaker is False
        stream = be._stream_factory()
        cfg = stream._max_speakers, stream._prefer_current_speaker, stream._max_delay_s
        assert cfg == (10, False, 2.0)
    finally:
        be.close()


def test_the_real_stream_sends_the_diarization_config():
    """The factory's kwargs end up in StartRecognition, not just on the object."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
    from shared.speechmatics import sm_config
    be = SpeechmaticsRtBackend(EventBus(), api_key="fake", max_speakers=12,
                               prefer_current_speaker=True)
    try:
        st = be._stream_factory()
        dcfg = sm_config(st._speakers, False, st._language, st._max_delay_s, st._eou_silence_s,
                         max_speakers=st._max_speakers,
                         prefer_current_speaker=st._prefer_current_speaker
                         )["transcription_config"]["speaker_diarization_config"]
        assert dcfg == {"max_speakers": 12, "prefer_current_speaker": True}
    finally:
        be.close()


def test_cli_parser_defaults_for_speechmatics_rt():
    """`--max-speakers` / `--prefer-current-speaker` exist and default to the
    measured values; `--max-delay` defaults to 2.0."""
    import sys as _sys
    orig = _sys.argv.copy()
    _sys.argv = ["pytest", "vosk"]
    try:
        from fundusapps.stt_server import args as a
    finally:
        _sys.argv = orig
    ns = a.parser.parse_args(["speechmatics-rt"])
    assert (ns.max_delay, ns.max_speakers, ns.prefer_current_speaker) == (2.0, 10, False)
    ns = a.parser.parse_args(["speechmatics-rt", "--max-speakers", "4", "--prefer-current-speaker"])
    assert (ns.max_speakers, ns.prefer_current_speaker) == (4, True)
