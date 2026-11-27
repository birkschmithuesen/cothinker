"""Issue #202's retroactive tool: re-derive the right speaker for lines
already written during an STT-restart clock-offset window."""
import json
import struct
import wave
from datetime import datetime, timezone

import pytest

from fundusapps.bbb_tap.reattribute import (
    apply_corrections, load_tracks, main, parse_protokoll_lines, reattribute,
)

RATE = 16000


def _write_wav(path, pcm):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def _loud(n, value=6000):
    return b"".join(struct.pack("<h", value) for _ in range(n))


def _silence(n):
    return b"\x00\x00" * n


def _write_sidecar(path, tracks, start_epoch):
    path.write_text(json.dumps({"mixed": "bbb-1.wav", "sample_rate": RATE,
                                "start_epoch": start_epoch, "tracks": tracks}),
                     encoding="utf-8")


def test_load_tracks_reads_the_sidecar_and_full_wavs(tmp_path):
    sidecar = tmp_path / "bbb-1-tracks.json"
    _write_sidecar(sidecar, {
        "1": {"file": "bbb-1-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0}]},
    }, start_epoch=1000.0)
    _write_wav(tmp_path / "bbb-1-trk1.wav", _loud(1600))

    tracks, owners_by_track, tap_start_epoch = load_tracks(sidecar)
    assert tap_start_epoch == 1000.0
    assert owners_by_track[1][0]["display_name"] == "Anna"
    assert tracks[1].energy_between(0.0, 0.1) > 0.0


def test_parse_protokoll_lines_keeps_only_raw_transcript_lines(tmp_path):
    protokoll = tmp_path / "protokoll.md"
    protokoll.write_text(
        "# Session\n"
        "## Entscheidungen\n"
        "- something else entirely\n"
        "## Rohtranskript\n"
        "- **10:00** *S1:* eins zwei\n"
        "- **10:01** drei vier\n",
        encoding="utf-8",
    )
    lines = parse_protokoll_lines(protokoll.read_text(encoding="utf-8"))
    assert [(l["stempel"], l["speaker"], l["text"]) for l in lines] == [
        ("10:00", "S1", "eins zwei"),
        ("10:01", None, "drei vier"),
    ]


def test_reattribute_corrects_only_the_lines_that_actually_changed():
    timing_rows = [
        {"at": "2026-01-01T00:00:00.100+00:00", "recognizer_id": "S1", "chars": 9,
         "cut": None, "t_start": 0.0, "t_end": 0.1},
        {"at": "2026-01-01T00:00:30.000+00:00", "recognizer_id": "S1", "chars": 0,
         "cut": "pause"},                                  # not an utterance
        {"at": "2026-01-01T00:00:35.100+00:00", "recognizer_id": "S1", "chars": 9,
         "cut": None, "t_start": 0.0, "t_end": 0.1},
    ]
    raw_lines = [
        # Already correctly attributed (track 1/Anna carries it) -- no change expected.
        {"line_no": 0, "stempel": "00:00", "speaker": "Anna", "text": "eins zwei"},
        # Written during the outage as the raw "S1" label -- track 2/Ben actually carries it.
        {"line_no": 1, "stempel": "00:35", "speaker": "S1", "text": "drei vier"},
    ]
    tap_epoch = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()

    class FakeEnergy:
        def __init__(self, loud_at):
            self.loud_at = loud_at

        def energy_between(self, start_s, end_s):
            lo, hi = self.loud_at
            return 1.0 if start_s < hi and end_s > lo else 0.0

    tracks = {1: FakeEnergy((0.0, 0.1)), 2: FakeEnergy((35.0, 35.1))}
    owners_by_track = {1: [{"display_name": "Anna", "since": 0.0}],
                       2: [{"display_name": "Ben", "since": 0.0}]}

    corrections = reattribute(timing_rows, raw_lines, tracks, owners_by_track, tap_epoch)
    assert len(corrections) == 1
    assert corrections[0]["line_no"] == 1
    assert corrections[0]["old_speaker"] == "S1"
    assert corrections[0]["new_speaker"] == "Ben"


def test_reattribute_raises_on_a_count_mismatch_instead_of_guessing():
    timing_rows = [{"at": "2026-01-01T00:00:00.000+00:00", "recognizer_id": "S1",
                    "chars": 1, "cut": None, "t_start": 0.0, "t_end": 0.1}]
    raw_lines = []
    with pytest.raises(ValueError):
        reattribute(timing_rows, raw_lines, {}, {}, 1000.0)


def test_apply_corrections_rewrites_only_the_speaker_of_changed_lines():
    text = ("## Rohtranskript\n"
            "- **00:00** *S1:* eins zwei\n"
            "- **00:35** *S1:* drei vier\n")
    corrections = [{"line_no": 2, "stempel": "00:35", "text": "drei vier",
                    "old_speaker": "S1", "new_speaker": "Ben"}]
    out = apply_corrections(text, corrections)
    assert out.splitlines() == [
        "## Rohtranskript",
        "- **00:00** *S1:* eins zwei",
        "- **00:35** *Ben:* drei vier",
    ]


def test_main_end_to_end_writes_a_corrected_file(tmp_path, capsys):
    sidecar = tmp_path / "bbb-1-tracks.json"
    tap_epoch = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    _write_sidecar(sidecar, {
        "1": {"file": "bbb-1-trk1.wav", "owners": [{"display_name": "Anna", "since": 0.0}]},
        "2": {"file": "bbb-1-trk2.wav", "owners": [{"display_name": "Ben", "since": 0.0}]},
    }, start_epoch=tap_epoch)
    _write_wav(tmp_path / "bbb-1-trk1.wav", _silence(35 * RATE) + _loud(1600))
    _write_wav(tmp_path / "bbb-1-trk2.wav", _loud(1600) + _silence(35 * RATE))

    timing = tmp_path / "timing.jsonl"
    timing.write_text(
        '{"at": "2026-01-01T00:00:35.100+00:00", "recognizer_id": "S1", '
        '"chars": 9, "cut": null, "t_start": 0.0, "t_end": 0.1}\n',
        encoding="utf-8")

    protokoll = tmp_path / "protokoll.md"
    protokoll.write_text("## Rohtranskript\n- **00:35** *S1:* drei vier\n", encoding="utf-8")

    out_path = tmp_path / "protokoll-corrected.md"
    rc = main(["--sidecar", str(sidecar), "--timing", str(timing),
              "--protokoll", str(protokoll), "--out", str(out_path)])
    assert rc == 0
    # Track 1/Anna, not track 2/Ben, carries the energy 35.0-35.1s into
    # the tap recording (see the WAVs above) -- the raw "S1" label was
    # the outage-era artifact this tool exists to fix.
    assert out_path.read_text(encoding="utf-8").splitlines()[-1] == "- **00:35** *Anna:* drei vier"
    # The input file is never touched.
    assert protokoll.read_text(encoding="utf-8").splitlines()[-1] == "- **00:35** *S1:* drei vier"
    out = capsys.readouterr().out
    assert "Anna" in out


def test_main_reports_nothing_to_do_without_a_start_epoch(tmp_path, capsys):
    sidecar = tmp_path / "bbb-1-tracks.json"
    sidecar.write_text(json.dumps({"mixed": "bbb-1.wav", "sample_rate": RATE, "tracks": {}}),
                       encoding="utf-8")
    timing = tmp_path / "timing.jsonl"
    timing.write_text("", encoding="utf-8")
    protokoll = tmp_path / "protokoll.md"
    protokoll.write_text("## Rohtranskript\n", encoding="utf-8")

    rc = main(["--sidecar", str(sidecar), "--timing", str(timing), "--protokoll", str(protokoll)])
    assert rc == 1
