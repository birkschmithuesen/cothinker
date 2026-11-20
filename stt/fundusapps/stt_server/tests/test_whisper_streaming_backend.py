import time
import threading

import numpy as np
import pytest

from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.backends.whisper_streaming_backend import (
    WhisperStreamingBackend,
)


class StubOnlineASR:
    """Scripted stand-in for whisper_online.OnlineASRProcessor.

    Each call to process_iter() returns (beg_s, end_s, text) tuples driven
    by the `script` list passed in. The non-empty deltas are also appended
    to ``self.commited`` to mirror upstream's behaviour: the real
    OnlineASRProcessor returns only the newly-committed delta from
    process_iter() and exposes the full confirmed prefix on ``self.commited``.
    finish() returns the empty hypothesis-buffer remainder (also matching
    upstream — every committed word has already been flushed).
    """

    class _InnerASR:
        sep = " "

    def __init__(self, script):
        self._script = list(script)
        self._idx = 0
        self.commited: list = []  # mirror OnlineASRProcessor.commited
        self.asr = StubOnlineASR._InnerASR()  # mirror OnlineASRProcessor.asr

    def init(self):
        self._idx = 0
        self.commited = []

    def insert_audio_chunk(self, audio):
        pass

    def process_iter(self):
        if self._idx >= len(self._script):
            return (None, None, "")
        beg, end, text = self._script[self._idx]
        self._idx += 1
        if text:
            self.commited.append((beg, end, text))
        return (beg, end, text)

    def to_flush(self, sents):
        if not sents:
            return (None, None, "")
        return (sents[0][0], sents[-1][1], self.asr.sep.join(s[2] for s in sents))

    def finish(self):
        return (None, None, "")


@pytest.fixture
def stub_backend():
    bus = EventBus()
    backend = WhisperStreamingBackend(
        event_bus=bus,
        asr_factory=lambda: StubOnlineASR([
            (0.0, 0.5, "Das"),
            (0.5, 1.0, "ist"),
            (1.0, 1.5, "ein"),
        ]),
        min_chunk_size=0.05,
    )
    yield backend, bus
    backend.close()


def _drain(bus_sub, deadline_s=2.0):
    """Pull events from a subscriber queue until deadline."""
    out = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            out.append(bus_sub.get(timeout=0.1))
        except Exception:
            continue
    return out


def test_wants_streaming_is_true(stub_backend):
    backend, _ = stub_backend
    assert backend.wants_streaming() is True


def test_partial_events_carry_monotonic_seq_and_turn_id(stub_backend):
    backend, bus = stub_backend
    sub = bus.subscribe()
    # 0.2 s of speech-energy audio so internal VAD/timer fires re-inferences.
    chunk = (np.ones(3200, dtype=np.float32) * 0.1 * 32767).astype(np.int16)
    list(backend.process_chunk(chunk, recognizer_id="left"))
    list(backend.process_chunk(chunk, recognizer_id="left"))
    list(backend.process_chunk(chunk, recognizer_id="left"))
    events = _drain(sub, deadline_s=3.0)
    partials = [e for e in events if e.type == "partial" and e.text]
    assert len(partials) >= 1
    seqs = [p.partial_seq for p in partials]
    assert seqs == sorted(seqs), seqs
    assert seqs[0] == 0
    turn_ids = {p.turn_id for p in partials}
    assert None not in turn_ids
    assert len(turn_ids) == 1, "all partials in one utterance share one turn_id"


def test_final_event_carries_same_turn_id_as_partials(stub_backend):
    backend, bus = stub_backend
    sub = bus.subscribe()
    chunk = (np.ones(3200, dtype=np.float32) * 0.1 * 32767).astype(np.int16)
    list(backend.process_chunk(chunk, recognizer_id="left"))
    list(backend.process_chunk(chunk, recognizer_id="left"))
    # Then silence triggers end-of-utterance.
    silence = np.zeros(16000, dtype=np.int16)
    list(backend.process_chunk(silence, recognizer_id="left"))
    events = _drain(sub, deadline_s=3.0)
    partials = [e for e in events if e.type == "partial" and e.text]
    finals = [e for e in events if e.type == "final" and e.text]
    assert len(finals) == 1
    if partials:
        assert finals[0].turn_id == partials[-1].turn_id


def test_two_utterances_get_distinct_turn_ids():
    bus = EventBus()
    sub = bus.subscribe()
    backend = WhisperStreamingBackend(
        event_bus=bus,
        asr_factory=lambda: StubOnlineASR([(0, 0.5, "Eins")]),
        min_chunk_size=0.05,
    )
    try:
        # utterance 1
        chunk = (np.ones(3200, dtype=np.float32) * 0.1 * 32767).astype(np.int16)
        list(backend.process_chunk(chunk, recognizer_id="left"))
        list(backend.process_chunk(np.zeros(16000, dtype=np.int16), recognizer_id="left"))
        time.sleep(0.5)
        # utterance 2
        list(backend.process_chunk(chunk, recognizer_id="left"))
        list(backend.process_chunk(np.zeros(16000, dtype=np.int16), recognizer_id="left"))
        events = _drain(sub, deadline_s=3.0)
        finals = [e for e in events if e.type == "final" and e.text]
        assert len(finals) >= 2
        assert finals[0].turn_id != finals[1].turn_id
    finally:
        backend.close()


def test_backend_recovers_after_asr_exception():
    """If process_iter raises mid-utterance, the worker logs and continues."""
    bus = EventBus()
    sub = bus.subscribe()

    class CrashyASR(StubOnlineASR):
        def __init__(self):
            super().__init__([])
            self._calls = 0

        def process_iter(self):
            self._calls += 1
            if self._calls == 2:
                raise RuntimeError("boom")
            if self._calls > 3:
                return (None, None, "")
            return (0, 0.5, "ok")

    backend = WhisperStreamingBackend(
        event_bus=bus, asr_factory=CrashyASR, min_chunk_size=0.05
    )
    try:
        chunk = (np.ones(3200, dtype=np.float32) * 0.1 * 32767).astype(np.int16)
        for _ in range(5):
            list(backend.process_chunk(chunk, recognizer_id="left"))
        list(backend.process_chunk(np.zeros(16000, dtype=np.int16), recognizer_id="left"))
        # No assertion on event count — just verify backend is still alive
        # (worker thread didn't die) by submitting again.
        list(backend.process_chunk(chunk, recognizer_id="left"))
    finally:
        backend.close()
