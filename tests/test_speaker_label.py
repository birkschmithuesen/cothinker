# CoThinker — tests of rule-based diarization-label attribution
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_speaker_label.py

Only invented placeholder names and speech; no network, no I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))

from stt.pipeline.speaker_labels import label as sl  # noqa: E402  (the ONE import of the module under test)

PEOPLE = ["Ada Vance", "Ben Ortiz", "Cora"]


# ------------------------------------------------------- match_participant
def test_match_full_entry():
    assert sl.match_participant("Ada Vance", PEOPLE) == "Ada Vance"


def test_match_first_word_returns_canonical_entry():
    assert sl.match_participant("Ada", PEOPLE) == "Ada Vance"


def test_match_is_case_insensitive():
    assert sl.match_participant("bEN", PEOPLE) == "Ben Ortiz"


def test_match_single_word_entry():
    assert sl.match_participant("Cora", PEOPLE) == "Cora"


def test_match_unknown_name_is_none():
    assert sl.match_participant("Dan", PEOPLE) is None


def test_match_surname_alone_does_not_match():
    assert sl.match_participant("Vance", PEOPLE) is None


def test_match_empty_name_is_none():
    assert sl.match_participant("", PEOPLE) is None
    assert sl.match_participant(None, PEOPLE) is None
    assert sl.match_participant("   ", PEOPLE) is None


def test_match_empty_or_none_participants():
    assert sl.match_participant("Ada", []) is None
    assert sl.match_participant("Ada", None) is None


def test_match_skips_blank_entries():
    assert sl.match_participant("Ada", ["", "  ", "Ada Vance"]) == "Ada Vance"


def test_match_strips_whitespace():
    assert sl.match_participant("  Ben ", ["  Ben Ortiz  "]) == "Ben Ortiz"


# ---------------------------------------------------------- rule_attribute
def test_rule_first_person_in_list():
    assert sl.rule_attribute("Ich bin Ada.", PEOPLE) == "Ada Vance"


def test_rule_returns_canonical_full_name():
    assert sl.rule_attribute("Hallo, mein Name ist Ben", PEOPLE) == "Ben Ortiz"


def test_rule_name_not_in_list_is_rejected():
    assert sl.rule_attribute("Ich bin Dan.", PEOPLE) is None


def test_rule_empty_list_rejects_everything():
    assert sl.rule_attribute("Ich bin Ada.", []) is None


def test_rule_none_list_accepts_any_name():
    assert sl.rule_attribute("Ich bin Dan.", None) == "Dan"


def test_rule_default_participants_is_unrestricted():
    assert sl.rule_attribute("Ich bin Dan.") == "Dan"


def test_rule_no_intro_phrase_is_none():
    assert sl.rule_attribute("Ada hat gestern angerufen.", PEOPLE) is None


def test_rule_third_person_mention_is_not_an_intro():
    assert sl.rule_attribute("Das ist Ben, der kennt sich aus.", PEOPLE) is None


def test_rule_non_name_after_ich_bin_is_none():
    assert sl.rule_attribute("Ich bin müde.", PEOPLE) is None
    assert sl.rule_attribute("Ich bin müde.", None) is None


def test_rule_lowercase_stt_output():
    assert sl.rule_attribute("ich heiße cora", PEOPLE) == "Cora"


def test_rule_non_string_text_is_none():
    assert sl.rule_attribute(None, PEOPLE) is None
    assert sl.rule_attribute(42, PEOPLE) is None


def test_rule_empty_text_is_none():
    assert sl.rule_attribute("", PEOPLE) is None


# ----------------------------------------------------- mentions_participant
def test_mentions_finds_first_name():
    assert sl.mentions_participant("Danke, Ada.", PEOPLE) == ["Ada Vance"]


def test_mentions_in_order_of_appearance():
    text = "Cora fragt Ben, ob Ada Zeit hat."
    assert sl.mentions_participant(text, PEOPLE) == ["Cora", "Ben Ortiz",
                                                     "Ada Vance"]


