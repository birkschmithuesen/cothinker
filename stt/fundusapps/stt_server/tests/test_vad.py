import numpy as np
from fundusapps.stt_server.vad import VadChunker

SR = 16000


def _silence(ms: int) -> np.ndarray:
    n = int(SR * ms / 1000)
    return np.zeros(n, dtype=np.int16)


def _tone(ms: int, amp: float = 0.2) -> np.ndarray:
    n = int(SR * ms / 1000)
    t = np.arange(n) / SR
    samples = (np.sin(2 * np.pi * 440 * t) * amp * 32767).astype(np.int16)
    return samples


def test_pure_silence_yields_no_chunks():
    chunker = VadChunker(
        sample_rate=SR, min_silence_ms=500, min_speech_ms=250, energy_threshold=0.01
    )
    out = []
    for frame in [_silence(100)] * 30:
        out.extend(chunker.feed(frame))
    out.extend(chunker.flush())
    assert out == []


def test_speech_then_silence_emits_one_chunk():
    chunker = VadChunker(
        sample_rate=SR, min_silence_ms=500, min_speech_ms=250, energy_threshold=0.01
    )
    out = []
    for frame in [_tone(100)] * 8:           # 800 ms of "speech"
        out.extend(chunker.feed(frame))
    for frame in [_silence(100)] * 8:        # 800 ms of silence -> end-of-phrase
        out.extend(chunker.feed(frame))
    assert len(out) == 1
    chunk = out[0]
    assert isinstance(chunk, np.ndarray)
    assert chunk.dtype == np.int16
    # chunk should be at least the speech length we fed
    assert len(chunk) >= int(SR * 0.5)


def test_too_short_speech_is_dropped():
    chunker = VadChunker(
        sample_rate=SR, min_silence_ms=300, min_speech_ms=500, energy_threshold=0.01
    )
    out = []
    for frame in [_tone(100)] * 2:           # 200 ms speech, below min_speech_ms
        out.extend(chunker.feed(frame))
    for frame in [_silence(100)] * 5:
        out.extend(chunker.feed(frame))
    assert out == []


def test_status_callbacks_fire_on_speech_start_and_chunk_submitted():
    events = []
    chunker = VadChunker(
        sample_rate=SR, min_silence_ms=300, min_speech_ms=100, energy_threshold=0.01,
        on_speech_start=lambda: events.append("start"),
        on_chunk_submitted=lambda audio, turn_id: events.append(("chunk", len(audio))),
    )
    for frame in [_tone(100)] * 3:
        chunker.feed(frame)
    for frame in [_silence(100)] * 5:
        chunker.feed(frame)
    assert events[0] == "start"
    assert events[-1][0] == "chunk"


import logging
import re


def _speech_then_silence(sample_rate=16000, speech_ms=300, silence_ms=600):
    n_speech = int(sample_rate * speech_ms / 1000)
    n_silence = int(sample_rate * silence_ms / 1000)
    speech = (np.random.randn(n_speech) * 5000).astype(np.int16)
    silence = np.zeros(n_silence, dtype=np.int16)
    return np.concatenate([speech, silence])


def test_vad_emits_vad_speech_end_with_ulid_and_threads_turn_id(caplog):
    """When VadChunker finalizes a chunk it must:
       1. emit a [LATENCY phase=vad_speech_end turn=<ULID>] log line, and
       2. call on_chunk_submitted with (chunk, turn_id) so downstream
          components can carry the same id forward."""
    captured = []

    def _cb(chunk, turn_id):
        captured.append((len(chunk), turn_id))

    chunker = VadChunker(
        sample_rate=16000,
        min_silence_ms=500,
        min_speech_ms=250,
        energy_threshold=0.01,
        on_chunk_submitted=_cb,
    )

    samples = _speech_then_silence()
    with caplog.at_level(logging.INFO):
        # Feed in 8000-sample blocks (≈500 ms).
        for start in range(0, len(samples), 8000):
            chunker.feed(samples[start:start + 8000])
        # Flush to finalize any pending speech at end of stream.
        chunker.flush()

    assert len(captured) == 1
    _, turn_id = captured[0]
    # Must be a 26-char Crockford-base32 ULID.
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", turn_id), turn_id

    lat = [r.getMessage() for r in caplog.records if "[LATENCY" in r.getMessage()]
    speech_end_lines = [m for m in lat if "phase=vad_speech_end" in m and f"turn={turn_id}" in m]
    assert len(speech_end_lines) == 1, lat
