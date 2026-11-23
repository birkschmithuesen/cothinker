"""Issue #128 live wiring: a speaker_resolver built from a running
bbb_tap --per-track session's on-disk output (the -tracks.json sidecar
plus each track's own growing WAV file)."""
import json
import struct
from types import SimpleNamespace

from fundusapps.stt_server.track_speaker_resolver import (
    TrackSpeakerResolver, build_speaker_resolver,
)

RATE = 16000


def _wav_header(data_bytes, rate=RATE):
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", data_bytes))


def _write_wav(path, pcm):
    path.write_bytes(_wav_header(len(pcm)) + pcm)


def _append_pcm(path, pcm):
    old = path.read_bytes()
    body = old[44:] + pcm
    path.write_bytes(_wav_header(len(body)) + body)


def _write_sidecar(path, tracks):
    path.write_text(json.dumps({"mixed": "bbb-1.wav", "sample_rate": RATE,
                                "tracks": tracks}), encoding="utf-8")


def _loud(n, value=6000):
    return (b"".join(struct.pack("<h", value) for _ in range(n)))


def _silence(n):
    return b"\x00\x00" * n


def test_returns_none_when_sidecar_is_missing(tmp_path):
    resolver = TrackSpeakerResolver(tmp_path / "missing-tracks.json")
    assert resolver(0.0, 1.0) is None


def test_attributes_to_the_louder_track_using_its_current_owner(tmp_path):
    sidecar = tmp_path / "bbb-1-tracks.json"
    _write_sidecar(sidecar, {
        "1": {"file": "bbb-1-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0}]},
        "2": {"file": "bbb-1-trk2.wav", "owners": [{"display_name": "Ben", "since": 0.0}]},
    })
    _write_wav(tmp_path / "bbb-1-trk1.wav", _loud(1600))      # 0.1 s loud
    _write_wav(tmp_path / "bbb-1-trk2.wav", _silence(1600))

    resolver = TrackSpeakerResolver(sidecar, reload_every_s=0.0)
    assert resolver(0.0, 0.1) == "Anna"


def test_picks_up_appended_pcm_across_calls(tmp_path):
    sidecar = tmp_path / "bbb-2-tracks.json"
    _write_sidecar(sidecar, {
        "1": {"file": "bbb-2-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0}]},
    })
    _write_wav(tmp_path / "bbb-2-trk1.wav", _silence(1600))

    resolver = TrackSpeakerResolver(sidecar, reload_every_s=0.0)
    assert resolver(0.1, 0.2) is None          # nothing loud there yet

    _append_pcm(tmp_path / "bbb-2-trk1.wav", _loud(1600))     # frames 1600..3199
    assert resolver(0.1, 0.2) == "Anna"         # the newly appended 0.1-0.2s window


def test_owner_change_in_the_sidecar_is_picked_up_on_reload(tmp_path):
    sidecar = tmp_path / "bbb-3-tracks.json"
    _write_sidecar(sidecar, {
        "1": {"file": "bbb-3-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0}]},
    })
    _write_wav(tmp_path / "bbb-3-trk1.wav", _loud(1600))

    resolver = TrackSpeakerResolver(sidecar, reload_every_s=0.0)
    assert resolver(0.0, 0.1) == "Anna"

    _write_sidecar(sidecar, {
        "1": {"file": "bbb-3-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0},
                                                   {"display_name": "Ben B.", "since": 0.0}]},
    })
    assert resolver(0.0, 0.1) == "Ben B."


def test_reload_is_throttled_between_calls(tmp_path):
    sidecar = tmp_path / "bbb-4-tracks.json"
    _write_sidecar(sidecar, {
        "1": {"file": "bbb-4-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0}]},
    })
    _write_wav(tmp_path / "bbb-4-trk1.wav", _loud(1600))

    clock = {"t": 0.0}
    resolver = TrackSpeakerResolver(sidecar, reload_every_s=5.0, monotonic=lambda: clock["t"])
    assert resolver(0.0, 0.1) == "Anna"

    _write_sidecar(sidecar, {
        "1": {"file": "bbb-4-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0},
                                                   {"display_name": "Ben B.", "since": 0.0}]},
    })
    clock["t"] = 1.0                             # still inside the 5s throttle window
    assert resolver(0.0, 0.1) == "Anna"          # served from the already-loaded state

    clock["t"] = 10.0                            # past the throttle window
    assert resolver(0.0, 0.1) == "Ben B."        # now it reloads and sees the rename


def test_build_speaker_resolver_is_none_without_the_flag():
    assert build_speaker_resolver(SimpleNamespace()) is None
    assert build_speaker_resolver(SimpleNamespace(per_track_sidecar="")) is None


def test_build_speaker_resolver_builds_one_when_the_flag_is_set(tmp_path):
    sidecar = tmp_path / "bbb-5-tracks.json"
    _write_sidecar(sidecar, {})
    resolver = build_speaker_resolver(SimpleNamespace(per_track_sidecar=str(sidecar)))
    assert isinstance(resolver, TrackSpeakerResolver)
