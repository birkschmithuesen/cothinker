"""The acceptance test: a synthetic WAV through a REAL recognizer.

Real `STTRecognizer`, real `samplerate` resampler, real `VadChunker`, real
`EventBus` -- only the audio device and the ASR backend are fakes, because
one needs hardware and the other needs a network. The audio is generated
here, at runtime, and written to pytest's tmp dir: no `.wav` is ever
committed, and nothing here is anybody's voice.

What it pins:
  * every `final` carries t_start/t_end within 50 ms of the known onsets,
  * the debug WAV holds exactly as many samples as the clock counted,
  * audio muted mid-session never reaches the clock.
"""
import sys
import time
import wave

import numpy as np
import pytest

# args.py runs argparse at import time; mock argv so importing sr is safe.
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]
from fundusapps.stt_server.sr import AudioSource, STTRecognizer  # noqa: E402
sys.argv = _original_argv

from fundusapps.stt_server.audio_clock import TimestampEnricher  # noqa: E402
from fundusapps.stt_server.events import (  # noqa: E402
    EventBus, TranscriptionEvent,
)

DEVICE_RATE = 44100
BLOCK = 4096                       # what AudioSource delivers per callback
TOTAL_S = 5.5
#: The two speech bursts, in seconds. Deliberately not on a frame
#: boundary: 1.013 s is 1073 native samples into the 11th block.
SPANS = [(1.013, 2.487), (3.300, 4.100)]


class FakeAudioSource(AudioSource):
    """The real AudioSource with start()/stop() replaced, so the metering
    and the fan-out under test are the production code path."""

    def start(self):
        pass

    def stop(self):
        pass

    def feed(self, samples):
        data = np.asarray(samples, dtype=np.int16).tobytes()
        self._callback(data, len(samples), 0.0, None)


class FakeSegmentedBackend:
    """A segmented backend -- the only kind that builds a VadChunker.
    Publishes one `final` per chunk, carrying the chunker's turn_id."""

    accepts_audio_offset = False

    def __init__(self, bus):
        self._bus = bus
        self.turns = []

    def wants_streaming(self):
        return False

    def process_chunk(self, audio, recognizer_id, turn_id=None):
        self.turns.append(turn_id)
        self._bus.publish(TranscriptionEvent(
            recognizer_id=recognizer_id, type="final",
            text=f"Platzhalter {len(self.turns)}", timestamp=time.time(),
            backend="whisper", turn_id=turn_id))
        return iter(())

    def close(self):
        pass


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _pcm(seed=11):
    """Silence, burst, silence, burst, silence -- hard-gated noise.

    Noise rather than a tone because the RMS decision is pure energy and
    noise has no crest factor to argue about; hard-gated because the
    resampler was measured to put a step onset through with 0.0 ms of
    delay, so the edges stay where they were put.
    """
    rng = np.random.default_rng(seed)
    x = np.zeros(int(round(TOTAL_S * DEVICE_RATE)))
    for t0, t1 in SPANS:
        a = int(round(t0 * DEVICE_RATE))
        b = int(round(t1 * DEVICE_RATE))
        x[a:b] = rng.normal(0.0, 0.25, size=b - a)
    return np.clip(x * 32767.0, -32768, 32767).astype(np.int16)


