"""Level-1 tests for the level-4 mechanical oracle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.acceptance.catalogue import MissingThreshold, Scenario  # noqa: E402
from demo.acceptance.oracle import Observation, grade, share_no_sentence_end  # noqa: E402

BASELINE = {"thresholds": {}}


def scen(expect: dict) -> Scenario:
    return Scenario(
        id="z", title="Z", request_class="order-with-pause", regression_only=False,
        speakers=({"name": "Alex", "voice": "v"},), timeline=({"speaker": "Alex", "say": "x"},),
        gaps=None, expect=expect, judge=None, timeouts={"answer_s": 10, "scenario_s": 60},
    )


def obs(**kw) -> Observation:
    base = dict(lane_calls=[], answers=[], topics=[], transcript_lines=[], agenda_lines=[], mode="call")
    base.update(kw)
    return Observation(**base)


def by_id(checks):
    return {c.id: c for c in checks}


ORDER = scen({"lane_calls": {"count": 1, "keywords_all": ["Infrastruktur", "Server"]}})


def test_failure2_wake_block_only_lane_call_fails():
    o = obs(mode="transcript", lane_calls=["Hey Robo."])
    c = by_id(grade(ORDER, o, BASELINE, {}))["scenario.z.lane_calls"]
    assert c.status == "FAIL"
    assert "Infrastruktur" in c.evidence


def test_call_window_shape_green_in_transcript_mode_answer_skipped():
    s = scen({
        "lane_calls": {"count": 1, "keywords_all": ["Infrastruktur", "Server"]},
        "answer": {"must_not": ["[SILENT]"]},
    })
    o = obs(mode="transcript", lane_calls=["Hey Robo. Fass zusammen: INFRASTRUKTUR und server."])
    c = by_id(grade(s, o, BASELINE, {}))
    assert c["scenario.z.lane_calls"].status == "PASS"
    assert c["scenario.z.answer"].status == "SKIPPED"


def test_silent_answer_fails_even_with_agenda_line():
    s = scen({"answer": {"must_not": ["[SILENT]"], "agenda_line": True}})
    o = obs(answers=["[SILENT] Punkt 1: Budget"], agenda_lines=["Punkt 1: Budget"])
    c = by_id(grade(s, o, BASELINE, {}))["scenario.z.answer"]
    assert c.status == "FAIL"
    assert "[SILENT]" in c.evidence


def test_agenda_line_answer_passes_live():
    s = scen({"answer": {"must_not": ["[SILENT]"], "agenda_line": True}})
    o = obs(answers=["Die Agenda: punkt 1: budget, dann Pause."], agenda_lines=["Punkt 1: Budget"])
    assert by_id(grade(s, o, BASELINE, {}))["scenario.z.answer"].status == "PASS"


def test_agenda_line_skipped_without_known_agenda():
    s = scen({"answer": {"agenda_line": True}})
    o = obs(answers=["irgendwas"])
    assert by_id(grade(s, o, BASELINE, {}))["scenario.z.answer"].status == "SKIPPED"


def test_agenda_line_fails_when_not_in_answer():
    s = scen({"answer": {"agenda_line": True}})
    o = obs(answers=["irgendwas"], agenda_lines=["Punkt 1: Budget"])
    assert by_id(grade(s, o, BASELINE, {}))["scenario.z.answer"].status == "FAIL"


def test_wake_sentence_in_topic_fails_quoting_topic():
    s = scen({"topic": {"must_not_contain": ["Hey Robo", "fass bitte"]}})
    o = obs(topics=["Budget", "Hey Robo, fass bitte kurz zusammen"])
    c = by_id(grade(s, o, BASELINE, {}))["scenario.z.topic"]
    assert c.status == "FAIL"
    assert "Hey Robo, fass bitte kurz zusammen" in c.evidence


def test_clean_topics_pass():
    s = scen({"topic": {"must_not_contain": ["Hey Robo"]}})
    assert by_id(grade(s, obs(topics=["Budget"]), BASELINE, {}))["scenario.z.topic"].status == "PASS"


def test_no_topics_observed_is_skipped():
    s = scen({"topic": {"must_not_contain": ["Hey Robo"]}})
    c = by_id(grade(s, obs(topics=[]), BASELINE, {}))["scenario.z.topic"]
    assert c.status == "SKIPPED"
    assert "no topic observed" in c.evidence


TR = scen({"transcript": {"mid_sentence_max": "baseline:scenario.share_no_sentence_end"}})


def test_fragmented_transcript_fails_against_override():
    lines = [("Alex", "und dann"), ("Alex", "haben wir"), ("Alex", "Ende.")]
    c = by_id(grade(TR, obs(transcript_lines=lines), BASELINE, {"scenario.share_no_sentence_end": 0.3}))
    assert c["scenario.z.transcript"].status == "FAIL"
    assert "0.67" in c["scenario.z.transcript"].evidence


def test_transcript_under_bound_passes():
    lines = [("Alex", "Das ist gut."), ("Sam", "Wirklich?"), ("Alex", "und dann")]
    c = by_id(grade(TR, obs(transcript_lines=lines), BASELINE, {"scenario.share_no_sentence_end": 0.5}))
    assert c["scenario.z.transcript"].status == "PASS"


def test_threshold_from_baseline():
    base = {"thresholds": {"scenario.share_no_sentence_end": {"value": 0.1, "measured_by": "m"}}}
    lines = [("Alex", "und dann")]
    assert by_id(grade(TR, obs(transcript_lines=lines), base, {}))["scenario.z.transcript"].status == "FAIL"


def test_missing_threshold_propagates():
    with pytest.raises(MissingThreshold):
        grade(TR, obs(transcript_lines=[("Alex", "x.")]), BASELINE, {})


def test_two_lane_calls_fail_on_count_despite_keywords():
    o = obs(mode="transcript", lane_calls=["Infrastruktur", "Server"])
    c = by_id(grade(ORDER, o, BASELINE, {}))["scenario.z.lane_calls"]
    assert c.status == "FAIL"
    assert "2" in c.evidence and "1" in c.evidence


def test_keywords_in_lane_calls_but_not_live_answer_fail():
    o = obs(mode="call", lane_calls=["ORDER"], answers=["Das war nichts."])
    c = by_id(grade(ORDER, o, BASELINE, {}))["scenario.z.lane_calls"]
    assert c.status == "FAIL"
    assert "Infrastruktur" in c.evidence


def test_live_keywords_read_from_answer():
    o = obs(mode="call", lane_calls=["ORDER"], answers=["Infrastruktur und Server."])
    assert by_id(grade(ORDER, o, BASELINE, {}))["scenario.z.lane_calls"].status == "PASS"


def test_empty_transcript_fails():
    c = by_id(grade(TR, obs(), BASELINE, {"scenario.share_no_sentence_end": 0.5}))["scenario.z.transcript"]
    assert c.status == "FAIL"
    assert "no transcript lines" in c.evidence


def test_no_answer_fails_live():
    s = scen({"answer": {"must_not": ["[SILENT]"]}})
    c = by_id(grade(s, obs(), BASELINE, {}))["scenario.z.answer"]
    assert c.status == "FAIL"
    assert "no answer" in c.evidence


def test_answer_missing_keyword_named():
    s = scen({"answer": {"keywords_all": ["Budget", "Termin"]}})
    c = by_id(grade(s, obs(answers=["Das Budget steht."]), BASELINE, {}))["scenario.z.answer"]
    assert c.status == "FAIL"
    assert "Termin" in c.evidence and "Budget" not in c.evidence.split("missing")[-1]


def test_answer_skipped_in_transcript_mode():
    s = scen({"answer": {"keywords_all": ["Budget"]}})
    assert by_id(grade(s, obs(mode="transcript"), BASELINE, {}))["scenario.z.answer"].status == "SKIPPED"


def test_overlap_missing_speaker_named():
    s = scen({"transcript": {"speakers_all": ["Alex", "Sam"], "keywords_all": ["Budget"]}})
    lines = [("Alex", "Das Budget."), ("Alex", "Noch mehr.")]
    c = by_id(grade(s, obs(transcript_lines=lines), BASELINE, {}))["scenario.z.transcript"]
    assert c.status == "FAIL"
    assert "Sam" in c.evidence


def test_transcript_missing_keyword_named():
    s = scen({"transcript": {"keywords_all": ["Budget", "Termin"]}})
    c = by_id(grade(s, obs(transcript_lines=[("Alex", "Das budget.")]), BASELINE, {}))["scenario.z.transcript"]
    assert c.status == "FAIL"
    assert "Termin" in c.evidence


def test_only_named_groups_graded():
    assert grade(scen({"topic": {"must_not_contain": ["x"]}}), obs(topics=["a"]), BASELINE, {})[0].id == "scenario.z.topic"
    assert len(grade(scen({"topic": {"must_not_contain": ["x"]}}), obs(topics=["a"]), BASELINE, {})) == 1


def test_share_no_sentence_end():
    assert share_no_sentence_end([]) == 1.0
    assert share_no_sentence_end(["  ", ""]) == 1.0
    assert share_no_sentence_end(["Fertig.", "Wie bitte?", "Nein!", "Er sagte \"ja.\"", "und dann"]) == pytest.approx(0.2)
    assert share_no_sentence_end(["Wait…", "Hm..."]) == 0.0


def test_keywords_fold_case_and_umlaut_transliterations():
    s = scen({"answer": {"keywords_all": ["Oberflaechen", "Strasse"], "must_not": ["Fuer"]}})
    o = obs(mode="call", answers=["Die OBERFLÄCHEN an der Straße."])
    assert by_id(grade(s, o, BASELINE, {}))["scenario.z.answer"].status == "PASS"
    o = obs(mode="call", answers=["Die Oberflächen an der Straße, für alle."])
    assert by_id(grade(s, o, BASELINE, {}))["scenario.z.answer"].status == "FAIL"


def test_folding_applies_to_transcript_keywords_and_topics():
    s = scen({"transcript": {"keywords_all": ["Maerz"]}, "topic": {"must_not_contain": ["Aenderung"]}})
    o = obs(transcript_lines=[("Alex", "Im März ist das so.")], topics=["Änderung am Plan"])
    rows = by_id(grade(s, o, BASELINE, {}))
    assert rows["scenario.z.transcript"].status == "PASS"
    assert rows["scenario.z.topic"].status == "FAIL"


def test_lane_call_keywords_are_not_found_in_a_prompt_context_only():
    # the replay hands the oracle the order text only, so keywords of the
    # history cannot satisfy the check
    o = obs(mode="transcript", lane_calls=["Robo, Ende."])
    c = by_id(grade(ORDER, o, BASELINE, {}))["scenario.z.lane_calls"]
    assert c.status == "FAIL"