def test_mentions_is_case_insensitive():
    assert sl.mentions_participant("ada, bitte", PEOPLE) == ["Ada Vance"]


def test_mentions_word_boundary_only():
    assert sl.mentions_participant("Das ist Adagio und Bender.", PEOPLE) == []


def test_mentions_hyphen_attached_token_does_not_match():
    # the token regex keeps hyphens inside a word
    assert sl.mentions_participant("Ada-Maria kommt", PEOPLE) == []


def test_mentions_no_participants():
    assert sl.mentions_participant("Ada ist da", []) == []
    assert sl.mentions_participant("Ada ist da", None) == []


def test_mentions_empty_text():
    assert sl.mentions_participant("", PEOPLE) == []
    assert sl.mentions_participant(None, PEOPLE) == []


def test_mentions_each_participant_once():
    assert sl.mentions_participant("Ada Ada Ada", PEOPLE) == ["Ada Vance"]


def test_mentions_is_broad_third_person_counts():
    # the detector is deliberately low precision: addressing counts too
    assert sl.mentions_participant("Ben hat gesagt, es geht.", PEOPLE) == [
        "Ben Ortiz"]


# --------------------------------------------------------- LabelAttribution
def test_attribution_starts_empty():
    a = sl.LabelAttribution()
    assert a.name_for("S1") is None
    assert a.pending == []


def test_assign_rule_sets_name():
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada Vance")
    assert a.name_for("S1") == "Ada Vance"
    assert a.name_for("S2") is None


def test_assign_rule_ignores_blank_input():
    a = sl.LabelAttribution()
    a.assign_rule("", "Ada")
    a.assign_rule("S1", "")
    a.assign_rule(None, None)
    assert a.name_for("S1") is None


def test_assign_rule_reintroduction_changes_name():
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    a.assign_rule("S1", "Ben")
    assert a.name_for("S1") == "Ben"
    assert a.pending == []


def test_name_for_normalises_label_whitespace():
    a = sl.LabelAttribution()
    a.assign_rule(" S1 ", "Ada")
    assert a.name_for("S1") == "Ada"
    assert a.name_for("  S1") == "Ada"


def test_llm_high_confidence_assigns_unnamed_label():
    a = sl.LabelAttribution()
    assert a.apply_llm_verdict("S2", "Ben", "high") == "assigned"
    assert a.name_for("S2") == "Ben"
    assert a.pending == []


def test_llm_non_high_confidence_is_only_a_suggestion():
    a = sl.LabelAttribution()
    assert a.apply_llm_verdict("S2", "Ben", "medium", "Danke, Ben") == \
        "suggestion"
    assert a.name_for("S2") is None
    assert len(a.pending) == 1
    p = a.pending[0]
    assert (p.label, p.proposed_name, p.confidence, p.kind) == (
        "S2", "Ben", "medium", "suggestion")
    assert p.evidence == "Danke, Ben"
    assert p.existing_name == ""


def test_llm_low_confidence_is_a_suggestion():
    a = sl.LabelAttribution()
    assert a.apply_llm_verdict("S2", "Ben", "low") == "suggestion"
    assert a.pending[0].confidence == "low"


def test_llm_same_name_as_rule_is_confirmed():
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    assert a.apply_llm_verdict("S1", "Ada", "high") == "confirmed"
    assert a.name_for("S1") == "Ada"
    assert a.pending == []


def test_llm_same_name_low_confidence_on_named_label_adds_nothing():
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    assert a.apply_llm_verdict("S1", "Ada", "low") == "suggestion"
    assert a.pending == []


def test_llm_never_overrides_rule_assignment():
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    assert a.apply_llm_verdict("S1", "Ben", "high", "Ben, bitte") == "conflict"
    assert a.name_for("S1") == "Ada"
    p = a.pending[-1]
    assert (p.kind, p.existing_name, p.proposed_name) == (
        "conflict", "Ada", "Ben")
    assert p.evidence == "Ben, bitte"


