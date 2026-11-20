import time
import pytest
import numpy as np

from fundusapps.stt_server.events import EventBus, TranscriptionEvent

faster_whisper = pytest.importorskip("faster_whisper")
from fundusapps.stt_server.backends.whisper_backend import WhisperBackend


@pytest.fixture
def whisper_backend():
    bus = EventBus()
    try:
        backend = WhisperBackend(
            event_bus=bus,
            model_name="large-v3-turbo",
            device="cuda",
            compute_type="float16",
            language="de",
        )
    except Exception as e:
        pytest.skip(f"WhisperBackend unavailable: {e}")
    yield backend, bus
    backend.close()


@pytest.mark.needs_whisper
def test_wants_streaming_is_false(whisper_backend):
    backend, _ = whisper_backend
    assert backend.wants_streaming() is False


@pytest.mark.needs_whisper
def test_silence_eventually_emits_no_final(whisper_backend):
    """Submit 1s of silence as a chunk; after up to 5s the worker should
    have processed it without emitting a non-empty final."""
    backend, bus = whisper_backend
    sub = bus.subscribe()
    silence = np.zeros(16000, dtype=np.int16)
    list(backend.process_chunk(silence, recognizer_id="left"))
    deadline = time.time() + 5.0
    finals = []
    while time.time() < deadline:
        try:
            ev = sub.get(timeout=0.5)
        except Exception:
            continue
        if ev.type == "final" and ev.text.strip():
            finals.append(ev)
    assert finals == []
