import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ACC = Path(__file__).resolve().parent.parent / "demo" / "acceptance"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ACC / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_sentence_count_has_unique_keywords():
    tts = _load("make_tts")
    n = 2 * 24
    sents = tts.sentences(n)
    assert len({kw for _, kw in sents}) == n
    assert all(kw in text for text, kw in sents)


def test_too_many_sentences_is_a_clear_error(tmp_path):
    tts = _load("make_tts")
    with pytest.raises(ValueError, match="at most"):
        tts.sentences(len(tts.KEYWORDS) + 1)
    with pytest.raises(SystemExit) as e:
        tts.main(["--out-dir", str(tmp_path), "--per-speaker", "99"])
    assert e.value.code == 2


def _fake_espeak(tmp: Path, seconds: float) -> Path:
    script = tmp / "fake-espeak"
    script.write_text(
        "#!%s\nimport sys, wave\n"
        "out = sys.argv[sys.argv.index('-w') + 1]\n"
        "w = wave.open(out, 'wb'); w.setnchannels(1); w.setsampwidth(2)\n"
        "w.setframerate(22050); w.writeframes(b'\\0\\0' * int(22050 * %r))\n"
        "w.close()\n" % (sys.executable, seconds))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _ffmpeg():
    from shutil import which
    f = which("ffmpeg")
    if not f:
        pytest.skip("ffmpeg not available")
    return f


def test_default_run_gives_at_least_55s_speech_per_speaker(tmp_path):
    ff = _ffmpeg()
    fake = _fake_espeak(tmp_path, 2.5)  # nominal espeak sentence length
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(ACC / "make_tts.py"),
                        "--out-dir", str(out), "--espeak", str(fake),
                        "--ffmpeg", ff], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = dict(p.split("=") for p in r.stdout.split())
    assert float(line["alex_speech_s"]) >= 55
    assert float(line["sam_speech_s"]) >= 55
    assert len(json.loads((out / "script.json").read_text())["events"]) == 48


def test_short_speech_fails(tmp_path):
    ff = _ffmpeg()
    fake = _fake_espeak(tmp_path, 1.0)
    r = subprocess.run([sys.executable, str(ACC / "make_tts.py"),
                        "--out-dir", str(tmp_path / "o"), "--espeak",
                        str(fake), "--ffmpeg", ff],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "need >=" in r.stderr


def test_participant_requires_cdp_port():
    pytest.importorskip("websocket")
    p = _load("participant")
    with pytest.raises(SystemExit) as e:
        p.main(["--url-file", "x", "--name", "A", "--wav", "w"])
    assert e.value.code == 2


def test_participant_accepts_stats_out(tmp_path):
    pytest.importorskip("websocket")
    p = _load("participant")
    a = p.build_arg_parser().parse_args(
        ["--url-file", "x", "--name", "A", "--wav", "w", "--cdp-port", "9301",
         "--stats-out", str(tmp_path / "stats.json")])
    assert a.stats_out == str(tmp_path / "stats.json")
    assert a.stats_timeout == 15


def test_gather_stats_script_awaits_every_captured_peer_connection():
    p = _load("participant")
    assert "window.__pcs" in p.CAPTURE_PEER_CONNECTIONS_SCRIPT
    assert "getStats" in p.GATHER_STATS_SCRIPT
