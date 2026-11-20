"""Level metering and runtime device switching — without any audio hardware.

Everything here runs against a FakeAudioSource: it subclasses the real
AudioSource and only replaces start()/stop(), so the fan-out, the level
computation and the recognizer plumbing under test are the production ones.
"""
import sys
import time

import numpy as np
import pytest

# args.py runs argparse at import time; mock argv so importing sr is safe.
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]
from fundusapps.stt_server import sr as sr_module  # noqa: E402
from fundusapps.stt_server.sr import AudioSource, STTServer, VOSK_RATE  # noqa: E402
from fundusapps.stt_server.events import EventBus  # noqa: E402
from fundusapps.stt_server.app import app  # noqa: E402
sys.argv = _original_argv


class FakeAudioSource(AudioSource):
    """AudioSource that never opens a stream. feed() pushes synthetic int16
    frames through the real _callback, so subscribers and level metering behave
    exactly as they do with PortAudio driving them.
    """

    def __init__(self, *a, fail_on_start=False, **kw):
        super().__init__(*a, **kw)
        self.started = False
        self.stopped = False
        self.fail_on_start = fail_on_start

    def start(self):
        if self.fail_on_start:
            raise RuntimeError("device busy")
        self.started = True

    def stop(self):
        self.stopped = True

    def feed(self, samples):
        data = np.asarray(samples, dtype=np.int16).tobytes()
        self._callback(data, len(samples), 0.0, None)


class FakeBackend:
    """Records what it was handed; must survive a device switch untouched."""

    def __init__(self):
        self.chunks = []
        self.closed = False

    def wants_streaming(self):
        return True

    def process_chunk(self, audio, recognizer_id, turn_id=None):
        self.chunks.append((recognizer_id, len(audio)))
        return []

    def close(self):
        self.closed = True


