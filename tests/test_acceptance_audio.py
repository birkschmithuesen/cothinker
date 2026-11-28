import array
import math
import random
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo.acceptance import audio, gap_probe  # noqa: E402

RATE = 16000


def speech(seconds, rate=RATE, amp=3000, seed=1):
    """Noisy tone: loud, never a run of near-zero samples."""
    rng = random.Random(seed)
    n = int(seconds * rate)
    return array.array("h", (
        int(amp * math.sin(2 * math.pi * 220 * i / rate) + rng.randint(-200, 200)) or 7
        for i in range(n)))


def lead_noise(seconds, rate=RATE, seed=2):
    rng = random.Random(seed)
    return array.array("h", (rng.randint(-60, 60) or 5 for _ in range(int(seconds * rate))))


def clip(speech_s=60.0, lead=2.0, tail=2.0):
    return lead_noise(lead) + speech(speech_s) + lead_noise(tail, seed=3)


def with_zeros(a, start_s, dur_s, rate=RATE):
    b = array.array("h", a)
    s = int(start_s * rate)
    for i in range(s, s + int(dur_s * rate)):
        b[i] = 0
    return b


PROFILE = audio.GapProfile(p50_s=0.58, p90_s=1.90, runs_per_voiced_min=12.1)


def test_zero_run_inside_speech_is_detected():
    a = with_zeros(speech(6.0), 2.0, 0.5)
    runs = gap_probe.zero_runs(a, RATE)
    assert len(runs) == 1
    assert runs[0] == pytest.approx(0.5, abs=0.01)


def test_zero_runs_at_the_edges_are_ignored():
    a = array.array("h", [0] * RATE) + speech(3.0) + array.array("h", [0] * RATE)
    assert gap_probe.zero_runs(a, RATE) == []