def test_llm_never_overrides_earlier_llm_assignment():
    a = sl.LabelAttribution()
    a.apply_llm_verdict("S1", "Ada", "high")
    assert a.apply_llm_verdict("S1", "Ben", "high") == "conflict"
    assert a.name_for("S1") == "Ada"


def test_llm_low_confidence_conflict_is_still_a_conflict():
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    assert a.apply_llm_verdict("S1", "Ben", "low") == "conflict"
    assert a.pending[0].kind == "conflict"


def test_llm_blank_input_is_ignored():
    a = sl.LabelAttribution()
    assert a.apply_llm_verdict("", "Ben", "high") == "ignored"
    assert a.apply_llm_verdict("S1", "", "high") == "ignored"
    assert a.pending == []


def test_rule_after_llm_assignment_overrides_it():
    a = sl.LabelAttribution()
    a.apply_llm_verdict("S1", "Ben", "high")
    a.assign_rule("S1", "Ada")
    assert a.name_for("S1") == "Ada"


def test_pending_accumulates_in_order():
    a = sl.LabelAttribution()
    a.apply_llm_verdict("S1", "Ada", "medium")
    a.apply_llm_verdict("S2", "Ben", "low")
    assert [p.label for p in a.pending] == ["S1", "S2"]


def test_attributions_are_independent_instances():
    a, b = sl.LabelAttribution(), sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    assert b.name_for("S1") is None


# -------------------------------------------------------------- notice_text
def test_notice_text_conflict():
    v = sl.PendingVerdict(label="S1", proposed_name="Ben", confidence="high",
                          kind="conflict", existing_name="Ada")
    t = sl.notice_text(v)
    assert "**S1**" in t and "**Ada**" in t and "**Ben**" in t
    assert "bitte bestätigen" in t


def test_notice_text_suggestion():
    v = sl.PendingVerdict(label="S2", proposed_name="Cora",
                          confidence="medium", kind="suggestion")
    t = sl.notice_text(v)
    assert "**S2**" in t and "**Cora**" in t and "(medium)" in t
    assert "nicht automatisch zugeordnet" in t


# --------------------------------------------------------- AttributionWindow
def test_window_default_size_is_six():
    assert sl.AttributionWindow().max_len == 6


def test_window_push_and_snapshot_order():
    w = sl.AttributionWindow()
    w.push("S1", "eins")
    w.push("S2", "zwei")
    snap = w.snapshot()
    assert [(e["index"], e["label"], e["text"]) for e in snap] == [
        (0, "S1", "eins"), (1, "S2", "zwei")]


def test_window_drops_oldest_beyond_max_len():
    w = sl.AttributionWindow(max_len=3)
    for i in range(5):
        w.push("S1", f"t{i}")
    assert [e["text"] for e in w.snapshot()] == ["t2", "t3", "t4"]


def test_window_indices_keep_counting_after_eviction():
    w = sl.AttributionWindow(max_len=2)
    for i in range(4):
        w.push("S1", f"t{i}")
    assert [e["index"] for e in w.snapshot()] == [2, 3]


def test_window_snapshot_is_a_copy_of_the_list():
    w = sl.AttributionWindow()
    w.push("S1", "x")
    snap = w.snapshot()
    snap.clear()
    assert len(w.snapshot()) == 1


def test_window_push_normalises_label_and_text():
    w = sl.AttributionWindow()
    w.push(" S1 ", None)
    assert w.snapshot()[0]["label"] == "S1"
    assert w.snapshot()[0]["text"] == ""


def test_window_unnamed_labels_first_seen_order_once_each():
    w = sl.AttributionWindow()
    for label in ("S2", "S1", "S2", "S3"):
        w.push(label, "x")
    assert w.unnamed_labels(sl.LabelAttribution()) == ["S2", "S1", "S3"]


