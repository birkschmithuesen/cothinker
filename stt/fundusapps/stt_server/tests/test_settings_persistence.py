"""Die eingemessenen Schwellen ueberleben den Neustart.

Vor Ort wird am realen Raum und am realen Mikrofon eingemessen. Bis dieses
Feature existierte, schrieb der Server das Ergebnis nirgendwohin: ein Neustart
nach einem Absturz fuhr stillschweigend wieder die Kommandozeilen-Vorgaben.

Der wichtigste Test dieser Datei ist
`test_typed_option_beats_the_stored_value` — die Rangfolge. Ohne sie waere die
Einstellungsdatei eine unsichtbare Falle, die einen getippten Befehl aushebelt.
Er ist das Ziel der Mutationsprobe.

Kein Audio, kein Netz: FakeAudioSource wie in test_vad_threshold.py, und die
Einstellungsdatei liegt in tmp_path.
"""
import json
import sys
import time

import numpy as np
import pytest

# args.py laesst argparse beim Import laufen; argv mocken, damit der Import geht.
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]
from fundusapps.stt_server import sr as sr_module  # noqa: E402
from fundusapps.stt_server import settings as settings_module  # noqa: E402
from fundusapps.stt_server.args import parser  # noqa: E402
from fundusapps.stt_server.settings import (  # noqa: E402
    BUILT_IN_DEFAULTS, PERSISTED_KEYS, RuntimeSettings, resolve_runtime_settings,
)
from fundusapps.stt_server.sr import AudioSource, STTServer  # noqa: E402
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
    """wants_streaming() False — die einzige Sorte, die einen VadChunker baut
    und die den geladenen Schwellwert also wirklich anfasst."""

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
        {"name": "ZOOM AMS-24", "max_input_channels": 2, "max_output_channels": 0,
         "default_samplerate": 44100.0, "hostapi": 0},
    ]

    def query_devices(self, index=None):
        return self.DEVICES if index is None else self.DEVICES[index]

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "ASIO"}]


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def build_server(monkeypatch, **kwargs):
    """Ein STTServer wie beim Start, nur ohne Audio-Hardware."""
    monkeypatch.setattr(sr_module, "sd", FakeSd())
    bus = EventBus()
    stt = STTServer(
        audio_device_indexes=[0],
        backend=FakeChunkingBackend(bus),
        event_bus=bus,
        enable_audio_logging=False,
        sample_rates=[44100],
        **kwargs,
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
    return stt


def parse(*argv):
    """Durch den ECHTEN Parser aus args.py — inklusive default=SUPPRESS.

    Absichtlich nicht durch einen nachgebauten Parser: getestet werden soll
    genau die Konfiguration, mit der der Server auch startet.
    """
    # `--opt=wert` und nicht `--opt wert`: beide Optionen haben nargs='+' und
    # wuerden sonst den Namen des Subkommandos als weiteren Wert schlucken --
    # derselbe Grund, aus dem --channels in args.py im _channel_parent steht.
    return parser.parse_args(
        ["--audio_devices=dummy", "--sample_rates=44100", *argv]
    )


# --- Datei schreiben --------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return RuntimeSettings(tmp_path / "settings" / "stt_runtime.json")


@pytest.fixture
def server(monkeypatch):
    stt = build_server(monkeypatch, vad_energy_threshold=0.01,
                       mic_gate_enabled=True, mic_gate_threshold=0.002,
                       mic_gate_debounce_ms=1500, mic_gate_release_ratio=0.5)
    with stt:
        assert wait_for(lambda: stt.threads[0].chunker is not None)
        yield stt


@pytest.fixture
def client(server, store):
    previous_server, previous_store = app.sst_server, getattr(app, "settings_store", None)
    app.sst_server = server
    app.settings_store = store
    try:
        yield app.test_client()
    finally:
        app.sst_server = previous_server
        app.settings_store = previous_store


def read(store):
    with open(store.path, encoding="utf-8") as fh:
        return json.load(fh)


def test_posting_a_threshold_writes_it_to_the_file(client, store):
    resp = client.post("/vad_threshold", json={"value": 0.031})

    assert resp.status_code == 200
    assert resp.get_json()["persisted"] is True
    assert read(store)["vad_energy_threshold"] == pytest.approx(0.031)


def test_posting_gate_values_writes_all_three(client, store, server):
    """Alle drei, nicht nur der geaenderte: die Datei beschreibt danach das
    ganze Gate und nicht einen Ausschnitt davon."""
    resp = client.post("/mic_gate", json={"value": 0.004, "debounce_ms": 900,
                                          "release_ratio": 0.4})

    assert resp.status_code == 200
    assert resp.get_json()["persisted"] is True
    stored = read(store)
    assert stored["mic_gate_threshold"] == pytest.approx(0.004)
    assert stored["mic_gate_debounce_ms"] == 900
    assert stored["mic_gate_release_ratio"] == pytest.approx(0.4)


def test_writing_one_setting_keeps_the_others(client, store):
    """Sonst loescht die naechste VAD-Aenderung die eingemessenen Gate-Werte."""
    client.post("/mic_gate", json={"value": 0.004})
    client.post("/vad_threshold", json={"value": 0.02})

    stored = read(store)
    assert stored["mic_gate_threshold"] == pytest.approx(0.004)
    assert stored["vad_energy_threshold"] == pytest.approx(0.02)


def test_rejected_values_are_not_written(client, store):
    client.post("/vad_threshold", json={"value": 0.02})
    before = read(store)

    assert client.post("/vad_threshold", json={"value": 5}).status_code == 400
    assert read(store) == before


def test_the_file_is_replaced_atomically(client, store, monkeypatch):
    """Erst Nebendatei, dann os.replace: sonst bleibt bei einem Stromausfall
    mitten im Schreiben eine halbe Datei zurueck — genau der Ausfall, gegen den
    das Feature schuetzt."""
    replaced = []
    monkeypatch.setattr(settings_module.os, "replace",
                        lambda a, b: replaced.append((a, b)))

    client.post("/vad_threshold", json={"value": 0.02})

    assert replaced == [(store.path + ".tmp", store.path)]


# --- Datei lesen ------------------------------------------------------------

def test_a_freshly_built_server_starts_with_the_stored_value(monkeypatch, tmp_path):
    """Der eigentliche Zweck: was gestern eingemessen wurde, gilt heute frueh.

    Der Weg ist der echte: Datei laden, Rangfolge anwenden, die aufgeloesten
    Werte in den STTServer geben — und dann steht der Wert im laufenden
    VadChunker, nicht nur in einem Attribut.
    """
    path = tmp_path / "stt_runtime.json"
    path.write_text(json.dumps({
        "vad_energy_threshold": 0.037,
        "mic_gate_threshold": 0.0044,
        "mic_gate_release_ratio": 0.6,
        "mic_gate_debounce_ms": 900,
    }), encoding="utf-8")

    args = parse("vosk")
    resolve_runtime_settings(args, RuntimeSettings(path).load(), env={})

    stt = build_server(
        monkeypatch,
        vad_energy_threshold=args.vad_energy_threshold,
        mic_gate_enabled=True,
        mic_gate_threshold=args.mic_gate_threshold,
        mic_gate_release_ratio=args.mic_gate_release_ratio,
        mic_gate_debounce_ms=args.mic_gate_debounce_ms,
    )
    with stt:
        assert wait_for(lambda: stt.threads[0].chunker is not None)
        assert stt.threads[0].chunker.energy_threshold == pytest.approx(0.037)
        assert stt.mic_gate.threshold == pytest.approx(0.0044)
        assert stt.mic_gate.release_ratio == pytest.approx(0.6)
        assert stt.mic_gate.debounce_ms == 900


def test_a_full_round_trip(client, store, monkeypatch):
    """POST — Datei — neuer Server. Der Gang, den ein Absturz erzwingt."""
    client.post("/vad_threshold", json={"value": 0.023})
    client.post("/mic_gate", json={"value": 0.006})

    args = parse("vosk")
    resolve_runtime_settings(args, RuntimeSettings(store.path).load(), env={})

    assert args.vad_energy_threshold == pytest.approx(0.023)
    assert args.mic_gate_threshold == pytest.approx(0.006)


def test_a_missing_file_is_the_normal_first_run(tmp_path):
    assert RuntimeSettings(tmp_path / "gibtsnicht.json").load() == {}


# --- Rangfolge --------------------------------------------------------------

def test_typed_option_beats_the_stored_value():
    """MUTATION PROBE TARGET.

    Wer `--vad_energy_threshold 0.005` tippt, meint das auch. Eine gespeicherte
    Datei, die den getippten Befehl aushebelt, waere eine unsichtbare Falle —
    und zwar eine, die man erst im Betrieb bemerkt, weil der Server ja laeuft.

    Faellt die Rangfolge weg (gespeicherter Wert gewinnt immer), steht hier
    0.03 statt 0.005.
    """
    stored = {"vad_energy_threshold": 0.03, "mic_gate_threshold": 0.02}
    args = parse("infomaniak-whisper", "--vad_energy_threshold", "0.005",
                 "--mic-gate-threshold", "0.001")

    resolved = resolve_runtime_settings(args, stored, env={})

    assert args.vad_energy_threshold == pytest.approx(0.005)
    assert args.mic_gate_threshold == pytest.approx(0.001)
    assert resolved["vad_energy_threshold"][1] == "Kommandozeile"
    assert resolved["mic_gate_threshold"][1] == "Kommandozeile"


def test_stored_value_wins_when_nothing_was_typed():
    args = parse("infomaniak-whisper")
    resolved = resolve_runtime_settings(args, {"vad_energy_threshold": 0.03}, env={})

    assert args.vad_energy_threshold == pytest.approx(0.03)
    assert resolved["vad_energy_threshold"][1] == "Einstellungsdatei"


def test_env_is_used_below_the_file_and_above_the_default():
    env = {"STT_MIC_GATE_THRESHOLD": "0.008", "STT_MIC_GATE_DEBOUNCE_MS": "700"}

    args = parse("vosk")
    resolved = resolve_runtime_settings(args, {"mic_gate_threshold": 0.02}, env=env)

    assert args.mic_gate_threshold == pytest.approx(0.02)          # Datei schlaegt .env
    assert resolved["mic_gate_threshold"][1] == "Einstellungsdatei"
    assert args.mic_gate_debounce_ms == 700                         # .env schlaegt Vorgabe
    assert resolved["mic_gate_debounce_ms"][1].startswith(".env")


def test_the_built_in_default_is_the_last_resort():
    args = parse("infomaniak-whisper")
    resolved = resolve_runtime_settings(args, {}, env={})

    for key in PERSISTED_KEYS:
        assert getattr(args, key) == BUILT_IN_DEFAULTS[key]
        assert resolved[key][1] == "Vorgabe"


def test_typing_exactly_the_default_still_counts_as_typed():
    """default=SUPPRESS statt default=None: nur so ist ein getippter Wert, der
    zufaellig dem Vorgabewert gleicht, von "gar nichts getippt" zu
    unterscheiden."""
    args = parse("infomaniak-whisper", "--vad_energy_threshold", "0.01")
    resolved = resolve_runtime_settings(args, {"vad_energy_threshold": 0.03}, env={})

    assert args.vad_energy_threshold == pytest.approx(0.01)
    assert resolved["vad_energy_threshold"][1] == "Kommandozeile"


def test_no_setting_is_ever_left_as_none():
    """Ein `None`, das bis in VadChunker.energy_threshold durchrutscht, macht
    den VAD unbrauchbar und faellt erst im Betrieb auf — beim leisen Backend,
    das ploetzlich alles oder nichts als Sprache hoert."""
    for argv in (["vosk"], ["whisper"], ["whisper-streaming"],
                 ["elevenlabs-scribe"], ["infomaniak-whisper"]):
        args = parse(*argv)
        resolve_runtime_settings(args, {}, env={})
        for key in PERSISTED_KEYS:
            value = getattr(args, key)
            assert value is not None, f"{key} ist None bei Backend {argv[0]}"
            assert isinstance(value, (int, float))


# --- kaputte Dateien --------------------------------------------------------

def test_broken_json_is_ignored_and_the_server_still_starts(monkeypatch, tmp_path, caplog):
    """Ein Server, der wegen einer beschaedigten Einstellungsdatei nicht
    startet, ist am Ausstellungstag ein Totalausfall."""
    path = tmp_path / "stt_runtime.json"
    path.write_text('{"vad_energy_threshold": 0.03,', encoding="utf-8")   # abgeschnitten

    with caplog.at_level("ERROR"):
        stored = RuntimeSettings(path).load()
    assert stored == {}
    assert caplog.records, "eine beschaedigte Datei muss deutlich geloggt werden"

    args = parse("infomaniak-whisper")
    resolve_runtime_settings(args, stored, env={})
    assert args.vad_energy_threshold == pytest.approx(0.01)

    stt = build_server(monkeypatch, vad_energy_threshold=args.vad_energy_threshold)
    with stt:
        assert wait_for(lambda: stt.threads[0].chunker is not None)
        assert stt.threads[0].chunker.energy_threshold == pytest.approx(0.01)


@pytest.mark.parametrize("content", [
    '[]',                       # kein Objekt
    '"0.03"',                   # kein Objekt
    'null',
    '',                         # leere Datei
])
def test_a_file_that_is_not_an_object_is_ignored(tmp_path, content):
    path = tmp_path / "stt_runtime.json"
    path.write_text(content, encoding="utf-8")
    assert RuntimeSettings(path).load() == {}


@pytest.mark.parametrize("bad", [0, -0.1, 1.5, "laut", None, True, [], float("nan")])
def test_an_out_of_range_value_in_the_file_is_discarded(tmp_path, bad, caplog):
    """Eine von Hand editierte Datei ist keine vertrauenswuerdige Quelle: sie
    geht durch dieselbe Validierung wie ein POST."""
    path = tmp_path / "stt_runtime.json"
    path.write_text(json.dumps({"vad_energy_threshold": bad,
                                "mic_gate_threshold": 0.004}), encoding="utf-8")

    with caplog.at_level("ERROR"):
        stored = RuntimeSettings(path).load()

    assert "vad_energy_threshold" not in stored
    assert caplog.records
    # Der Rest der Datei war lesbar und einmal richtig gemessen — er bleibt.
    assert stored["mic_gate_threshold"] == pytest.approx(0.004)


@pytest.mark.parametrize("bad", [0, 1.0, 1.5, -0.2, "halb"])
def test_a_bad_release_ratio_in_the_file_is_discarded(tmp_path, bad):
    """1.0 nimmt die Hysterese heraus: das Gate flattert dann an der Schwelle
    und erzeugt dutzende Interview-Grenzen pro Minute."""
    path = tmp_path / "stt_runtime.json"
    path.write_text(json.dumps({"mic_gate_release_ratio": bad}), encoding="utf-8")
    assert RuntimeSettings(path).load() == {}


@pytest.mark.parametrize("bad", [-5, 60001, "sofort", True])
def test_a_bad_debounce_in_the_file_is_discarded(tmp_path, bad):
    path = tmp_path / "stt_runtime.json"
    path.write_text(json.dumps({"mic_gate_debounce_ms": bad}), encoding="utf-8")
    assert RuntimeSettings(path).load() == {}


def test_unknown_keys_are_ignored_without_dropping_the_good_ones(tmp_path):
    path = tmp_path / "stt_runtime.json"
    path.write_text(json.dumps({"vad_energy_threshold": 0.02,
                                "audio_device": "CABLE Output"}), encoding="utf-8")
    assert RuntimeSettings(path).load() == {"vad_energy_threshold": 0.02}


# --- Schreiben schlaegt fehl ------------------------------------------------

@pytest.fixture
def unwritable_store(tmp_path):
    """Ein Pfad, der nicht beschreibbar sein KANN: das Elternverzeichnis ist in
    Wahrheit eine Datei. Absichtlich kein chmod — das wirkt unter Windows nicht
    und als root gar nicht, und dieser Test muss ueberall dasselbe pruefen."""
    blocker = tmp_path / "blockiert"
    blocker.write_text("keine Verzeichnisdatei", encoding="utf-8")
    return RuntimeSettings(blocker / "stt_runtime.json")


def test_a_write_error_does_not_undo_the_runtime_change(monkeypatch, unwritable_store):
    stt = build_server(monkeypatch, vad_energy_threshold=0.01)
    previous_server, previous_store = app.sst_server, getattr(app, "settings_store", None)
    with stt:
        assert wait_for(lambda: stt.threads[0].chunker is not None)
        app.sst_server = stt
        app.settings_store = unwritable_store
        try:
            resp = app.test_client().post("/vad_threshold", json={"value": 0.055})
        finally:
            app.sst_server = previous_server
            app.settings_store = previous_store

        body = resp.get_json()
        assert resp.status_code == 200          # die Vorstellung laeuft weiter
        assert body["vad_energy_threshold"] == pytest.approx(0.055)
        assert body["persisted"] is False
        assert body["persist_error"]
        # Und der Wert gilt wirklich, nicht nur in der Antwort.
        assert stt.threads[0].chunker.energy_threshold == pytest.approx(0.055)


def test_a_write_error_on_the_gate_is_reported_the_same_way(monkeypatch, unwritable_store):
    stt = build_server(monkeypatch, mic_gate_enabled=True, mic_gate_threshold=0.002)
    previous_server, previous_store = app.sst_server, getattr(app, "settings_store", None)
    with stt:
        app.sst_server = stt
        app.settings_store = unwritable_store
        try:
            resp = app.test_client().post("/mic_gate", json={"value": 0.006})
        finally:
            app.sst_server = previous_server
            app.settings_store = previous_store

        assert resp.status_code == 200
        assert resp.get_json()["persisted"] is False
        assert stt.mic_gate.threshold == pytest.approx(0.006)


def test_a_failed_write_leaves_no_half_file_behind(store, monkeypatch):
    """os.replace bricht ab, nachdem die Nebendatei geschrieben wurde. Weder
    darf die Zieldatei entstehen noch die Nebendatei liegen bleiben."""
    import os

    def boom(a, b):
        raise OSError("Platte voll")

    monkeypatch.setattr(settings_module.os, "replace", boom)
    ok, reason = store.update(vad_energy_threshold=0.02)

    assert ok is False and "Platte voll" in reason
    assert not os.path.exists(store.path)
    assert not os.path.exists(store.path + ".tmp")


def test_a_value_lost_to_a_write_error_is_written_by_the_next_success(store, monkeypatch):
    """Kommt die Platte zurueck, soll auch die Aenderung von vorhin mit — sonst
    wird der Operator dafuer bestraft, dass er zweimal gedreht hat."""
    calls = {"n": 0}
    real_replace = settings_module.os.replace

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Platte voll")
        return real_replace(a, b)

    monkeypatch.setattr(settings_module.os, "replace", flaky)
    assert store.update(vad_energy_threshold=0.02)[0] is False
    assert store.update(mic_gate_threshold=0.004)[0] is True

    assert read(store) == {"vad_energy_threshold": 0.02, "mic_gate_threshold": 0.004}


def test_without_a_store_the_answer_says_so_instead_of_claiming_success(server):
    previous_server, previous_store = app.sst_server, getattr(app, "settings_store", None)
    app.sst_server = server
    app.settings_store = None
    try:
        body = app.test_client().post("/vad_threshold", json={"value": 0.02}).get_json()
    finally:
        app.sst_server = previous_server
        app.settings_store = previous_store

    assert body["persisted"] is False
    assert body["persist_error"]


# --- UI ---------------------------------------------------------------------

def test_operator_page_shows_whether_the_value_was_saved(client):
    """Flach mit Absicht: beweist nur, dass die Seite die Rueckmeldung noch
    ausliefert. Faellt sie weg, funktionieren die Endpunkte weiter und der
    Operator erfaehrt nichts mehr davon."""
    html = client.get("/operator").get_data(as_text=True)
    assert 'id="vad-saved"' in html
    assert 'id="gate-saved"' in html
    assert 'persisted' in html
    assert 'nicht gespeichert' in html

