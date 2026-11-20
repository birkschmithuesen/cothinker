"""A synthetic WAV through a REAL recognizer, with the thought-end tap on.

Real STTRecognizer, real resampler, real VadChunker, real EventBus with
the real TimestampEnricher chained before the tap -- only the device, the
ASR backend and the smart-turn worker are fakes. Pins that the ring is
fed from the same samples the clock counts, and that every final with a
span is followed by exactly one thought_end event that carries numbers
only.
"""
import json
import sys
import time

import pytest

_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]
from fundusapps.stt_server.sr import STTRecognizer  # noqa: E402
from fundusapps.stt_server import args as stt_args  # noqa: E402
sys.argv = _original_argv

from fundusapps.stt_server.audio_clock import TimestampEnricher  # noqa: E402
from fundusapps.stt_server.events import EventBus  # noqa: E402
from fundusapps.stt_server.tests.test_audio_timestamps_e2e import (  # noqa: E402
    BLOCK, DEVICE_RATE, SPANS, FakeAudioSource, FakeSegmentedBackend, _pcm,
    _wait_for)
from fundusapps.stt_server.thought_end import (  # noqa: E402
    ThoughtEndTap, chain_enrich)


class FakeClient:
    def __init__(self):
        self.seen = []

    def start(self):
        pass

    def ask(self, req_id, data):
        self.seen.append(len(data))
        return {"id": req_id, "p": 0.6, "ms": 3.0}

    def close(self):
        pass


@pytest.fixture(scope="module")
def lauf():
    bus = EventBus()
    sub = bus.subscribe()
    enricher = TimestampEnricher()
    client = FakeClient()
    tap = ThoughtEndTap(bus.publish, lambda: client)
    bus.set_enrich(chain_enrich(enricher, tap))
    tap.start()
    backend = FakeSegmentedBackend(bus)
    source = FakeAudioSource(device=0, sample_rate=DEVICE_RATE,
                             num_channels=1, blocksize=BLOCK)
    rec = STTRecognizer(
        backend=backend, recognizer_id="0", audio_source=source,
        blocksize=BLOCK, vad_min_silence_ms=500, vad_min_speech_ms=250,
        vad_energy_threshold=0.02, enricher=enricher, thought_end_tap=tap)
    rec.start()
    pcm = _pcm()
    for i in range(0, pcm.size, BLOCK):
        source.feed(pcm[i:i + BLOCK])
    assert _wait_for(lambda: len(backend.turns) >= 2, 20.0)
    assert _wait_for(lambda: len(client.seen) >= 2, 20.0), client.seen
    time.sleep(0.3)
    rec.stop()
    rec.join(timeout=10.0)
    tap.close()
    events = []
    while True:
        try:
            events.append(sub.get_nowait())
        except Exception:
            break
    return {"events": events, "rec": rec, "client": client}


def test_every_final_with_a_span_gets_one_thought_end(lauf):
    finals = [e for e in lauf["events"] if e.type == "final"]
    ends = [e for e in lauf["events"] if e.type == "thought_end"]
    assert len(finals) == 2 and len(ends) == 2
    for fin, end in zip(finals, ends):
        assert end.turn_id == fin.turn_id and end.t_end == fin.t_end
        assert end.p == 0.6 and end.error is None


def test_the_window_is_the_turn_plus_the_stop_time(lauf):
    for (t0, t1), n_bytes in zip(SPANS, lauf["client"].seen):
        assert abs(n_bytes / 2 / 16000 - (t1 + 0.2 - t0)) <= 0.06


def test_thought_end_events_carry_no_text(lauf):
    for e in lauf["events"]:
        if e.type == "thought_end":
            d = e.to_dict()
            assert "text" not in d
            assert "Platzhalter" not in json.dumps(d)


def test_the_ring_saw_every_sample_the_clock_counted(lauf):
    rec = lauf["rec"]
    assert rec.ring is not None and rec.ring.end == rec.clock.samples


def test_without_a_tap_there_is_no_ring():
    bus = EventBus()
    source = FakeAudioSource(device=0, sample_rate=DEVICE_RATE,
                             num_channels=1, blocksize=BLOCK)
    rec = STTRecognizer(backend=FakeSegmentedBackend(bus), recognizer_id="0",
                        audio_source=source, blocksize=BLOCK)
    assert rec.ring is None


def test_the_switch_is_off_by_default():
    a = stt_args.parser.parse_args(["vosk"])
    assert a.thought_end == "off"
    a = stt_args.parser.parse_args(["--thought-end", "smart_turn", "vosk"])
    assert a.thought_end == "smart_turn"
