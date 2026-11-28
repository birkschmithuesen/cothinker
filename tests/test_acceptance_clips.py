import importlib
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ROOT / "demo" / "acceptance" / "clips"


@pytest.fixture
def sc():
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("demo.acceptance.scenarios")
    finally:
        sys.path.remove(str(ROOT))


def test_every_clip_has_reference_and_licence_row():
    sources = (CLIPS / "SOURCES.md").read_text(encoding="utf-8")
    third = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    clips = sorted(CLIPS.glob("*.opus"))
    assert clips
    for c in clips:
        assert (CLIPS / f"{c.stem}.ref.txt").read_text(encoding="utf-8").strip(), c.stem
        assert f"| {c.stem} |" in sources, c.stem
    assert "Speech clips for the acceptance run" in third


def test_clip_budget():
    total = sum(p.stat().st_size for p in CLIPS.glob("*.opus"))
    assert total <= 3 * 1024 * 1024, total


def test_scenario_c_points_at_a_committed_clip():
    text = (ROOT / "demo" / "acceptance" / "scenarios" / "c.yaml").read_text(encoding="utf-8")
    assert "clip:de-parl-01" in text
    assert (CLIPS / "de-parl-01.opus").is_file()


def test_clip_step_decodes_committed_opus_via_injected_decoder(sc, tmp_path):
    seen = {}

    def decode(raw, ffmpeg, norm):
        seen["args"] = (Path(raw), ffmpeg, Path(norm))
        return b"\x01\x00" * 8

    pcm = sc.clip_pcm("de-parl-01", None, "ffmpeg-x", tmp_path, decode=decode)
    assert pcm == b"\x01\x00" * 8
    raw, ffmpeg, norm = seen["args"]
    assert raw == CLIPS / "de-parl-01.opus"
    assert ffmpeg == "ffmpeg-x"
    assert norm.parent == tmp_path


def test_audio_dir_wav_overrides_the_committed_clip(sc, tmp_path):
    with wave.open(str(tmp_path / "de-parl-01.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<4h", 1, 2, 3, 4))

    def decode(*a):
        raise AssertionError("private clip must win over the committed one")

    pcm = sc.clip_pcm("de-parl-01", str(tmp_path), "ffmpeg", tmp_path, decode=decode)
    assert pcm == struct.pack("<4h", 1, 2, 3, 4)


def test_audio_dir_only_clip_still_works_and_missing_clip_is_reported(sc, tmp_path):
    with wave.open(str(tmp_path / "private-x.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x05\x00")
    assert sc.clip_pcm("private-x", str(tmp_path), "ffmpeg", tmp_path) == b"\x05\x00"
    with pytest.raises(FileNotFoundError, match="nope"):
        sc.clip_pcm("nope", str(tmp_path), "ffmpeg", tmp_path)
