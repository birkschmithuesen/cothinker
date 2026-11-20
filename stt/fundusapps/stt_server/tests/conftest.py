import os
import sys
import types
import pytest

# Ensure tests do not require a real .env file. The args module loads .env
# at import time, so populate placeholders before any STT server import.
os.environ.setdefault("STT_HOST", "127.0.0.1")
os.environ.setdefault("STT_PORT", "5051")
os.environ.setdefault("SST_AUDIO_DEVICES", "dummy")
os.environ.setdefault("STT_AUDIO_DEVICES_SR", "44100")
os.environ.setdefault("BOTSERVER_HOST", "127.0.0.1")
os.environ.setdefault("BOTSERVER_PORT", "5084")


def _install_sounddevice_stub_if_missing():
    """`import sounddevice` loads the native PortAudio library at import time.

    On the exhibition PC that library is there and the real module is used —
    this function then does nothing at all. On a developer box without
    PortAudio installed, the import raises `OSError: PortAudio library not
    found` and turns EVERY stt_server test into a collection error, even
    though not a single one of them opens an audio stream: they all run
    against FakeAudioSource and monkeypatch `sr.sd` with their own fake.

    So the stand-in below only has to satisfy the import and the two lookups
    sr.py performs on module attributes. Anything that would really talk to
    hardware raises, loudly — a silent no-op stream would let a test claim to
    have captured audio that never existed.
    """
    try:
        import sounddevice  # noqa: F401
        return False
    except Exception:
        pass

    stub = types.ModuleType("sounddevice")

    def _unavailable(*_a, **_kw):
        raise RuntimeError(
            "sounddevice stub: no PortAudio on this machine. A test that "
            "reaches real audio hardware is a broken test."
        )

    stub.query_devices = _unavailable
    stub.query_hostapis = _unavailable
    stub.RawInputStream = _unavailable
    stub.OutputStream = _unavailable

    class _AsioSettings:
        def __init__(self, channel_selectors=None):
            self.channel_selectors = channel_selectors

    stub.AsioSettings = _AsioSettings
    stub.__stub__ = True
    sys.modules["sounddevice"] = stub
    return True


SOUNDDEVICE_IS_STUBBED = _install_sounddevice_stub_if_missing()


@pytest.fixture
def silence_pcm16():
    """1 second of silence as int16 mono @ 16 kHz."""
    import numpy as np
    return np.zeros(16000, dtype=np.int16)