def test_zero_run_next_to_silence_is_ignored():
    a = speech(2.0) + array.array("h", [0] * (RATE // 2)) + lead_noise(2.0)
    assert gap_probe.zero_runs(a, RATE) == []


def test_flanking_voiced_frames_must_be_within_the_window_of_each_other():
    def build(quiet_s):
        return (speech(1.0) + lead_noise(quiet_s) + array.array("h", [0] * (RATE // 2))
                + lead_noise(quiet_s, seed=3) + speech(1.0))
    # each neighbour is 2 s away (<= 3 s) but they are 4.5 s apart: not counted
    assert gap_probe.zero_runs(build(2.0), RATE) == []
    # neighbours 1.5 s apart: counted
    assert len(gap_probe.zero_runs(build(0.5), RATE)) == 1


def test_runs_outside_min_max_are_ignored():
    short = with_zeros(speech(6.0), 2.0, 0.1)
    long_ = with_zeros(speech(12.0), 2.0, 4.0)
    assert gap_probe.zero_runs(short, RATE) == []
    assert gap_probe.zero_runs(long_, RATE) == []


def test_stats():
    st = gap_probe.stats([0.4, 0.6, 1.0, 2.0], voiced_min=2.0)
    assert st["n"] == 4
    assert st["runs_per_voiced_min"] == pytest.approx(2.0)
    assert 0.4 <= st["p50_s"] <= 2.0 and st["p50_s"] <= st["p90_s"] <= 2.0


def test_stats_empty():
    st = gap_probe.stats([], voiced_min=1.0)
    assert st["n"] == 0 and st["runs_per_voiced_min"] == 0.0


def test_from_baseline_reads_the_entry():
    p = audio.GapProfile.from_baseline(
        {"p50_s": 0.43, "p90_s": 1.34, "runs_per_voiced_min": 11.0, "measured_by": "x"})
    assert (p.p50_s, p.p90_s, p.runs_per_voiced_min) == (0.43, 1.34, 11.0)


def test_draw_matches_quantiles_and_is_clipped():
    rng = random.Random(7)
    draws = [PROFILE.draw(rng) for _ in range(20000)]
    assert min(draws) >= 0.2 and max(draws) <= 3.0
    qs = statistics.quantiles(draws, n=10)
    assert qs[4] == pytest.approx(0.58, rel=0.06)
    assert qs[8] == pytest.approx(1.90, rel=0.08)


def test_voiced_frames_marks_speech_not_noise():
    a = clip(speech_s=3.0, lead=1.0, tail=1.0)
    v = audio.voiced_frames(a, RATE)
    assert not any(v[:10]) and all(v[10:40]) and not any(v[40:])


def test_inject_is_deterministic_and_seed_dependent():
    a = clip(30.0)
    pcm = a.tobytes()
    one, n1 = audio.inject_gaps_counted(pcm, RATE, PROFILE, 5)
    two = audio.inject_gaps(pcm, RATE, PROFILE, 5)
    assert one == two and n1 > 0
    assert audio.inject_gaps(pcm, RATE, PROFILE, 6) != one
    assert one != pcm and len(one) == len(pcm)


def test_injected_count_matches_runs_per_voiced_min():
    a = clip(60.0)
    out, n = audio.inject_gaps_counted(a.tobytes(), RATE, PROFILE, 11)
    assert n == round(PROFILE.runs_per_voiced_min * 1.0)
    runs = gap_probe.zero_runs(array.array("h", out), RATE)
    # gaps are kept two frames apart so none merge: the probe sees every one.
    assert len(runs) == n
    assert all(0.19 <= r <= 3.01 for r in runs)


def test_gaps_stay_inside_speech_and_edges_are_untouched():
    lead, tail = 2.0, 2.0
    a = clip(60.0, lead, tail)
    for seed in range(5):
        out = array.array("h")
        out.frombytes(audio.inject_gaps(a.tobytes(), RATE, PROFILE, seed))
        first = int(lead * RATE)
        last = len(a) - int(tail * RATE)
        # lead and trailing noise byte-identical, and a full voiced frame
        # of margin on each side of the speech is untouched as well
        margin = int(0.1 * RATE)
        assert out[:first + margin] == a[:first + margin]
        assert out[last - margin:] == a[last - margin:]


def test_no_gaps_without_voiced_speech():
    a = lead_noise(20.0)
    assert audio.inject_gaps(a.tobytes(), RATE, PROFILE, 1) == a.tobytes()


def test_probe_cli_prints_numbers_only(tmp_path, capsys):
    import wave
    a = with_zeros(speech(8.0), 2.0, 0.5)
    p = tmp_path / "t.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.tobytes())
    assert gap_probe.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "n=1" in out and "t.wav" in out and str(tmp_path) not in out


# ------------------------------------------------------------- scenario wiring

def _scenarios_module():
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "demo" / "acceptance" / "scenarios.py"
    spec = importlib.util.spec_from_file_location("scenarios", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scenarios"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_apply_gaps_rewrites_tracks_and_reports_evidence(tmp_path):
    from demo.acceptance import catalogue, make_tts
    sc = _scenarios_module()
    root = Path(__file__).resolve().parent.parent / "demo" / "acceptance"
    s = catalogue.load_all(root / "scenarios")["g"]
    baseline = catalogue.load_baseline(root / "baseline.yaml")
    a = make_tts.RATE
    pcm = (lead_noise(1.0, a) + speech(20.0, a) + lead_noise(1.0, a, seed=3)).tobytes()
    path = tmp_path / "alex.wav"
    make_tts.write_wav(path, pcm)
    ev = sc.apply_gaps(s, {"Alex": path}, baseline)
    n = int(ev.rsplit("injected=", 1)[1])
    assert ev.startswith(f"gaps: profile={s.gaps['profile']} seed={s.gaps['seed']} injected=")
    assert n > 0
    import wave
    with wave.open(str(path), "rb") as w:
        out = w.readframes(w.getnframes())
    assert len(out) == len(pcm) and out != pcm
    assert sc.apply_gaps(catalogue.load_all(root / "scenarios")["a"], {}, baseline) == ""


def test_apply_gaps_unknown_profile_is_an_error(tmp_path):
    from demo.acceptance import catalogue
    sc = _scenarios_module()
    root = Path(__file__).resolve().parent.parent / "demo" / "acceptance"
    s = catalogue.load_all(root / "scenarios")["g"]
    with pytest.raises(ValueError):
        sc.apply_gaps(s, {}, {"gap_profiles": {}})


def test_grade_scenario_adds_an_audio_row_with_the_gap_evidence(tmp_path, monkeypatch):
    from demo.acceptance import catalogue, oracle
    sc = _scenarios_module()
    root = Path(__file__).resolve().parent.parent / "demo" / "acceptance"
    s = catalogue.load_all(root / "scenarios")["g"]
    ev = "gaps: profile=track-a seed=3 injected=4"
    monkeypatch.setattr(sc, "run_call", lambda *a, **k: oracle.Observation(audio_evidence=ev))

    class J:
        cfg = None
    args = sc.parse_args(["--catalogue", str(root / "scenarios"), "--via", "transcript",
                          "--out", str(tmp_path)])
    args.via = "call"
    rows = sc.grade_scenario(s, args, {}, {}, J(), tmp_path)
    audio_rows = [r for r in rows if r.id == "scenario.g.audio"]
    assert len(audio_rows) == 1 and audio_rows[0].evidence == ev
