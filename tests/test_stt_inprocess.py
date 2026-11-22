# CoThinker — tests of the in-process STT transport
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of `shared/stt_inprocess.py` (`open_stt` with `transport="inprocess"`).

Most checks drive the session with an in-memory fake engine: no network, no
`websockets`. The last six talk to the scripted fake Speechmatics server
(`tests/fake_speechmatics.py`) through the real `SpeechmaticsStream`; they
`importorskip("websockets")` one by one, so the rest still runs without it.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from shared.speechmatics import SttEvent
from shared.stt import SttConfig, TranscriptEvent, open_stt
from shared.stt_inprocess import InprocessSession

CHUNK = bytes(1280)  # 40 ms of s16le mono 16 kHz


def until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def sttev(kind, text="", label="P1", start=0.0, end=0.03, volume=None, words=None):
    return SttEvent(kind=kind, text=text, speaker_label=label, start_s=start, end_s=end,
                    msg_id="m", volume=volume, words=words)


class Gate:
    """A thread-safe latch the fake engine polls from the session's loop."""

    def __init__(self):
        self._ev = threading.Event()

    def set(self):
        self._ev.set()

    async def wait(self):
        while not self._ev.is_set():
            await asyncio.sleep(0.005)


class FakeEngine:
    """In-memory stand-in for a Speechmatics stream: start/send can block or
    die, events are pushed from the test thread."""

    def __init__(self, *, block_start=None, block_send=None, die_on_send=False):
        self.block_start, self.block_send, self.die_on_send = block_start, block_send, die_on_send
        self.sent: list = []
        self.closed = 0
        self.started = False
        self._loop = None
        self._q: asyncio.Queue = asyncio.Queue()

    async def start(self):
        self._loop = asyncio.get_running_loop()
        if self.block_start is not None:
            await self.block_start.wait()
        self.started = True

    async def send_pcm(self, chunk):
        if self.block_send is not None:
            await self.block_send.wait()
        if self.die_on_send:
            await self._q.put(None)              # the reader sees the stream end ...
            raise ConnectionError("gone")        # ... and the send fails
        self.sent.append(chunk)

    async def events(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self.closed += 1
        await self._q.put(None)

    def push(self, ev):
        self._loop.call_soon_threadsafe(self._q.put_nowait, ev)


class Reader:
    """Drains `events()` on a thread, like a consumer would."""

    def __init__(self, session):
        self.events: list[TranscriptEvent] = []
        self.done = False
        self._t = threading.Thread(target=self._run, args=(session,), daemon=True)
        self._t.start()

    def _run(self, session):
        for ev in session.events():
            self.events.append(ev)
        self.done = True

    def of(self, type_):
        return [e for e in self.events if e.type == type_]


def make_cfg(**kw):
    return SttConfig(transport="inprocess", url="", **kw)


@pytest.fixture
def sessions():
    opened = []

    def make(factory, *, cfg=None, **kw):
        kw.setdefault("retry_s", (0.05,))
        s = InprocessSession(cfg or make_cfg(), factory, **kw).start()
        opened.append(s)
        return s

    yield make
    for s in opened:
        s.close()


def one_engine(**kw):
    eng = FakeEngine(**kw)
    return eng, (lambda speakers: eng)


def live_engine(sessions, **kw):
    """A session whose single engine is up; returns (session, engine, reader)."""
    eng, factory = one_engine(**kw)
    sess = sessions(factory)
    assert until(lambda: sess.state == "ok")
    return sess, eng, Reader(sess)


# ============================================================ API surface
def test_open_stt_inprocess_returns_an_audio_session():
    with pytest.raises(ValueError, match="api_key"):
        open_stt(make_cfg())                 # no key and no engine_factory
    eng = FakeEngine()
    sess = open_stt(make_cfg(), engine_factory=lambda speakers: eng)
    try:
        assert until(lambda: sess.state == "ok")
        assert callable(sess.send_pcm) and callable(sess.flush)
        assert callable(sess.events) and callable(sess.close)
    finally:
        sess.close()
    assert sess.state == "closed"


def test_unknown_engine_raises_and_nothing_else_is_tried():
    called = []
    with pytest.raises(ValueError, match="not available in-process"):
        open_stt(make_cfg(engine="other-engine", api_key="k"),
                 engine_factory=lambda speakers: called.append(speakers))
    assert called == []


def test_api_key_not_in_repr():
    cfg = make_cfg(api_key="sekrit-value-123")
    assert "sekrit-value-123" not in repr(cfg)
    assert cfg.api_key == "sekrit-value-123"


# ================================================================== mapping
def test_send_pcm_reaches_the_engine_in_order(sessions):
    sess, eng, _ = live_engine(sessions)
    chunks = [bytes([i]) * 640 for i in range(1, 8)]
    for c in chunks:
        sess.send_pcm(c)
    assert until(lambda: len(eng.sent) == len(chunks))
    assert eng.sent == chunks


def test_partials_pass_through_as_partial_events(sessions):
    sess, eng, rd = live_engine(sessions)
    eng.push(sttev("partial", "guten ta", label="P2", start=0.1, end=0.4))
    assert until(lambda: rd.of("partial"))
    ev = rd.of("partial")[0]
    assert (ev.text, ev.speaker, ev.t_start, ev.t_end) == ("guten ta", "P2", 0.1, 0.4)
    assert ev.turn_id == "1-1"
    assert "confirmed" not in ev.extra


def test_confirmed_fragment_is_a_confirmed_partial(sessions):
    sess, eng, rd = live_engine(sessions)
    eng.push(sttev("final", "guten tag", start=0.0, end=0.5))
    assert until(lambda: rd.of("partial"))
    ev = rd.of("partial")[0]
    assert ev.extra == {"confirmed": True}
    assert ev.text == "guten tag" and ev.turn_id == "1-1"
    assert rd.of("final") == []          # not merged until the utterance ends


def test_finals_are_merged_per_utterance_on_end_of_utterance(sessions):
    sess, eng, rd = live_engine(sessions)
    w1 = [{"w": "guten", "label": "P1", "volume": 0.5, "start_s": 0.0, "end_s": 0.2}]
    w2 = [{"w": "tag", "label": "P1", "volume": 0.7, "start_s": 0.2, "end_s": 0.4}]
    eng.push(sttev("final", "guten", start=0.0, end=0.2, volume=0.5, words=w1))
    eng.push(sttev("final", "tag", start=0.2, end=0.4, volume=0.7, words=w2))
    eng.push(sttev("eou", label=None, start=None, end=None))
    assert until(lambda: rd.of("final"))
    (fin,) = rd.of("final")
    assert fin.text == "guten tag" and fin.turn_id == "1-1" and fin.speaker == "P1"
    assert (fin.t_start, fin.t_end) == (0.0, 0.4)
    assert fin.extra["stream"] == 1
    assert fin.extra["volume"] == pytest.approx(0.6)
    assert fin.extra["words"] == w1 + w2
    assert len(rd.of("partial")) == 2    # one confirmed fragment each


def test_flush_returns_the_pending_final_and_does_not_repeat_it(sessions):
    sess, eng, rd = live_engine(sessions)
    eng.push(sttev("final", "noch offen"))
    assert until(lambda: rd.of("partial"))
    got = sess.flush()
    assert [(e.type, e.text) for e in got] == [("final", "noch offen")]
    assert sess.flush() == []
    sess.close()
    assert until(lambda: rd.done)
    assert rd.of("final") == []


def test_event_fields_match_the_wire(sessions):
    cfg = make_cfg(recognizer_id="table-7")
    eng, factory = one_engine()
    sess = sessions(factory, cfg=cfg)
    assert until(lambda: sess.state == "ok")
    rd = Reader(sess)
    before = time.time()
    eng.push(sttev("partial", "hallo", label="P3", start=0.5, end=0.9))
    assert until(lambda: rd.events)
    ev = rd.events[0]
    assert ev.recognizer_id == "table-7" and ev.backend == "speechmatics-rt"
    assert before <= ev.timestamp <= time.time()
    assert ev.channel is None
    wire = ev.to_wire()
    assert wire["type"] == "partial" and wire["speaker"] == "P3" and wire["text"] == "hallo"
    back = TranscriptEvent.from_wire(wire)
    assert (back.type, back.turn_id, back.speaker, back.t_start, back.t_end, back.backend) == (
        ev.type, ev.turn_id, ev.speaker, ev.t_start, ev.t_end, ev.backend)


def test_audio_epoch_plus_t_start_is_the_receive_wall_time(sessions):
    eng, factory = one_engine()
    sess = sessions(factory, wall=lambda: 5000.0)
    assert until(lambda: sess.state == "ok")
    rd = Reader(sess)
    sess.send_pcm(CHUNK)
    assert until(lambda: eng.sent)
    eng.push(sttev("partial", "hallo", start=0.01, end=0.03))
    eng.push(sttev("partial", "viel spaeter", start=9.0, end=9.5))   # audio never received
    assert until(lambda: len(rd.events) == 2)
    first, later = rd.events
    assert first.audio_epoch + first.t_start == pytest.approx(5.0)
    assert later.audio_epoch is None


# ================================================================== stages
class _Suffix:
    def __init__(self, name, tag, log):
        self.name, self.tag, self.log = name, tag, log

    def feed(self, ev):
        self.log.append((self.name, ev.type))
        return [ev.replace(text=ev.text + self.tag)]


class _Boom:
    name = "boom-stage"

    def feed(self, ev):
        raise RuntimeError("stage failure")


def test_stages_run_in_order_on_every_event(sessions):
    calls = []
    cfg = make_cfg(stages=(_Suffix("a", "-a", calls), _Suffix("b", "-b", calls)))
    eng, factory = one_engine()
    sess = sessions(factory, cfg=cfg)
    assert until(lambda: sess.state == "ok")
    rd = Reader(sess)
    eng.push(sttev("partial", "x"))
    eng.push(sttev("final", "y"))
    eng.push(sttev("eou", label=None, start=None, end=None))
    assert until(lambda: len(rd.events) == 3)
    assert [e.text for e in rd.events] == ["x-a-b", "y-a-b", "y-a-b"]
    assert [e.type for e in rd.events] == ["partial", "partial", "final"]
    assert calls[:2] == [("a", "partial"), ("b", "partial")]
    eng.push(sttev("final", "z"))
    assert until(lambda: len(rd.events) == 4)
    assert [(e.type, e.text) for e in sess.flush()] == [("final", "z-a-b")]   # flush runs the stages too


def test_a_raising_stage_passes_the_event_unchanged(sessions):
    logged = []
    cfg = make_cfg(stages=(_Boom(), _Suffix("after", "!", [])))
    eng, factory = one_engine()
    sess = sessions(factory, cfg=cfg, log=logged.append)
    assert until(lambda: sess.state == "ok")
    rd = Reader(sess)
    eng.push(sttev("partial", "eins"))
    eng.push(sttev("partial", "zwei"))
    assert until(lambda: len(rd.events) == 2)
    assert [e.text for e in rd.events] == ["eins!", "zwei!"]    # later stages still run
    assert len([m for m in logged if "boom-stage" in m]) == 1   # logged once


def test_close_flushes_then_ends_events(sessions):
    sess, eng, rd = live_engine(sessions)
    eng.push(sttev("final", "letzter satz"))
    assert until(lambda: rd.of("partial"))
    assert not rd.done
    chunks = [bytes([i]) * 640 for i in range(1, 21)]
    for c in chunks:
        sess.send_pcm(c)
    sess.close()                                     # at once: the queued audio must not be dropped
    assert eng.sent == chunks
    assert until(lambda: rd.done)
    assert [(e.type, e.text) for e in rd.events][-1] == ("final", "letzter satz")
    assert sess.state == "closed"


# ====================================================== lifecycle (fake engine)
def test_close_during_connect_closes_the_half_started_stream(sessions):
    made = []
    gate = Gate()

    def blocked(speakers):
        made.append(FakeEngine(block_start=gate))
        return made[-1]

    sess = sessions(blocked)
    assert until(lambda: made)
    sess.close()
    assert made[0].closed >= 1


def _dying_then_good(sessions):
    streams = []
    gate = Gate()

    def factory(speakers):
        s = FakeEngine(die_on_send=not streams, block_start=gate if not streams else None)
        streams.append(s)
        return s

    sess = sessions(factory)
    assert until(lambda: streams and streams[0]._loop)
    streams[0].push(sttev("final", "offen"))        # an utterance still open when this stream dies
    sess.send_pcm(bytes([1]) * 1280)
    assert until(lambda: sess._ss._buffer_bytes == 1280)    # buffered: no stream is live yet
    gate.set()
    assert until(lambda: sess.state == "ok" and len(streams) >= 2)
    return sess, streams


def test_a_stream_dying_mid_flush_is_retired_and_a_second_one_goes_live(sessions):
    sess, streams = _dying_then_good(sessions)
    assert len(streams) == 2 and streams[0].closed >= 1 and sess.state == "ok"
    rd = Reader(sess)                                # the open utterance arrives without close()
    assert until(lambda: rd.of("final"))
    assert rd.of("final")[0].text == "offen" and rd.of("final")[0].turn_id == "1-1"


def test_the_buffered_audio_reaches_the_second_stream(sessions):
    sess, streams = _dying_then_good(sessions)
    assert until(lambda: streams[1].sent)
    assert streams[1].sent == [bytes([1]) * 1280]


def _stalling_then_good(sessions):
    streams = []
    stall = Gate()                                   # never set: the first stream's send hangs

    def factory(speakers):
        s = FakeEngine(block_send=stall if not streams else None)
        streams.append(s)
        return s

    sess = sessions(factory, send_timeout_s=0.2)
    assert until(lambda: sess.state == "ok")
    return sess, streams


def test_a_stalled_send_returns_within_the_send_timeout(sessions):
    sess, streams = _stalling_then_good(sessions)
    t0 = time.monotonic()
    sess.send_pcm(b"S" * 640)
    assert time.monotonic() - t0 < 0.1               # the caller is never held up
    assert until(lambda: len(streams) >= 2 and streams[1].sent, 3)
    assert time.monotonic() - t0 < 0.2 + 1.0         # the timeout fired, not a hang


def test_the_stalled_chunk_is_resent_on_the_reconnected_stream(sessions):
    sess, streams = _stalling_then_good(sessions)
    sess.send_pcm(b"S" * 640)
    assert until(lambda: len(streams) >= 2 and streams[1].sent, 3)
    assert len(streams) == 2 and streams[1].sent == [b"S" * 640] and streams[0].closed >= 1


def test_audio_after_the_stall_flows_on_the_new_stream(sessions):
    sess, streams = _stalling_then_good(sessions)
    sess.send_pcm(b"S" * 640)
    sess.send_pcm(b"T" * 640)
    assert until(lambda: len(streams) >= 2 and streams[1].sent[-1:] == [b"T" * 640], 3)
    assert streams[1].sent == [b"S" * 640, b"T" * 640]


# ====================================== lifecycle (fake Speechmatics server)
class _ServerThread:
    """Runs the scripted fake server on its own loop, apart from the session's."""

    def __init__(self, script):
        from fake_speechmatics import FakeSpeechmaticsServer
        self.srv = FakeSpeechmaticsServer(script)
        self._loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._t.start()
        asyncio.run_coroutine_threadsafe(self.srv.__aenter__(), self._loop).result(5)

    @property
    def url(self):
        return self.srv.url

    @property
    def received(self):
        return list(self.srv.received)

    def chunks(self):
        return [m for m in self.received if isinstance(m, bytes)]

    def messages(self, name):
        return [m for m in self.received if isinstance(m, dict) and m.get("message") == name]

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.srv.__aexit__(None, None, None), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._t.join(5)


@pytest.fixture
def fake_server():
    pytest.importorskip("websockets")
    started = []

    def make(script):
        s = _ServerThread(script)
        started.append(s)
        return s

    yield make
    for s in started:
        s.stop()


def _words(text, start, end, speaker="P1"):
    return {"message": "AddTranscript", "results": [
        {"type": "word", "start_time": start, "end_time": end,
         "alternatives": [{"content": text, "speaker": speaker}]}]}


def _engine_for(url, start_gate=None, send_gate=None, send_entered=None):
    from shared.speechmatics import SpeechmaticsStream

    def factory(speakers):
        real = SpeechmaticsStream("test-key", "de", speakers, url=url)
        if start_gate is not None:
            real_start = real.start

            async def gated_start():
                await start_gate.wait()
                await real_start()
            real.start = gated_start
        if send_gate is not None:
            real_send = real.send_pcm

            async def slow_send(chunk):
                if send_entered is not None:
                    send_entered.set()
                await send_gate.wait()
                await real_send(chunk)
            real.send_pcm = slow_send
        return real
    return factory


def test_pcm_sent_before_the_stream_was_up_reaches_it_in_order(sessions, fake_server):
    srv = fake_server([])
    gate = Gate()
    sess = sessions(_engine_for(srv.url, start_gate=gate))
    chunks = [bytes([i]) * 1280 for i in range(1, 5)]
    for c in chunks:
        sess.send_pcm(c)
    assert until(lambda: sess._ss._buffer_bytes == 4 * 1280)
    gate.set()
    assert until(lambda: len(srv.chunks()) == 4)
    assert srv.chunks() == chunks


def test_the_event_carries_stream_1_and_the_receive_time_of_its_audio(sessions, fake_server):
    srv = fake_server([_words("eins", 0.0, 0.03), {"message": "EndOfUtterance", "metadata": {}}])
    gate = Gate()
    ticks = iter(range(10_000))
    sess = sessions(_engine_for(srv.url, start_gate=gate), wall=lambda: 1000.0 + next(ticks))
    rd = Reader(sess)
    for _ in range(4):
        sess.send_pcm(CHUNK)
    assert until(lambda: sess._ss._buffer_bytes == 4 * 1280)
    gate.set()
    assert until(lambda: rd.of("final"))
    fin = rd.of("final")[0]
    assert fin.extra["stream"] == 1 and fin.turn_id.startswith("1-")
    assert fin.audio_epoch + fin.t_start == pytest.approx(1.0)      # the first chunk's wall time


def test_states_ok_then_closed(sessions, fake_server):
    srv = fake_server([])
    sess = sessions(_engine_for(srv.url))
    assert until(lambda: sess.state == "ok")
    sess.close()
    assert sess.states == ["ok", "closed"]


def test_an_stt_error_degrades_and_the_session_reconnects(sessions, fake_server):
    srv = fake_server([{"message": "Error", "type": "quota_exceeded"}])
    sess = sessions(_engine_for(srv.url))
    assert until(lambda: "degraded" in sess.states and sess.states.count("ok") >= 2)
    assert sess.states[:3] == ["ok", "degraded", "ok"]


def test_two_streams_were_opened(sessions, fake_server):
    srv = fake_server([{"message": "Error", "type": "quota_exceeded"}])
    sess = sessions(_engine_for(srv.url))
    assert until(lambda: len(srv.messages("StartRecognition")) >= 2)
    assert sess.streams_started >= 2


def test_close_during_flush_ends_the_stream(sessions, fake_server):
    srv = fake_server([])
    gate = Gate()                                     # never set: the flush hangs in send
    entered = threading.Event()
    sess = sessions(_engine_for(srv.url, send_gate=gate, send_entered=entered))
    sess.send_pcm(CHUNK)
    assert entered.wait(5.0)                          # the opener now sits in its flush
    sess.close()
    assert until(lambda: srv.messages("EndOfStream"))