class FakeSd:
    """Minimal stand-in for the sounddevice module."""

    DEVICES = [
        {"name": "Mikrofonarray (Realtek)", "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 44100.0, "hostapi": 0},
        {"name": "CABLE Output (VB-Audio)", "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 16000.0, "hostapi": 0},
        {"name": "Lautsprecher", "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 44100.0, "hostapi": 0},
    ]

    def query_devices(self, index=None):
        if index is None:
            return self.DEVICES
        return self.DEVICES[index]  # IndexError for unknown index, like the real one

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "ASIO"}]


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- level metering ---------------------------------------------------------

def test_level_is_zero_for_silence():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(np.zeros(512, dtype=np.int16).tobytes(), 512, 0.0, None)
    assert source.level == 0.0
    assert source.level_snapshot()["signal"] is True  # frames DO arrive, they are just silent


def test_level_is_one_at_full_scale():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(np.full(512, 32767, dtype=np.int16).tobytes(), 512, 0.0, None)
    assert source.level == pytest.approx(1.0, abs=1e-4)
    assert source.level_snapshot()["level"] == pytest.approx(1.0, abs=1e-4)


def test_level_follows_amplitude():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(np.full(512, 16384, dtype=np.int16).tobytes(), 512, 0.0, None)
    assert source.level == pytest.approx(0.5, abs=1e-3)


def test_no_signal_when_frames_stop():
    """A meter must not hold its last value once the stream is dead."""
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(np.full(512, 32767, dtype=np.int16).tobytes(), 512, 0.0, None)
    stale = source.level_snapshot(now=source.level_timestamp + AudioSource.SIGNAL_TIMEOUT + 0.5)
    assert stale["signal"] is False
    assert stale["level"] == 0.0
    assert stale["age"] > AudioSource.SIGNAL_TIMEOUT


def test_no_signal_before_any_frame():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    snap = source.level_snapshot()
    assert snap == {"level": 0.0, "level_rms": 0.0, "signal": False, "age": None}


# --- device switching -------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    """A running STTServer on a fake 44100 Hz source, one recognizer."""
    monkeypatch.setattr(sr_module, "sd", FakeSd())
    backend = FakeBackend()
    stt = STTServer(
        audio_device_indexes=[0],
        backend=backend,
        event_bus=EventBus(),
        enable_audio_logging=False,
        sample_rates=[44100],
    )
    created = []

    def _create(device, sample_rate, num_channels, asio_channels):
        source = FakeAudioSource(
            device=device, sample_rate=sample_rate, num_channels=num_channels,
            asio_channels=asio_channels, blocksize=stt.chunk_size,
        )
        created.append(source)
        return source

    stt._create_source = _create
    stt.created = created
    with stt:
        yield stt


def test_switch_moves_recognizers_without_touching_threads_or_backend(server):
    old = server.created[0]
    recognizer = server.threads[0]
    backend = server.backend

    # Frames flow at 44100 Hz: the recognizer resamples down to 16 kHz.
    old.feed(np.zeros(4410, dtype=np.int16))
    assert wait_for(lambda: backend.chunks)
    assert backend.chunks[0][1] == pytest.approx(1600, rel=0.1)

    result = server.set_audio_device(1, sample_rate=VOSK_RATE)

    assert result["device"] == 1
    assert result["device_name"] == "CABLE Output (VB-Audio)"
    assert old.stopped is True
    new = server.created[1]
    assert new.started is True
    assert recognizer.audio_source is new
    assert recognizer.samplerate == VOSK_RATE
    assert recognizer.is_alive()
    assert server.backend is backend and backend.closed is False
    assert list(server._sources) == [(1, VOSK_RATE)]


def test_switch_rebuilds_the_resampler(server):
    """New rate, new ratio — otherwise recognition silently runs at the wrong speed."""
    backend = server.backend
    server.set_audio_device(1, sample_rate=VOSK_RATE)
    new = server.created[1]
    assert wait_for(lambda: not server.threads[0]._source_changed.is_set())

    backend.chunks.clear()
    for _ in range(3):
        new.feed(np.zeros(1600, dtype=np.int16))
    assert wait_for(lambda: len(backend.chunks) == 3)
    # 16 kHz in, 16 kHz out: sample count must pass through unchanged.
    assert [n for _, n in backend.chunks] == [1600, 1600, 1600]


def test_frames_from_the_old_device_are_dropped_on_switch(server):
    """Buffered old-rate frames must not be fed through the new resampler."""
    recognizer = server.threads[0]
    recognizer._mute.set()  # keep the run loop from draining the queue
    old = server.created[0]
    old.feed(np.zeros(4410, dtype=np.int16))
    recognizer._mute.clear()

    server.set_audio_device(1, sample_rate=VOSK_RATE)
    assert recognizer.audio_queue.qsize() == 0
    assert recognizer.audio_queue is not old._subscribers[0]


def test_failed_switch_keeps_the_old_device_running(server):
    old = server.created[0]
    recognizer = server.threads[0]
    backend = server.backend

    def _failing(device, sample_rate, num_channels, asio_channels):
        source = FakeAudioSource(
            device=device, sample_rate=sample_rate, num_channels=num_channels,
            asio_channels=asio_channels, fail_on_start=True,
        )
        server.created.append(source)
        return source

    server._create_source = _failing
    with pytest.raises(RuntimeError, match="device busy"):
        server.set_audio_device(1)

    assert old.stopped is False
    assert recognizer.audio_source is old
    assert recognizer.samplerate == 44100
    assert recognizer.is_alive()
    assert backend.closed is False
    assert list(server._sources) == [(0, 44100)]

    # And recognition genuinely still works on the old device.
    backend.chunks.clear()
    old.feed(np.zeros(4410, dtype=np.int16))
    assert wait_for(lambda: len(backend.chunks) == 1)


def test_switch_to_unknown_device_is_rejected(server):
    with pytest.raises(ValueError, match="does not exist"):
        server.set_audio_device(99)
    assert server.created[0].stopped is False
    assert list(server._sources) == [(0, 44100)]


def test_switch_by_name_substring(server):
    result = server.set_audio_device("CABLE Output")
    assert result["device"] == 1
    # No sample_rate given -> the running rate is kept.
    assert result["sample_rate"] == 44100


def test_switch_to_same_device_is_a_noop(server):
    old = server.created[0]
    result = server.set_audio_device(0)
    assert result["device"] == 0
    assert old.stopped is False
    assert len(server.created) == 1


def test_asio_source_refuses_non_asio_target(monkeypatch):
    """The Fireface setup reads physical inputs 13/14; dropping those selectors
    would keep running while listening to the wrong thing."""
    monkeypatch.setattr(sr_module, "sd", FakeSd())
    stt = STTServer(
        audio_device_indexes=[0], backend=FakeBackend(), event_bus=EventBus(),
        enable_audio_logging=False, sample_rates=[44100],
        channels=["audience", "regie"], asio_channels=[13, 14],
    )
    created = []

    def _create(device, sample_rate, num_channels, asio_channels):
        source = FakeAudioSource(
            device=device, sample_rate=sample_rate, num_channels=num_channels,
            asio_channels=asio_channels,
        )
        created.append(source)
        return source

    stt._create_source = _create
    with stt:
        assert created[0].asio_channels == [12, 13]  # 1-indexed CLI -> 0-indexed
        with pytest.raises(ValueError, match="ASIO"):
            stt.set_audio_device(1)
        assert created[0].stopped is False
        assert len(stt.threads) == 2


# --- introspection ----------------------------------------------------------

def test_describe_sources_reports_device_and_level(server):
    server.created[0].feed(np.full(512, 32767, dtype=np.int16))
    described = server.describe_sources()
    assert len(described) == 1
    entry = described[0]
    assert entry["device"] == 0
    assert entry["device_name"] == "Mikrofonarray (Realtek)"
    assert entry["sample_rate"] == 44100
    assert entry["num_channels"] == 1
    assert entry["recognizers"] == ["0"]
    assert entry["level"] == pytest.approx(1.0, abs=1e-4)
    assert entry["signal"] is True


def test_list_input_devices_marks_the_active_one(server):
    devices = server.list_input_devices()
    assert [d["index"] for d in devices] == [0, 1]  # output-only device filtered out
    assert devices[0]["active"] is True
    assert devices[1]["active"] is False
    assert devices[0]["max_input_channels"] == 2


# --- HTTP endpoints ---------------------------------------------------------

@pytest.fixture
def client(server):
    previous = app.sst_server
    app.sst_server = server
    try:
        yield app.test_client()
    finally:
        app.sst_server = previous


def test_levels_endpoint(client, server):
    server.created[0].feed(np.full(512, 32767, dtype=np.int16))
    body = client.get("/levels").get_json()
    assert body["status"] == "initialized"
    assert body["sources"][0]["device_name"] == "Mikrofonarray (Realtek)"
    assert body["sources"][0]["level"] == pytest.approx(1.0, abs=1e-4)


def test_levels_endpoint_reports_pause_independently(client, server):
    """Paused server, live meter: pause only mutes the recognizers."""
    server.pause()
    server.created[0].feed(np.full(512, 16384, dtype=np.int16))
    body = client.get("/levels").get_json()
    assert body["status"] == "paused"
    assert body["sources"][0]["signal"] is True
    assert body["sources"][0]["level"] == pytest.approx(0.5, abs=1e-3)


def test_audio_devices_endpoint(client):
    body = client.get("/audio_devices").get_json()
    assert [d["index"] for d in body["devices"]] == [0, 1]


def test_audio_device_endpoint_switches(client, server):
    resp = client.post("/audio_device", json={"device": 1, "sample_rate": VOSK_RATE})
    assert resp.status_code == 200
    assert resp.get_json()["source"]["device"] == 1
    assert server.created[0].stopped is True


def test_audio_device_endpoint_reports_failure_without_killing_capture(client, server):
    resp = client.post("/audio_device", json={"device": 99})
    assert resp.status_code == 400
    assert "does not exist" in resp.get_json()["error"]
    assert server.created[0].stopped is False
    assert server.threads[0].is_alive()


def test_audio_device_endpoint_requires_a_device(client):
    resp = client.post("/audio_device", json={})
    assert resp.status_code == 400