def test_window_unnamed_labels_skips_named():
    w = sl.AttributionWindow()
    w.push("S1", "x")
    w.push("S2", "y")
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada")
    assert w.unnamed_labels(a) == ["S2"]


def test_window_unnamed_labels_ignores_blank_label():
    w = sl.AttributionWindow()
    w.push("", "x")
    assert w.unnamed_labels(sl.LabelAttribution()) == []


# ---------------------------------------------------------- should_fire_llm
def _window(*entries):
    w = sl.AttributionWindow()
    for label, text in entries:
        w.push(label, text)
    return w


def test_fire_empty_window_is_false():
    assert sl.should_fire_llm(sl.AttributionWindow(), PEOPLE,
                              sl.LabelAttribution()) is False


def test_fire_on_handover_to_unnamed_label():
    w = _window(("S1", "Ben, du hast die Hand gehoben."))
    assert sl.should_fire_llm(w, PEOPLE, sl.LabelAttribution()) is True


def test_no_fire_without_mention():
    w = _window(("S1", "Wir fangen jetzt an."))
    assert sl.should_fire_llm(w, PEOPLE, sl.LabelAttribution()) is False


def test_no_fire_when_every_label_is_named():
    w = _window(("S1", "Danke, Ben."))
    a = sl.LabelAttribution()
    a.assign_rule("S1", "Ada Vance")
    assert sl.should_fire_llm(w, PEOPLE, a) is False


def test_no_fire_when_mentioned_person_already_named():
    w = _window(("S2", "Dann übernimmt Ben."), ("S1", "Danke, Ben."))
    a = sl.LabelAttribution()
    a.assign_rule("S2", "Ben Ortiz")
    # S1 is still unnamed, but Ben already has a label
    assert sl.should_fire_llm(w, PEOPLE, a) is False


def test_fire_when_other_mentioned_person_still_free():
    w = _window(("S2", "x"), ("S1", "Danke, Ben und Ada."))
    a = sl.LabelAttribution()
    a.assign_rule("S2", "Ben Ortiz")
    assert sl.should_fire_llm(w, PEOPLE, a) is True


def test_only_most_recent_final_is_inspected_for_mentions():
    w = _window(("S1", "Ben, bitte."), ("S2", "Ja, gerne."))
    assert sl.should_fire_llm(w, PEOPLE, sl.LabelAttribution()) is False


def test_fire_requires_participants():
    w = _window(("S1", "Ben, bitte."))
    assert sl.should_fire_llm(w, [], sl.LabelAttribution()) is False
    assert sl.should_fire_llm(w, None, sl.LabelAttribution()) is False


def test_fire_reads_the_current_utterance_pushed_before_the_call():
    w = sl.AttributionWindow()
    a = sl.LabelAttribution()
    assert sl.should_fire_llm(w, PEOPLE, a) is False
    w.push("S1", "Danke, Cora.")
    assert sl.should_fire_llm(w, PEOPLE, a) is True


# ------------------------------------------------- end-to-end intro round
def test_intro_round_names_labels_and_stays_quiet_afterwards():
    attrib, window = sl.LabelAttribution(), sl.AttributionWindow()
    script = [("S1", "Hallo zusammen, ich bin Ada."),
              ("S2", "Ich heiße Ben."),
              ("S3", "Moin."),
              ("S1", "Danke.")]
    fired = []
    for label, text in script:
        name = sl.rule_attribute(text, PEOPLE)
        if name:
            attrib.assign_rule(label, name)
        window.push(label, text)
        fired.append(sl.should_fire_llm(window, PEOPLE, attrib))
    assert attrib.name_for("S1") == "Ada Vance"
    assert attrib.name_for("S2") == "Ben Ortiz"
    assert attrib.name_for("S3") is None
    assert fired == [False, False, False, False]
