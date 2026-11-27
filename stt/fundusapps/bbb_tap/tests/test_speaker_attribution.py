"""Acceptance test (issue #128): three synthetic tracks, known talk
spans, >=95% of recognised words attributed to the right participant."""
import numpy as np

from fundusapps.bbb_tap.speaker_attribution import (TrackEnergy,
                                                      attribute_by_wall_clock,
                                                      attribute_word,
                                                      resolve_speaker)

RATE = 16000
CHUNK = 2048  # same frame size the tap's hook.js uses

TALK_SPANS = [("Anna", 0.0, 10.0), ("Ben", 10.0, 20.0), ("Chris", 20.0, 30.0)]
TRACK_OF = {"Anna": 1, "Ben": 2, "Chris": 3}


def _track_pcm(name, duration_s, talk_spans, rng):
    """One track's full-duration audio: a low noise floor everywhere
    (cross-talk bleed, room noise) plus a loud voice-like signal during
    this participant's own talk span(s)."""
    n = int(duration_s * RATE)
    t = np.arange(n) / RATE
    noise = rng.normal(0, 150, n)
    voice = np.zeros(n)
    for speaker, start, end in talk_spans:
        if speaker != name:
            continue
        mask = (t >= start) & (t < end)
        voice[mask] = 6000 * np.sin(2 * np.pi * 180 * t[mask])
    pcm = np.clip(noise + voice, -32768, 32767).astype("<i2")
    return pcm.tobytes()


def _build_track_energy(pcm_bytes):
    energy = TrackEnergy()
    step = CHUNK * 2  # bytes per chunk
    for i in range(0, len(pcm_bytes), step):
        start_frame = i // 2
        energy.add_chunk(start_frame, pcm_bytes[i:i + step])
    return energy


def _words_for(duration_s, word_len=0.4, gap=0.1):
    spans = []
    t = 0.0
    while t + word_len <= duration_s:
        spans.append((t, t + word_len))
        t += word_len + gap
    return spans


def test_three_synthetic_tracks_known_spans_attribute_at_least_95_percent():
    rng = np.random.default_rng(42)
    duration = 30.0
    tracks = {}
    owners_by_track = {}
    for name, n in TRACK_OF.items():
        pcm = _track_pcm(name, duration, TALK_SPANS, rng)
        tracks[n] = _build_track_energy(pcm)
        owners_by_track[n] = [{"participant_id": f"p{n}", "display_name": name, "since": 0.0}]

    total = correct = 0
    for speaker, start, end in TALK_SPANS:
        for t_start, t_end in _words_for(end - start):
            t_start, t_end = t_start + start, t_end + start
            got = attribute_word(tracks, owners_by_track, t_start, t_end)
            total += 1
            correct += got == speaker

    accuracy = correct / total
    assert accuracy >= 0.95, f"only {accuracy:.3f} of {total} words attributed correctly"


def test_attribute_word_returns_none_when_no_track_has_energy():
    tracks = {1: TrackEnergy(), 2: TrackEnergy()}
    assert attribute_word(tracks, {}, 0.0, 1.0) is None


def test_attribute_by_wall_clock_converts_to_the_tap_frame_axis():
    # Issue #202's retroactive tool: a line's wall-clock instant ("at")
    # plus bbb_tap's own start_epoch, not the STT clock span that was
    # wrong at the time, is what this re-derives the speaker from.
    tracks = {1: TrackEnergy(), 2: TrackEnergy()}
    tracks[1].add_chunk(30 * RATE, b"\x00\x18" * 1600)   # loud 30.0-30.1s
    tracks[2].add_chunk(0, b"\x00\x18" * 1600)            # loud 0.0-0.1s
    owners_by_track = {1: [{"display_name": "Anna", "since": 0.0}],
                       2: [{"display_name": "Ben", "since": 0.0}]}
    tap_start_epoch = 1000.0
    at = 1030.1                                           # 30.1s into the tap
    got = attribute_by_wall_clock(tracks, owners_by_track, at,
                                   duration=0.1, tap_start_epoch=tap_start_epoch)
    assert got == "Anna"


def test_attribute_by_wall_clock_returns_none_when_no_track_has_energy():
    tracks = {1: TrackEnergy()}
    assert attribute_by_wall_clock(tracks, {}, at=1000.0, duration=0.1,
                                    tap_start_epoch=1000.0) is None


def test_resolve_speaker_follows_owner_changes_over_time():
    owners = [{"display_name": "Anna", "since": 0.0},
              {"display_name": "Ben", "since": 30.0}]
    assert resolve_speaker(owners, 10.0) == "Anna"
    assert resolve_speaker(owners, 30.0) == "Ben"
    assert resolve_speaker(owners, 45.0) == "Ben"
    assert resolve_speaker([], 10.0) is None