@pytest.fixture(scope="module")
def lauf(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stt-timestamps")
    quelle = tmp / "quelle.wav"
    with wave.open(str(quelle), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(DEVICE_RATE)
        w.writeframes(_pcm().tobytes())
    with wave.open(str(quelle), "rb") as r:
        pcm = np.frombuffer(r.readframes(r.getnframes()), dtype=np.int16)

    bus = EventBus()
    sub = bus.subscribe()
    enricher = TimestampEnricher()
    bus.set_enrich(enricher)
    backend = FakeSegmentedBackend(bus)
    source = FakeAudioSource(device=0, sample_rate=DEVICE_RATE,
                             num_channels=1, blocksize=BLOCK)
    debug = tmp / "debug"
    rec = STTRecognizer(
        backend=backend, recognizer_id="0", audio_source=source,
        blocksize=BLOCK, debug_audio_folder=debug,
        vad_min_silence_ms=500, vad_min_speech_ms=250,
        vad_energy_threshold=0.02, enricher=enricher,
    )
    t_before = time.time()
    rec.start()
    for i in range(0, pcm.size, BLOCK):
        source.feed(pcm[i:i + BLOCK])
    assert _wait_for(lambda: len(backend.turns) >= 2, 20.0), backend.turns
    assert _wait_for(lambda: rec.audio_queue.empty(), 20.0)
    time.sleep(0.3)                       # let any in-flight frame land
    t_after = time.time()

    # Now the muted stretch. The sleep after mute is longer than the
    # queue's own 0.2 s timeout, so the thread is certainly parked in the
    # mute branch and not blocked in a get() that a new frame would wake.
    samples_before_mute = rec.clock.samples
    rec._mute.set()
    time.sleep(0.4)
    laut = np.full(BLOCK, 8000, dtype=np.int16)
    for _ in range(11):                   # ~1.0 s at 44.1 kHz
        source.feed(laut)
    time.sleep(0.4)
    samples_after_mute = rec.clock.samples

    rec.stop()
    rec.join(timeout=10.0)

    wavs = sorted(debug.glob("*.wav"))
    assert wavs, list(debug.iterdir())
    with wave.open(str(wavs[0]), "rb") as r:
        debug_frames = r.getnframes()
        debug_rate = r.getframerate()

    finals = []
    while True:
        try:
            ev = sub.get_nowait()
        except Exception:
            break
        if ev.type == "final":
            finals.append(ev)

    return {
        "finals": finals, "t_before": t_before, "t_after": t_after,
        "clock_samples": rec.clock.samples, "debug_frames": debug_frames,
        "debug_rate": debug_rate,
        "samples_before_mute": samples_before_mute,
        "samples_after_mute": samples_after_mute,
    }


def test_every_final_carries_audio_clock_timestamps_within_50_ms(lauf):
    finals = lauf["finals"]
    assert len(finals) == 2, [(f.text, f.t_start, f.t_end) for f in finals]
    for ev, (t0, t1) in zip(finals, SPANS):
        assert ev.t_start is not None and ev.t_end is not None
        # Printed, not just asserted: docs/stt.md quotes the measured
        # deviation, and "under 50 ms" is a bound, not a measurement.
        print(f"MESSUNG t_start {ev.t_start:.3f} soll {t0:.3f} "
              f"-> {abs(ev.t_start - t0) * 1000:5.1f} ms | "
              f"t_end {ev.t_end:.3f} soll {t1:.3f} "
              f"-> {abs(ev.t_end - t1) * 1000:5.1f} ms")
        assert abs(ev.t_start - t0) <= 0.050, (ev.t_start, t0)
        assert abs(ev.t_end - t1) <= 0.050, (ev.t_end, t1)
        assert ev.audio_epoch is not None
        # Coarse by contract -- only that it is a plausible instant.
        assert lauf["t_before"] - 0.5 <= ev.audio_epoch <= lauf["t_after"]
    print(f"MESSUNG audio_epoch vs. erste Einspeisung: "
          f"{lauf['t_before'] - finals[0].audio_epoch:+.3f} s")


def test_the_debug_wav_and_the_clock_agree_sample_for_sample(lauf):
    """This equality is the whole reason the clock counts OUTPUT samples:
    a t_start is a seek position in that file, with nothing to correct."""
    assert lauf["debug_rate"] == 16000
    assert lauf["debug_frames"] == lauf["clock_samples"]


def test_muted_audio_never_reaches_the_clock(lauf):
    """A second of full-scale audio while muted -- not a sample of it is
    resampled, written or counted."""
    assert lauf["samples_after_mute"] == lauf["samples_before_mute"]
