import pytest
import numpy as np
from pathlib import Path

from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.backends.vosk_backend import VoskBackend

VOSK_MODEL = Path("models/vosk-model-small-de-0.15")


@pytest.fixture
def vosk_backend():
    if not VOSK_MODEL.exists():
        pytest.skip(f"missing vosk model at {VOSK_MODEL}")
    bus = EventBus()
    backend = VoskBackend(event_bus=bus, model_path=str(VOSK_MODEL))
    yield backend, bus
    backend.close()


@pytest.mark.needs_vosk_model
def test_wants_streaming_is_true(vosk_backend):
    backend, _ = vosk_backend
    assert backend.wants_streaming() is True


@pytest.mark.needs_vosk_model
def test_silence_emits_no_final(vosk_backend):
    """Feed 1s of silence as int16 PCM bytes, no final should appear."""
    backend, bus = vosk_backend
    sub = bus.subscribe()
    silence = np.zeros(16000, dtype=np.int16)
    events = list(backend.process_chunk(silence, recognizer_id="left"))
    finals = [e for e in events if e.type == "final"]
    assert finals == []
