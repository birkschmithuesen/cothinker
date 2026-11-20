"""Peak vs. RMS, and the runtime-adjustable VAD threshold.

The single most important test in this file is
`test_peak_and_rms_differ_for_a_sine`. The operator page draws the VAD
threshold as a line into the input meter, and the VAD decides on RMS while
the meter has always shown PEAK. For speech those two differ by the crest
factor (roughly 3–10x), so a threshold line drawn against the peak bar sits
far too low and gets set wrong — confidently. Everything else here only
matters if that distinction holds.

No audio hardware and no network: FakeAudioSource subclasses the real
AudioSource and replaces start()/stop() only, so the metering under test is
the production code path.
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
from fundusapps.stt_server.vad import VadChunker  # noqa: E402
from fundusapps.stt_server.events import EventBus  # noqa: E402
from fundusapps.stt_server.app import app  # noqa: E402
sys.argv = _original_argv


class FakeAudioSource(AudioSource):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def feed(self, samples):
        data = np.asarray(samples, dtype=np.int16).tobytes()
        self._callback(data, len(samples), 0.0, None)


class FakeChunkingBackend:
    """A segmented backend (wants_streaming() False) — the only kind that
    builds a VadChunker, and therefore the only kind the threshold reaches."""

    def __init__(self, bus):
        self._bus = bus
        self.chunks = []
        self.closed = False

    def wants_streaming(self):
        return False

    def process_chunk(self, audio, recognizer_id, turn_id=None):
        self.chunks.append((recognizer_id, len(audio)))
        return []

    def close(self):
        self.closed = True


class FakeSd:
    DEVICES = [
        {"name": "Mikrofonarray (Realtek)", "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 44100.0, "hostapi": 0},
        {"name": "CABLE Output (VB-Audio)", "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 16000.0, "hostapi": 0},
    ]

    def query_devices(self, index=None):
        if index is None:
            return self.DEVICES
        return self.DEVICES[index]

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "ASIO"}]


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def sine(amplitude, samples=4410, periods=10):
    """Integer number of periods, so the RMS is exactly amplitude/sqrt(2)."""
    t = np.arange(samples, dtype=np.float64)
    return (amplitude * np.sin(2 * np.pi * periods * t / samples)).astype(np.int16)


# --- peak vs. RMS -----------------------------------------------------------

def test_peak_and_rms_differ_for_a_sine():
    """A sine has a known crest factor: RMS = peak / sqrt(2).

    Both numbers have to be right AND visibly different — if they came out
    equal, the meter and the VAD would be measuring the same thing and the
    threshold line would be meaningless.
    """
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(sine(16384).tobytes(), 4410, 0.0, None)

    assert source.level == pytest.approx(0.5, abs=2e-3)
    assert source.level_rms == pytest.approx(0.5 / np.sqrt(2), abs=2e-3)
    # The whole point: peak sits ~41% above RMS for this signal.
    assert source.level > source.level_rms * 1.3


def test_peak_and_rms_coincide_for_a_dc_block():
    """Crest factor 1: the two measures must agree, or one of them is wrong."""
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(np.full(512, 16384, dtype=np.int16).tobytes(), 512, 0.0, None)
    assert source.level == pytest.approx(0.5, abs=1e-3)
    assert source.level_rms == pytest.approx(0.5, abs=1e-3)


def test_rms_of_speech_like_noise_is_far_below_peak():
    """The real-world case the threshold line exists for: a peaky signal."""
    rng = np.random.default_rng(1234)
    # Sparse bursts — the crest factor of speech, exaggerated but the same shape.
    block = np.zeros(4096)
    block[::16] = rng.normal(0.0, 20000.0, size=block[::16].shape)
    block = np.clip(block, -32767, 32767).astype(np.int16)

    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(block.tobytes(), len(block), 0.0, None)

    assert source.level > source.level_rms * 3


def test_rms_matches_the_vad_definition_exactly():
    """AudioSource.level_rms and VadChunker's own RMS must be the same number.

    They are computed in two different places (PortAudio thread vs. recognizer
    thread) for two different purposes. If they ever drifted apart the drawn
    line would still look plausible, which is the worst kind of wrong.
    """
    block = sine(9000, samples=4096, periods=7)
    source = AudioSource(device=0, sample_rate=VOSK_RATE, num_channels=1)
    source._callback(block.tobytes(), len(block), 0.0, None)

    chunker = VadChunker(sample_rate=VOSK_RATE, min_silence_ms=500,
                         min_speech_ms=250, energy_threshold=0.01)
    assert source.level_rms == pytest.approx(chunker._rms(block), abs=1e-6)


def test_level_snapshot_carries_rms_and_zeroes_it_when_stale():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(sine(32767).tobytes(), 4410, 0.0, None)

    live = source.level_snapshot()
    assert live["level_rms"] == pytest.approx(1.0 / np.sqrt(2), abs=2e-3)

    stale = source.level_snapshot(now=source.level_timestamp + AudioSource.SIGNAL_TIMEOUT + 0.5)
    assert stale["signal"] is False
    assert stale["level"] == 0.0
    assert stale["level_rms"] == 0.0


def test_level_snapshot_before_any_frame():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    assert source.level_snapshot() == {
        "level": 0.0, "level_rms": 0.0, "signal": False, "age": None,
    }


def test_empty_block_does_not_divide_by_zero():
    source = AudioSource(device=0, sample_rate=44100, num_channels=1)
    source._callback(b"", 0, 0.0, None)
    assert source.level == 0.0
    assert source.level_rms == 0.0


# --- server fixture ---------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    """Running STTServer with a segmented backend, so a VadChunker exists."""
    monkeypatch.setattr(sr_module, "sd", FakeSd())
    bus = EventBus()
    stt = STTServer(
        audio_device_indexes=[0],
        backend=FakeChunkingBackend(bus),
        event_bus=bus,
        enable_audio_logging=False,
        sample_rates=[44100],
        vad_energy_threshold=0.01,
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
        # The chunker is built inside the recognizer thread's run() — wait for it.
        assert wait_for(lambda: stt.threads[0].chunker is not None)
        yield stt


@pytest.fixture
def client(server):
    previous = app.sst_server
    app.sst_server = server
    try:
        yield app.test_client()
    finally:
        app.sst_server = previous


# --- threshold at runtime ---------------------------------------------------

def test_levels_endpoint_reports_rms_and_threshold(client, server):
    server.created[0].feed(sine(32767))
    body = client.get("/levels").get_json()
    source = body["sources"][0]
    assert source["level"] == pytest.approx(1.0, abs=2e-3)
    assert source["level_rms"] == pytest.approx(1.0 / np.sqrt(2), abs=2e-3)
    assert body["vad_energy_threshold"] == pytest.approx(0.01)
    assert source["vad_energy_threshold"] == pytest.approx(0.01)


def test_vad_threshold_endpoint_updates_the_running_chunker(client, server):
    chunker = server.threads[0].chunker
    assert chunker.energy_threshold == pytest.approx(0.01)

    resp = client.post("/vad_threshold", json={"value": 0.031})
    assert resp.status_code == 200
    assert resp.get_json()["vad_energy_threshold"] == pytest.approx(0.031)

    # The live chunker, not a copy of it: the recognizer thread is running and
    # must pick the new value up without a restart.
    assert chunker.energy_threshold == pytest.approx(0.031)
    assert server.vad_energy_threshold == pytest.approx(0.031)
    assert server.threads[0].vad_energy_threshold == pytest.approx(0.031)


def test_vad_threshold_changes_what_the_vad_actually_accepts(server):
    """Not just an attribute: the decision has to change with it."""
    chunker = server.threads[0].chunker
    quiet = sine(300, samples=1600, periods=5)  # RMS ~0.0065
    assert chunker._rms(quiet) < 0.01

    server.set_vad_energy_threshold(0.001)
    assert chunker.feed(quiet) == []          # opens speech, nothing finalized yet
    assert chunker._in_speech is True


def test_new_recognizer_inherits_the_new_threshold(server):
    """A recognizer built after the change must not fall back to the startup
    value — that is what makes the setting survive a device switch."""
    server.set_vad_energy_threshold(0.042)
    server.set_audio_device(1, sample_rate=VOSK_RATE)

    # The existing chunker survives a device switch (only the source is swapped)…
    assert server.threads[0].chunker.energy_threshold == pytest.approx(0.042)
    # …and anything constructed from the server's config afterwards agrees.
    fresh = server._make_recognizer("probe", server.created[-1], None)
    assert fresh.vad_energy_threshold == pytest.approx(0.042)


def test_threshold_endpoint_reads_back_from_levels(client, server):
    client.post("/vad_threshold", json={"value": 0.005})
    body = client.get("/levels").get_json()
    assert body["vad_energy_threshold"] == pytest.approx(0.005)


@pytest.mark.parametrize("value", [0, 0.0, -0.1, 1.5, 2, "abc", None, [], float("nan"), True])
def test_invalid_thresholds_are_rejected_without_changing_anything(client, server, value):
    chunker = server.threads[0].chunker
    before = chunker.energy_threshold

    resp = client.post("/vad_threshold", json={"value": value})
    assert resp.status_code == 400
    assert resp.get_json()["error"]

    assert chunker.energy_threshold == before
    assert server.vad_energy_threshold == before


def test_missing_value_is_rejected(client, server):
    resp = client.post("/vad_threshold", json={})
    assert resp.status_code == 400


def test_operator_page_labels_both_meters_and_offers_the_control(client):
    """Smoke test for the page contract the endpoints above serve.

    Deliberately shallow — it cannot prove the line lands in the right place,
    only that the page still ships a labelled RMS bar with a threshold marker
    and a control that talks to /vad_threshold. If someone renames these, the
    endpoints keep working and the page silently stops using them.
    """
    html = client.get("/operator").get_data(as_text=True)
    assert 'class="meter peak"' in html
    assert 'class="meter rms"' in html
    assert '>Peak<' in html and '>RMS<' in html      # beide Groessen benannt
    assert 'b class="thr"' in html                    # Schwellenlinie im RMS-Balken
    assert 'id="vad-range"' in html and 'id="vad-num"' in html
    assert '/vad_threshold' in html
    assert 'level_rms' in html                        # Linie haengt am RMS, nicht am Peak


def test_threshold_endpoint_without_a_server_is_503():
    previous = app.sst_server
    app.sst_server = None
    try:
        assert app.test_client().post("/vad_threshold", json={"value": 0.01}).status_code == 503
    finally:
        app.sst_server = previous
