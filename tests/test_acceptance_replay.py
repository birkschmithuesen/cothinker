"""Level-1 tests for the offline transcript replay (real consumer, fake STT)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.acceptance.catalogue import load_all  # noqa: E402
from demo.acceptance.replay import render_finals, run  # noqa: E402

SCEN = load_all(ROOT / "demo" / "acceptance" / "scenarios")


def test_render_finals_scenario_a_keeps_the_pause():
    finals = render_finals(SCEN["a"])
    assert finals[0]["text"] == "Hey Robo."
    assert finals[0]["type"] == "final" and finals[0]["speaker"] == "Alex"
    gap = finals[1]["t_start"] - finals[0]["t_end"]
    assert gap == pytest.approx(4.0)
    # consecutive say steps abut
    assert finals[2]["t_start"] == pytest.approx(finals[1]["t_end"])
    assert finals[0]["t_end"] - finals[0]["t_start"] == pytest.approx(2 / 2.5)


def test_render_finals_scenario_g_overlap_starts_early():
    finals = render_finals(SCEN["g"])
    assert finals[1]["t_start"] < finals[0]["t_end"]
    assert finals[1]["t_start"] == pytest.approx(finals[0]["t_end"] - 1.5)
    assert finals[1]["speaker"] == "Sam"


def test_render_finals_rejects_clip_steps():
    with pytest.raises(ValueError, match="clip"):
        render_finals(SCEN["c"])


def test_replay_scenario_h_makes_exactly_one_lane_call(tmp_path):
    obs = run(SCEN["h"], repo=ROOT, workdir=tmp_path, speed=4.0, timeout_s=50)
    assert len(obs.lane_calls) == 1
    assert "fass" in obs.lane_calls[0].lower()
    assert any("fass" in text.lower() for _, text in obs.transcript_lines)
