import pytest
from fundusapps.stt_server.backends import BACKENDS, get_backend_class
from fundusapps.stt_server.backends.base import RecognizerBackend


def test_get_backend_class_raises_for_unknown():
    with pytest.raises(KeyError):
        get_backend_class("nope")


def test_abc_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        RecognizerBackend(event_bus=None)


def test_vosk_is_registered():
    assert "vosk" in BACKENDS
    assert issubclass(BACKENDS["vosk"], RecognizerBackend)


def test_get_vosk_class():
    cls = get_backend_class("vosk")
    assert cls is BACKENDS["vosk"]


def test_get_whisper_class_lazy():
    pytest.importorskip("faster_whisper")
    cls = get_backend_class("whisper")
    from fundusapps.stt_server.backends.whisper_backend import WhisperBackend
    assert cls is WhisperBackend


def test_get_elevenlabs_scribe_class_lazy():
    pytest.importorskip("fundusapps.stt_server.backends.elevenlabs_scribe_backend")
    cls = get_backend_class("elevenlabs-scribe")
    from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
        ElevenLabsScribeBackend,
    )
    assert cls is ElevenLabsScribeBackend
