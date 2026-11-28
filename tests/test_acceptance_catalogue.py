"""Level-1 tests for the level-4 scenario catalogue loader and baseline file."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.acceptance import catalogue  # noqa: E402
from demo.acceptance.catalogue import (  # noqa: E402
    CatalogueError,
    MissingThreshold,
    load_all,
    load_baseline,
    required_thresholds,
    resolve,
)

SCEN_DIR = ROOT / "demo" / "acceptance" / "scenarios"
BASELINE = ROOT / "demo" / "acceptance" / "baseline.yaml"

MINIMAL = {
    "id": "z",
    "title": "Minimal",
    "request_class": "overlap",
    "speakers": [{"name": "Alex", "voice": "v1"}],
    "timeline": [{"speaker": "Alex", "say": "Hallo."}],
    "expect": {"lane_calls": {"count": 1}},
    "timeouts": {"answer_s": 30, "scenario_s": 120},
}


def _write(d: Path, name: str, data: dict) -> None:
    (d / name).write_text(yaml.safe_dump(data), encoding="utf-8")


def _mut(**changes):
    data = copy.deepcopy(MINIMAL)
    for key, val in changes.items():
        if val is _DROP:
            data.pop(key)
        else:
            data[key] = val
    return data


_DROP = object()


def test_load_all_finds_eight_scenarios():
    scen = load_all(SCEN_DIR)
    assert sorted(scen) == list("abcdefgh")
    assert scen["h"].regression_only is True
    assert scen["a"].regression_only is False


def test_non_regression_classes_cover_seven_real_classes():
    scen = load_all(SCEN_DIR)
    classes = {s.request_class for s in scen.values() if not s.regression_only}
    assert classes == {
        "order-with-pause",
        "answer-from-files",
        "natural-segmentation",
        "wake-never-topic",
        "structure-a-list",
        "interruption",
        "overlap",
    }
    assert set(classes) <= set(catalogue.REQUEST_CLASSES)


def test_catalogue_uses_only_placeholder_speakers_and_f_g_name_both():
    scen = load_all(SCEN_DIR)
    for s in scen.values():
        assert {sp["name"] for sp in s.speakers} <= {"Alex", "Sam"}
    for sid in "fg":
        assert set(scen[sid].expect["transcript"]["speakers_all"]) == {"Alex", "Sam"}
    assert any("overlap_prev_s" in st for st in scen["g"].timeline)


def test_scenario_c_committed_clip_and_threshold():
    c = load_all(SCEN_DIR)["c"]
    assert any(sp["voice"] == "clip:de-parl-01" for sp in c.speakers)
    assert c.expect["transcript"]["mid_sentence_max"] == "baseline:scenario.share_no_sentence_end"
    assert required_thresholds(load_all(SCEN_DIR).values()) == {
        "baseline:scenario.share_no_sentence_end"
    }


def test_minimal_valid_scenario_loads(tmp_path):
    _write(tmp_path, "z.yaml", MINIMAL)
    s = load_all(tmp_path)["z"]
    assert s.regression_only is False
    assert s.gaps is None and s.judge is None
    assert s.timeline[0]["say"] == "Hallo."


def test_unknown_top_level_key_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(bogus=1))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


@pytest.mark.parametrize("key", ["id", "title", "request_class", "speakers", "timeline", "expect", "timeouts"])
def test_missing_required_key_rejected(tmp_path, key):
    _write(tmp_path, "z.yaml", _mut(**{key: _DROP}))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_unknown_request_class_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(request_class="nope"))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_timeline_step_without_say_pause_clip_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(timeline=[{"speaker": "Alex"}]))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_timeline_step_with_two_kinds_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(timeline=[{"speaker": "Alex", "say": "x", "pause": 1}]))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_timeline_unknown_speaker_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(timeline=[{"speaker": "Nobody", "say": "x"}]))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_unknown_expect_group_or_subkey_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(expect={"nonsense": {}}))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)
    _write(tmp_path, "z.yaml", _mut(expect={"lane_calls": {"cuont": 1}}))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_empty_expect_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(expect={}))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_bad_timeouts_and_judge_rejected(tmp_path):
    _write(tmp_path, "z.yaml", _mut(timeouts={"answer_s": 30}))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)
    _write(tmp_path, "z.yaml", _mut(judge={"q": "x"}))
    with pytest.raises(CatalogueError):
        load_all(tmp_path)


def test_duplicate_id_across_files_rejected(tmp_path):
    _write(tmp_path, "one.yaml", MINIMAL)
    _write(tmp_path, "two.yaml", MINIMAL)
    with pytest.raises(CatalogueError, match="duplicate"):
        load_all(tmp_path)


def test_resolve_passthrough_and_baseline():
    base = {"thresholds": {"x.y": 0.5}}
    assert resolve(3, base, {}) == 3
    assert resolve("plain", base, {}) == "plain"
    assert resolve("baseline:x.y", base, {}) == 0.5


def test_resolve_missing_threshold_and_override():
    base = {"thresholds": {}}
    with pytest.raises(MissingThreshold) as ei:
        resolve("baseline:a.b", base, {})
    assert ei.value.key == "a.b"
    assert resolve("baseline:a.b", base, {"a.b": 0.3}) == 0.3


def test_required_thresholds_collects_nested(tmp_path):
    data = _mut(expect={"transcript": {"mid_sentence_max": "baseline:k.one"},
                        "answer": {"keywords_all": ["baseline:k.two"]}})
    _write(tmp_path, "z.yaml", data)
    assert required_thresholds(load_all(tmp_path).values()) == {"baseline:k.one", "baseline:k.two"}


def test_baseline_file_shape_and_measured_by():
    base = load_baseline(BASELINE)
    assert base["thresholds"] == {} and base["stt"] == {} and base["measurements"] == []
    a = base["gap_profiles"]["track-a"]
    b = base["gap_profiles"]["track-b"]
    assert (a["p50_s"], a["p90_s"], a["runs_per_voiced_min"]) == (0.58, 1.90, 12.1)
    assert (b["p50_s"], b["p90_s"], b["runs_per_voiced_min"]) == (0.43, 1.34, 11.0)
    for prof in base["gap_profiles"].values():
        assert "M4" in prof["measured_by"]
    for t in base["thresholds"].values():
        assert t["measured_by"]


def test_every_scenario_gap_profile_exists_in_baseline():
    base = load_baseline(BASELINE)
    for s in load_all(SCEN_DIR).values():
        if s.gaps:
            assert s.gaps["profile"] in base["gap_profiles"]
