#!/usr/bin/env python3
# CoThinker - the stage state and the rules that decide it: characterization tests
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The stage state: entries, identities, caps. See docs/stage-methodology.md.

python3 -m pytest -q tests/test_stage_state.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gateway.events import is_number, is_token
from gateway.stage_state import (MAX_HISTORY, MAX_PENDING, PENDING_KINDS,
                                 PENDING_TIMEOUT_S, RECEIPT_LINGER_S,
                                 TOO_SOON, TOPIC_MIN_HOLD_S, TOPIC_WINDOW_S,
                                 StageState, derived_title, quote_is_spoken,
                                 scrub)


class Clock:
    """Injected time. `t` is monotonic seconds, `wall` the display clock."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.wall = datetime(2026, 9, 8, 19, 11, 52)

    def now(self) -> float:
        return self.t

    def clock(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fresh_state() -> tuple[StageState, Clock]:
    c = Clock()
    return StageState(now=c.now, clock=c.clock), c


OFFSET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


# --------------------------------------------------------- mirror and headline
@pytest.fixture
def mirror_scenario(fresh_state):
    s, c = fresh_state
    empty_at_start = s.mirror
    mirror_accepted = s.apply({"action": "mirror", "text": "Robo hört mit."})
    mirror_is_set = s.mirror
    headline_accepted = s.apply({"action": "headline", "text": "Punkt 2"})
    headline_is_set = s.headline
    non_string_rejected = s.apply({"action": "mirror", "text": 42})
    old_mirror_survives = s.mirror
    explicit_empty_clears = s.apply({"action": "mirror", "text": ""})
    mirror_now_empty = s.mirror
    return SimpleNamespace(**locals())


def test_mirror_is_empty_at_the_start(mirror_scenario):
    assert mirror_scenario.empty_at_start == ""


def test_mirror_accepted(mirror_scenario):
    assert mirror_scenario.mirror_accepted == ""


def test_mirror_is_set(mirror_scenario):
    assert mirror_scenario.mirror_is_set == "Robo hört mit."


def test_headline_accepted(mirror_scenario):
    assert mirror_scenario.headline_accepted == ""


def test_headline_is_set(mirror_scenario):
    assert mirror_scenario.headline_is_set == "Punkt 2"


def test_a_non_string_mirror_is_rejected(mirror_scenario):
    assert mirror_scenario.non_string_rejected != ""


def test_the_old_mirror_survives_a_rejected_action(mirror_scenario):
    assert mirror_scenario.old_mirror_survives == "Robo hört mit."


def test_an_explicit_empty_mirror_clears_it(mirror_scenario):
    assert mirror_scenario.explicit_empty_clears == ""


def test_the_mirror_is_actually_empty_now(mirror_scenario):
    assert mirror_scenario.mirror_now_empty == ""


# ------------------------------ call_answer: a call-out result that outlives
# the mirror and the topic
@pytest.fixture
def call_answer_scenario(fresh_state):
    s, c = fresh_state
    empty_at_start = s.call_answer
    call_answer_accepted = s.apply(
        {"action": "call_answer", "text": "Robo: Übersicht ..."})
    call_answer_is_set = s.call_answer
    non_string_rejected = s.apply({"action": "call_answer", "text": 42})
    old_survives = s.call_answer
    topic_result = s.apply(
        {"action": "topic", "title": "Startup", "markdown": "x"})
    call_answer_after_topic = s.call_answer
    explicit_empty_clears = s.apply({"action": "call_answer", "text": ""})
    call_answer_now_empty = s.call_answer
    return SimpleNamespace(**locals())


def test_call_answer_is_empty_at_the_start(call_answer_scenario):
    assert call_answer_scenario.empty_at_start == ""


def test_call_answer_accepted(call_answer_scenario):
    assert call_answer_scenario.call_answer_accepted == ""


def test_call_answer_is_set(call_answer_scenario):
    assert call_answer_scenario.call_answer_is_set == "Robo: Übersicht ..."


def test_a_non_string_call_answer_is_rejected(call_answer_scenario):
    assert call_answer_scenario.non_string_rejected != ""


def test_the_old_call_answer_survives_a_rejected_action(call_answer_scenario):
    assert call_answer_scenario.old_survives == "Robo: Übersicht ..."


def test_a_topic_change_does_not_clear_call_answer(call_answer_scenario):
    assert call_answer_scenario.topic_result == ""
    assert call_answer_scenario.call_answer_after_topic == "Robo: Übersicht ..."


def test_an_explicit_empty_call_answer_clears_it(call_answer_scenario):
    assert call_answer_scenario.explicit_empty_clears == ""


def test_the_call_answer_is_actually_empty_now(call_answer_scenario):
    assert call_answer_scenario.call_answer_now_empty == ""


# --------------------------------------- decisions: append, cap at three,
# oldest drops
@pytest.fixture
def decisions_scenario(fresh_state):
    s, c = fresh_state
    for i in range(4):
        c.advance(60)
        s.apply({"action": "decision", "time": f"19:4{i}", "text": f"Beschluss {i}"})
    content = s.content()
    return SimpleNamespace(s=s, c=c, content=content)


def test_at_most_three_decisions(decisions_scenario):
    assert len(decisions_scenario.s.decisions) == 3


def test_the_oldest_decision_dropped_out(decisions_scenario):
    assert decisions_scenario.s.decisions[0].text == "Beschluss 1"


def test_the_newest_decision_is_last(decisions_scenario):
    assert decisions_scenario.s.decisions[-1].text == "Beschluss 3"


def test_decision_identities_are_unique(decisions_scenario):
    assert len({d.id for d in decisions_scenario.s.decisions}) == 3


def test_decision_identities_are_not_reused(decisions_scenario):
    assert decisions_scenario.s.decisions[-1].id == "D4"


def test_every_decision_carries_added_at_in_content(decisions_scenario):
    d = decisions_scenario.content
    assert all(isinstance(e.get("added_at"), str) and "T" in e["added_at"]
               for e in d["decisions"])


def test_added_at_is_the_entrys_own_label_not_the_render_time(decisions_scenario):
    d = decisions_scenario.content
    assert d["decisions"][0]["added_at"] == decisions_scenario.s.decisions[0].added_at_label


def test_added_at_carries_the_utc_offset_like_every_displayed_timestamp(decisions_scenario):
    d = decisions_scenario.content
    assert d["decisions"][-1]["added_at"].endswith(d["updated"][-6:])


# ----------------------------------------------- notices: two slots, the
# third is parked
@pytest.fixture
def notices_scenario(fresh_state):
    s, c = fresh_state
    first_accepted = s.apply({"action": "notice", "kind": "value_conflict",
                              "text": "**Frist** eben 6.10., im Kerndokument 5.10."})
    second_accepted = s.apply({"action": "notice", "kind": "decision_breach",
                               "text": "**Beschluss** Auto-Router fehlt in der Kalkulation"})
    third_parked = s.apply({"action": "notice", "kind": "value_conflict",
                            "text": "noch etwas"})
    still_two = len(s.notices)
    parked_counted = s.parked
    unknown_kind_rejected = s.apply(
        {"action": "notice", "kind": "vibes", "text": "x"})
    content = s.content()
    return SimpleNamespace(**locals())


@pytest.fixture
def speaker_conflict_scenario(fresh_state):
    s_spk, c = fresh_state
    speaker_conflict_accepted = s_spk.apply(
        {"action": "notice", "kind": "speaker_conflict",
         "text": "**S1**: bereits **Anke** zugeordnet"})
    unknown_kind_rejected = s_spk.apply(
        {"action": "notice", "kind": "made_up_kind", "text": "x"})
    return SimpleNamespace(**locals())


@pytest.fixture
def empty_notice_slot_scenario(fresh_state):
    leer, c = fresh_state
    leer.apply({"action": "notice", "kind": "value_conflict", "text": "passt rein"})
    content = leer.content()
    return SimpleNamespace(**locals())


def test_first_notice_accepted(notices_scenario):
    assert notices_scenario.first_accepted == ""


def test_second_notice_accepted(notices_scenario):
    assert notices_scenario.second_accepted == ""


def test_third_notice_is_parked(notices_scenario):
    assert notices_scenario.third_parked == "parked"


def test_still_only_two_notices(notices_scenario):
    assert notices_scenario.still_two == 2


def test_the_parked_notice_is_counted(notices_scenario):
    assert notices_scenario.parked_counted == 1


def test_speaker_conflict_is_an_accepted_notice_kind(speaker_conflict_scenario):
    assert speaker_conflict_scenario.speaker_conflict_accepted == ""


def test_an_unknown_kind_is_still_rejected(speaker_conflict_scenario):
    assert speaker_conflict_scenario.unknown_kind_rejected == "unknown kind"


def test_an_unknown_kind_is_rejected(notices_scenario):
    assert notices_scenario.unknown_kind_rejected == "unknown kind"


def test_the_parked_entry_reaches_content(notices_scenario):
    assert len(notices_scenario.content["parked_entries"]) == 1


def test_the_parked_entry_carries_its_text(notices_scenario):
    assert notices_scenario.content["parked_entries"][0]["text"] == "noch etwas"


def test_the_parked_entry_carries_its_parking_time_as_iso_text(notices_scenario):
    parked_at = notices_scenario.content["parked_entries"][0]["parked_at"]
    assert isinstance(parked_at, str) and "T" in parked_at


def test_the_running_count_is_carried_alongside_the_entries(notices_scenario):
    assert notices_scenario.content["parked"] == 1


def test_the_parked_entries_are_json_serialisable(notices_scenario):
    assert isinstance(json.dumps(notices_scenario.content["parked_entries"]), str)


def test_nothing_is_parked_while_a_slot_is_free(empty_notice_slot_scenario):
    assert (empty_notice_slot_scenario.content["parked_entries"] == []
            and empty_notice_slot_scenario.content["parked"] == 0)


# ------------------------------- the parked line lingers, then goes -- with
# an event
@pytest.fixture
def parked_line_linger_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "notice", "kind": "value_conflict", "text": "eins"})
    s.apply({"action": "notice", "kind": "decision_breach", "text": "zwei"})
    s.apply({"action": "notice", "kind": "value_conflict", "text": "drei"})
    c.advance(3)
    s.tick()
    still_stands_after_3s = len(s.parked_entries) == 1
    c.advance(5)
    events = s.tick()
    gone_after_linger = (s.parked_entries == []
                         and s.content()["parked_entries"] == [])
    removal_reported = any(e["event"] == "parked_cleared" for e in events)
    geparkt = next((e for e in events if e["event"] == "parked_cleared"), None)
    counter_survives = s.content()["parked"] == 1
    more = s.tick()
    not_repeated = not any(e["event"] == "parked_cleared" for e in more)
    return SimpleNamespace(**locals())


def test_the_parked_line_still_stands_after_3_s(parked_line_linger_scenario):
    assert parked_line_linger_scenario.still_stands_after_3s


def test_it_is_gone_after_the_linger_time(parked_line_linger_scenario):
    assert parked_line_linger_scenario.gone_after_linger


def test_the_removal_is_reported_for_the_trace(parked_line_linger_scenario):
    assert parked_line_linger_scenario.removal_reported


def test_the_parked_event_carries_the_entrys_age(parked_line_linger_scenario):
    assert parked_line_linger_scenario.geparkt["age_s"] == 8.0


def test_the_counter_survives_the_removal_it_is_the_meetings_total(parked_line_linger_scenario):
    assert parked_line_linger_scenario.counter_survives


def test_tick_is_idempotent_the_parked_event_is_not_repeated(parked_line_linger_scenario):
    assert parked_line_linger_scenario.not_repeated


# ------------------------------- a parked question is kept for display in
# the same way
@pytest.fixture
def parked_question_display_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    s.apply({"action": "question", "text": "Und der Betrag?"})
    parked_on_display = [p["text"] for p in s.content()["parked_entries"]]
    question_untouched = s.question.text
    c.advance(RECEIPT_LINGER_S)
    events = s.tick()
    goes_with_event = (s.content()["parked_entries"] == []
                       and any(e["event"] == "parked_cleared" for e in events))
    return SimpleNamespace(**locals())


def test_the_parked_question_is_on_display(parked_question_display_scenario):
    assert parked_question_display_scenario.parked_on_display == ["Und der Betrag?"]


def test_the_question_in_the_slot_is_untouched(parked_question_display_scenario):
    assert parked_question_display_scenario.question_untouched == "Fünfter oder sechster Oktober?"


def test_it_goes_after_the_linger_time_with_its_event(parked_question_display_scenario):
    assert parked_question_display_scenario.goes_with_event


# ----------------------------------------------- question: exactly one slot
@pytest.fixture
def one_question_slot_scenario(fresh_state):
    s, c = fresh_state
    question_accepted = s.apply(
        {"action": "question", "text": "Fünfter oder sechster Oktober?"})
    second_parked = s.apply({"action": "question", "text": "Und der Betrag?"})
    first_stands = s.question.text
    return SimpleNamespace(**locals())


def test_question_accepted(one_question_slot_scenario):
    assert one_question_slot_scenario.question_accepted == ""


def test_a_second_question_is_parked(one_question_slot_scenario):
    assert one_question_slot_scenario.second_parked == "parked"


def test_the_first_question_stands(one_question_slot_scenario):
    assert one_question_slot_scenario.first_stands == "Fünfter oder sechster Oktober?"


# ------------------------------------------- unknown actions never damage
# the surface
@pytest.fixture
def unknown_action_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "mirror", "text": "Robo arbeitet."})
    unknown_rejected = s.apply({"action": "explode"})
    missing_key_rejected = s.apply({})
    none_result = s.apply({"action": "none"})
    mirror_unchanged = s.mirror
    return SimpleNamespace(**locals())


def test_unknown_action_rejected(unknown_action_scenario):
    assert unknown_action_scenario.unknown_rejected == "unknown action"


def test_a_missing_action_key_is_rejected(unknown_action_scenario):
    assert unknown_action_scenario.missing_key_rejected == "unknown action"


def test_none_is_accepted_and_changes_nothing(unknown_action_scenario):
    assert (unknown_action_scenario.none_result == ""
            and unknown_action_scenario.mirror_unchanged == "Robo arbeitet.")


# ----------------------------------------------- content() carries the schema
@pytest.fixture
def content_schema_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "mirror", "text": "Robo hört mit."})
    s.apply({"action": "notice", "kind": "value_conflict",
             "text": "**Frist** 6.10. vs 5.10."})
    s.apply({"action": "call_answer", "text": "Robo: Übersicht ..."})
    d = s.content()
    return SimpleNamespace(s=s, c=c, d=d)


def test_version_is_2(content_schema_scenario):
    assert content_schema_scenario.d["version"] == 2


def test_mirror_is_carried(content_schema_scenario):
    assert content_schema_scenario.d["mirror"] == "Robo hört mit."


def test_call_answer_is_carried(content_schema_scenario):
    assert content_schema_scenario.d["call_answer"] == "Robo: Übersicht ..."


def test_notices_are_a_list_of_dicts(content_schema_scenario):
    d = content_schema_scenario.d
    assert isinstance(d["notices"], list) and isinstance(d["notices"][0], dict)


def test_a_notice_carries_raised_at_as_iso_text(content_schema_scenario):
    raised_at = content_schema_scenario.d["notices"][0]["raised_at"]
    assert isinstance(raised_at, str) and "T" in raised_at


def test_the_browser_gets_the_age_thresholds(content_schema_scenario):
    d = content_schema_scenario.d
    assert d["age_visible_after_s"] == 20 and d["follow_up_after_s"] == 120


def test_raised_at_carries_the_utc_offset(content_schema_scenario):
    assert bool(OFFSET_RE.match(content_schema_scenario.d["notices"][0]["raised_at"]))


def test_updated_carries_the_utc_offset(content_schema_scenario):
    assert bool(OFFSET_RE.match(content_schema_scenario.d["updated"]))


def test_the_label_keeps_the_wall_clock_reading_of_the_injected_clock(content_schema_scenario):
    assert content_schema_scenario.d["updated"].startswith("2026-09-08T19:11:52")


def test_the_label_round_trips_through_fromisoformat_as_an_aware_datetime(content_schema_scenario):
    assert datetime.fromisoformat(content_schema_scenario.d["updated"]).utcoffset() is not None


def test_an_aware_clock_keeps_its_own_offset():
    s_tz = StageState(now=lambda: 0.0,
                      clock=lambda: datetime(2026, 9, 8, 19, 11, 52,
                                             tzinfo=timezone(timedelta(hours=2))))
    s_tz.apply({"action": "main", "markdown": "## Test", "quote": "zeig das"},
               "Robo, zeig das mal.")
    assert s_tz.main.set_at_label == "2026-09-08T19:11:52+02:00"


# ------------------------- links: replace, drop bad items, fall back to
# file as label
@pytest.fixture
def links_scenario(fresh_state):
    s, c = fresh_state
    well_formed_accepted = s.apply({"action": "links", "items": [
        {"file": "protokoll.md", "label": "Protokoll"},
    ]})
    file_and_label_preserved = list(s.links)
    missing_label_result = s.apply(
        {"action": "links", "items": [{"file": "notizen.md"}]})
    missing_label_fallback = list(s.links)
    non_list_rejected = s.apply({"action": "links", "items": "notizen.md"})
    links_untouched_by_rejection = list(s.links)
    bad_items_result = s.apply({"action": "links", "items": [
        "not a dict",
        {"label": "no file field"},
        {"file": "gut.md", "label": "Gut"},
    ]})
    bad_items_dropped = list(s.links)
    second_links_result = s.apply(
        {"action": "links", "items": [{"file": "neu.md", "label": "Neu"}]})
    replaces_not_appends = list(s.links)
    content = s.content()
    return SimpleNamespace(**locals())


def test_well_formed_items_accepted(links_scenario):
    assert links_scenario.well_formed_accepted == ""


def test_file_and_label_are_preserved(links_scenario):
    assert links_scenario.file_and_label_preserved == [
        {"file": "protokoll.md", "label": "Protokoll"}]


def test_a_missing_label_falls_back_to_the_file_value(links_scenario):
    assert (links_scenario.missing_label_result == ""
            and links_scenario.missing_label_fallback
            == [{"file": "notizen.md", "label": "notizen.md"}])


def test_a_non_list_items_value_is_rejected(links_scenario):
    assert links_scenario.non_list_rejected == "no items"


def test_state_links_is_untouched_by_the_rejected_action(links_scenario):
    assert links_scenario.links_untouched_by_rejection == [
        {"file": "notizen.md", "label": "notizen.md"}]


def test_bad_items_are_dropped_good_ones_in_the_same_list_still_arrive(links_scenario):
    assert (links_scenario.bad_items_result == ""
            and links_scenario.bad_items_dropped
            == [{"file": "gut.md", "label": "Gut"}])


def test_a_second_successful_links_action_replaces_the_list_not_appends(links_scenario):
    assert (links_scenario.second_links_result == ""
            and links_scenario.replaces_not_appends
            == [{"file": "neu.md", "label": "Neu"}])


def test_content_links_reflects_the_current_list(links_scenario):
    assert links_scenario.content["links"] == [{"file": "neu.md", "label": "Neu"}]


def test_content_links_is_json_serialisable(links_scenario):
    assert isinstance(json.dumps(links_scenario.content["links"]), str)


# ------------------------------------- main area: replaced only with a
# verbatim quote
GESPROCHEN = ("Wir muessen das mal sehen. Robo, zeig uns die vier Stränge "
              "des Antrags, bitte.")


@pytest.fixture
def main_area_quote_scenario(fresh_state):
    s, c = fresh_state
    empty_at_start = s.main.markdown
    without_quote_rejected = s.apply(
        {"action": "main", "markdown": "## Vier Stränge"}, GESPROCHEN)
    stayed_empty = s.main.markdown
    empty_quote_rejected = s.apply(
        {"action": "main", "markdown": "## Vier Stränge", "quote": "   "}, GESPROCHEN)
    with_quote_accepted = s.apply(
        {"action": "main", "markdown": "## Vier Stränge",
         "quote": "zeig uns die vier Stränge"}, GESPROCHEN)
    main_is_set = s.main.markdown
    quote_kept = s.main.quote
    main_id = s.main.id
    without_markdown_rejected = s.apply(
        {"action": "main", "quote": "zeig was"}, GESPROCHEN)
    return SimpleNamespace(**locals())


def test_main_is_empty_at_the_start(main_area_quote_scenario):
    assert main_area_quote_scenario.empty_at_start == ""


def test_main_without_a_quote_is_rejected(main_area_quote_scenario):
    assert main_area_quote_scenario.without_quote_rejected == "no quote"


def test_the_main_area_stayed_empty(main_area_quote_scenario):
    assert main_area_quote_scenario.stayed_empty == ""


def test_an_empty_quote_is_rejected(main_area_quote_scenario):
    assert main_area_quote_scenario.empty_quote_rejected == "no quote"


def test_with_a_quote_it_is_accepted(main_area_quote_scenario):
    assert main_area_quote_scenario.with_quote_accepted == ""


def test_the_main_area_is_set(main_area_quote_scenario):
    assert main_area_quote_scenario.main_is_set == "## Vier Stränge"


def test_the_quote_is_kept_for_the_trace(main_area_quote_scenario):
    assert main_area_quote_scenario.quote_kept == "zeig uns die vier Stränge"


def test_main_got_an_identity(main_area_quote_scenario):
    assert main_area_quote_scenario.main_id == "M1"


def test_main_without_markdown_is_rejected(main_area_quote_scenario):
    assert main_area_quote_scenario.without_markdown_rejected == "no markdown"


# ----------------------------- main area: A3 -- no line-count cap, nothing
# is ever collapsed
@pytest.fixture
def main_area_no_cap_scenario(fresh_state):
    s, c = fresh_state
    long_doc = "\n".join(f"Zeile {i}" for i in range(1, 21))
    s.apply({"action": "main", "markdown": long_doc, "quote": "zeig die Liste"},
            "Robo, zeig die Liste bitte.")
    all_twenty_lines = s.main.markdown.count("\n") + 1
    nothing_collapsed = s.main.collapsed_lines
    collapsed_text_empty = s.main.collapsed_markdown
    c_short = Clock()
    s_short = StageState(now=c_short.now, clock=c_short.clock)
    s_short.apply({"action": "main", "markdown": "eins\nzwei\ndrei",
                  "quote": "zeig die drei"}, "Zeig die drei, bitte.")
    short_not_collapsed = s_short.main.collapsed_lines
    short_content_untouched = s_short.main.markdown
    return SimpleNamespace(**locals())


def test_all_twenty_lines_stay_on_the_surface(main_area_no_cap_scenario):
    assert main_area_no_cap_scenario.all_twenty_lines == 20


def test_nothing_is_collapsed(main_area_no_cap_scenario):
    assert main_area_no_cap_scenario.nothing_collapsed == 0


def test_the_collapsed_text_is_empty(main_area_no_cap_scenario):
    assert main_area_no_cap_scenario.collapsed_text_empty == ""


def test_nothing_is_collapsed_for_a_short_surface_either(main_area_no_cap_scenario):
    assert main_area_no_cap_scenario.short_not_collapsed == 0


def test_and_the_content_is_untouched(main_area_no_cap_scenario):
    assert main_area_no_cap_scenario.short_content_untouched == "eins\nzwei\ndrei"


# ------------------- main area: A3 mutation test -- a 27-line draft comes
# through whole
def test_all_27_lines_are_visible_none_collapsed(fresh_state):
    s_27, c = fresh_state
    doc_27 = "\n".join(f"Zeile {i}" for i in range(1, 28))
    s_27.apply({"action": "main", "markdown": doc_27, "quote": "zeig den Entwurf"},
               "Robo, zeig den Entwurf.")
    assert s_27.main.markdown.count("\n") + 1 == 27 and s_27.main.collapsed_lines == 0


# ------------------------- main area: a replacement still gets a fresh
# identity
@pytest.fixture
def main_area_replacement_scenario(fresh_state):
    s, c = fresh_state
    long_doc = "\n".join(f"Zeile {i}" for i in range(1, 21))
    s.apply({"action": "main", "markdown": long_doc, "quote": "zeig die Liste"},
            "Robo, zeig die Liste bitte.")
    s.apply({"action": "main", "markdown": "## Kurz", "quote": "zeig die Kosten"},
            "Und jetzt zeig die Kosten.")
    return SimpleNamespace(s=s, c=c)


def test_the_new_content_is_short(main_area_replacement_scenario):
    assert main_area_replacement_scenario.s.main.markdown == "## Kurz"


def test_still_nothing_collapsed(main_area_replacement_scenario):
    assert main_area_replacement_scenario.s.main.collapsed_lines == 0


def test_the_new_content_got_a_new_identity(main_area_replacement_scenario):
    assert main_area_replacement_scenario.s.main.id == "M2"


# ------------------------------------- resolution: a visible receipt in
# the same slot
@pytest.fixture
def resolution_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "notice", "kind": "value_conflict",
             "text": "**Frist** 6.10. vs 5.10."})
    nid = s.notices[0].id
    unknown_id_rejected = s.resolve("N99", "irgendwas")
    resolving_works = s.resolve(nid, "die Runde hat den 6.10. bestätigt")
    entry_stays_in_place = len(s.notices)
    marked_resolved = s.notices[0].state
    receipt_text_kept = s.notices[0].resolution
    slot_free_again = s.apply(
        {"action": "notice", "kind": "value_conflict", "text": "neu"})
    resolving_twice_rejected = s.resolve(nid, "nochmal")
    return SimpleNamespace(**locals())


def test_an_unknown_id_cannot_be_resolved(resolution_scenario):
    assert resolution_scenario.unknown_id_rejected == "unknown id"


def test_resolving_works(resolution_scenario):
    assert resolution_scenario.resolving_works == ""


def test_the_entry_stays_in_place_resolution(resolution_scenario):
    assert resolution_scenario.entry_stays_in_place == 1


def test_it_is_marked_resolved_notice(resolution_scenario):
    assert resolution_scenario.marked_resolved == "resolved"


def test_the_receipt_text_is_kept_notice(resolution_scenario):
    assert resolution_scenario.receipt_text_kept == "die Runde hat den 6.10. bestätigt"


def test_the_slot_is_free_again_notice(resolution_scenario):
    assert resolution_scenario.slot_free_again == ""


def test_resolving_twice_is_rejected_notice(resolution_scenario):
    assert resolution_scenario.resolving_twice_rejected == "not open"


# ----------------------- receipts clear themselves, but only after the
# linger time
@pytest.fixture
def notice_receipt_linger_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "notice", "kind": "value_conflict",
             "text": "**Frist** 6.10. vs 5.10."})
    s.resolve(s.notices[0].id, "geklärt")
    c.advance(3)
    s.tick()
    still_stands_after_3s = len(s.notices) == 1
    c.advance(5)
    events = s.tick()
    gone_after_linger = len(s.notices) == 0
    clearing_reported = any(e["event"] == "notice_cleared" for e in events)
    cleared = next(e for e in events if e["event"] == "notice_cleared")
    return SimpleNamespace(**locals())


def test_the_receipt_still_stands_after_3_s_notice(notice_receipt_linger_scenario):
    assert notice_receipt_linger_scenario.still_stands_after_3s


def test_the_receipt_is_gone_after_the_linger_time_notice(notice_receipt_linger_scenario):
    assert notice_receipt_linger_scenario.gone_after_linger


def test_the_clearing_is_reported_for_the_trace_notice(notice_receipt_linger_scenario):
    assert notice_receipt_linger_scenario.clearing_reported


def test_the_cleared_event_carries_the_entrys_age_notice(notice_receipt_linger_scenario):
    assert notice_receipt_linger_scenario.cleared["age_s"] == 8.0


# ------------------------------------------ follow-up: a notice may not
# get stuck
@pytest.fixture
def notice_follow_up_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "notice", "kind": "value_conflict",
             "text": "**Frist** 6.10. vs 5.10."})
    no_follow_up_right_away = s.follow_up_note()
    c.advance(60)
    s.tick()
    still_none_after_one_minute = s.follow_up_note()
    c.advance(70)
    events = s.tick()
    follow_up_reached = s.notices[0].follow_up is True
    change_reported_once = sum(1 for e in events if e["event"] == "notice_follow_up") == 1
    note = s.follow_up_note()
    names_age_in_minutes = "2 Minuten" in note
    names_identity = s.notices[0].id in note
    german_check_request = "pruefe" in note and "beantwortet" in note
    ascii_transliteration = ("Gespraech" in note and "laengst" in note
                             and "loese" in note
                             and not any(ch in note for ch in "äöüß"))
    c.advance(10)
    more_events = s.tick()
    not_reported_twice = not any(e["event"] == "notice_follow_up" for e in more_events)
    return SimpleNamespace(**locals())


def test_no_follow_up_right_away_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.no_follow_up_right_away == ""


def test_still_none_after_one_minute_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.still_none_after_one_minute == ""


def test_the_follow_up_state_is_reached_after_two_minutes_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.follow_up_reached


def test_the_change_is_reported_once_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.change_reported_once


def test_the_supplement_names_the_age_in_minutes_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.names_age_in_minutes


def test_the_supplement_names_the_identity_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.names_identity


def test_the_supplement_is_german_and_asks_for_a_check(notice_follow_up_scenario):
    assert notice_follow_up_scenario.german_check_request


def test_the_supplement_uses_the_prompts_ascii_transliteration(notice_follow_up_scenario):
    assert notice_follow_up_scenario.ascii_transliteration


def test_the_follow_up_state_is_not_reported_twice_notice(notice_follow_up_scenario):
    assert notice_follow_up_scenario.not_reported_twice


# ------------------------------------ the question expires after ten minutes
@pytest.fixture
def question_expiry_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    c.advance(599)
    s.tick()
    still_open_before_limit = s.question.state == "open"
    c.advance(2)
    events = s.tick()
    expired_after_ten_minutes = s.question.state == "expired"
    expiry_reported = any(e["event"] == "question_expired" for e in events)
    expired = next(e for e in events if e["event"] == "question_expired")
    slot_free_again = s.apply({"action": "question", "text": "Und der Betrag?"})
    return SimpleNamespace(**locals())


def test_still_open_just_before_the_limit(question_expiry_scenario):
    assert question_expiry_scenario.still_open_before_limit


def test_expired_after_ten_minutes(question_expiry_scenario):
    assert question_expiry_scenario.expired_after_ten_minutes


def test_the_expiry_is_reported(question_expiry_scenario):
    assert question_expiry_scenario.expiry_reported


def test_the_expiry_event_carries_the_entrys_age(question_expiry_scenario):
    assert question_expiry_scenario.expired["age_s"] == 601.0


def test_the_slot_is_free_again_question(question_expiry_scenario):
    assert question_expiry_scenario.slot_free_again == ""


# ------------------------------------- a notice does NOT expire on a timer
def test_an_hour_later_the_notice_still_stands(fresh_state):
    s2, c2 = fresh_state
    s2.apply({"action": "notice", "kind": "value_conflict", "text": "steht"})
    c2.advance(3600)
    s2.tick()
    assert s2.notices[0].state == "open"


# ---------------- resolving the question: a visible receipt, and the slot
# freed only once it settles
@pytest.fixture
def resolving_question_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    qid = s.question.id
    resolving_returns_empty = s.resolve(qid, "die Runde hat den 6.10. bestätigt")
    entry_stays_in_place = s.question is not None and s.question.id == qid
    marked_resolved = s.question.state == "resolved"
    receipt_text_kept = s.question.resolution
    resolving_twice_rejected = s.resolve(qid, "nochmal")
    new_question_accepted = s.apply(
        {"action": "question", "text": "Und der Betrag?"})
    return SimpleNamespace(**locals())


def test_resolving_the_question_returns_empty(resolving_question_scenario):
    assert resolving_question_scenario.resolving_returns_empty == ""


def test_the_entry_stays_in_place_question(resolving_question_scenario):
    assert resolving_question_scenario.entry_stays_in_place


def test_it_is_marked_resolved_question(resolving_question_scenario):
    assert resolving_question_scenario.marked_resolved


def test_the_receipt_text_is_kept_question(resolving_question_scenario):
    assert resolving_question_scenario.receipt_text_kept == "die Runde hat den 6.10. bestätigt"


def test_resolving_twice_is_rejected_question(resolving_question_scenario):
    assert resolving_question_scenario.resolving_twice_rejected == "not open"


def test_a_new_question_is_accepted_afterwards(resolving_question_scenario):
    assert resolving_question_scenario.new_question_accepted == ""


# -------------------------------- follow-up: the question may not get
# stuck either
@pytest.fixture
def question_follow_up_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    no_follow_up_right_away = s.question.follow_up is False
    c.advance(60)
    s.tick()
    still_none_after_one_minute = s.question.follow_up is False
    c.advance(70)
    events = s.tick()
    follow_up_reached = s.question.follow_up is True
    change_reported_once = sum(
        1 for e in events if e["event"] == "question_follow_up") == 1
    note = s.follow_up_note()
    names_age_in_minutes = "2 Minuten" in note
    names_identity = s.question.id in note
    c.advance(10)
    more_events = s.tick()
    not_reported_twice = not any(
        e["event"] == "question_follow_up" for e in more_events)
    return SimpleNamespace(**locals())


def test_no_follow_up_right_away_question(question_follow_up_scenario):
    assert question_follow_up_scenario.no_follow_up_right_away


def test_still_none_after_one_minute_question(question_follow_up_scenario):
    assert question_follow_up_scenario.still_none_after_one_minute


def test_the_follow_up_state_is_reached_after_two_minutes_question(question_follow_up_scenario):
    assert question_follow_up_scenario.follow_up_reached


def test_the_change_is_reported_once_question(question_follow_up_scenario):
    assert question_follow_up_scenario.change_reported_once


def test_the_supplement_names_the_age_in_minutes_question(question_follow_up_scenario):
    assert question_follow_up_scenario.names_age_in_minutes


def test_the_supplement_names_the_identity_question(question_follow_up_scenario):
    assert question_follow_up_scenario.names_identity


def test_the_follow_up_state_is_not_reported_twice_question(question_follow_up_scenario):
    assert question_follow_up_scenario.not_reported_twice


# --------------------- the question's receipt clears itself after the
# linger time
@pytest.fixture
def question_receipt_linger_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    s.resolve(s.question.id, "geklärt")
    c.advance(3)
    s.tick()
    still_stands_after_3s = s.question is not None
    c.advance(5)
    events = s.tick()
    gone_after_linger = s.question is None
    clearing_reported = any(e["event"] == "question_cleared" for e in events)
    cleared = next(e for e in events if e["event"] == "question_cleared")
    return SimpleNamespace(**locals())


def test_the_receipt_still_stands_after_3_s_question(question_receipt_linger_scenario):
    assert question_receipt_linger_scenario.still_stands_after_3s


def test_the_receipt_is_gone_after_the_linger_time_question(question_receipt_linger_scenario):
    assert question_receipt_linger_scenario.gone_after_linger


def test_the_clearing_is_reported_for_the_trace_question(question_receipt_linger_scenario):
    assert question_receipt_linger_scenario.clearing_reported


def test_the_cleared_event_carries_the_entrys_age_question(question_receipt_linger_scenario):
    assert question_receipt_linger_scenario.cleared["age_s"] == 8.0


# ---------------------- FIX 1: a settled question is not silently
# overwritten by a new one
@pytest.fixture
def question_not_overwritten_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    old_id = s.question.id
    s.resolve(old_id, "geklärt")
    new_question_accepted = s.apply(
        {"action": "question", "text": "Und der Betrag?"})
    new_question_in_slot = (s.question is not None
                            and s.question.text == "Und der Betrag?"
                            and s.question.id != old_id)
    events = s.tick()
    displaced_event_present = any(
        e["event"] == "question_cleared" and e["id"] == old_id for e in events)
    cleared = next(e for e in events
                   if e["event"] == "question_cleared" and e["id"] == old_id)
    more_events = s.tick()
    not_repeated = not any(
        e["event"] == "question_cleared" and e["id"] == old_id
        for e in more_events)
    return SimpleNamespace(**locals())


def test_a_new_question_is_accepted_immediately_within_the_linger_window(question_not_overwritten_scenario):
    assert question_not_overwritten_scenario.new_question_accepted == ""


def test_the_new_question_is_now_in_the_slot(question_not_overwritten_scenario):
    assert question_not_overwritten_scenario.new_question_in_slot


def test_the_displaced_receipt_produced_a_question_cleared_event_on_the_next_tick(question_not_overwritten_scenario):
    assert question_not_overwritten_scenario.displaced_event_present


def test_the_state_carried_is_the_settled_state_it_had(question_not_overwritten_scenario):
    assert question_not_overwritten_scenario.cleared["state"] == "resolved"


def test_the_event_carries_the_entrys_age_fix1(question_not_overwritten_scenario):
    assert question_not_overwritten_scenario.cleared["age_s"] == 0.0


def test_tick_is_idempotent_the_event_is_not_repeated_fix1(question_not_overwritten_scenario):
    assert question_not_overwritten_scenario.not_repeated


# ============================================ C1: unencodable foreign text
GIFT = "Robo \ud83d hat verstanden"


@pytest.fixture
def unencodable_text_scenario(fresh_state):
    s, c = fresh_state
    poisoned_mirror_accepted = s.apply({"action": "mirror", "text": GIFT})
    stored_can_be_encoded = s.mirror.encode("utf-8", "strict")
    readable_part_survives = "hat verstanden" in s.mirror
    whole_content_encodable = json.dumps(
        s.content(), ensure_ascii=False).encode("utf-8")
    later_proposal_arrives = (
        s.apply({"action": "decision", "time": "19:43", "text": "kein Pitch"}) == ""
        and s.decisions[-1].text == "kein Pitch")
    s.apply({"action": "notice", "kind": "value_conflict",
             "text": "**Frist** \udcff 6.10."})
    s.apply({"action": "question", "text": "Fünfter \ud800 oder sechster?"})
    notice_and_question_scrubbed = json.dumps(
        s.content(), ensure_ascii=False).encode("utf-8")
    only_unencodable_is_no_text = s.apply({"action": "mirror", "text": "\ud83d"})
    old_mirror_survives_rejection = "hat verstanden" in s.mirror
    return SimpleNamespace(**locals())


def test_the_poisoned_mirror_is_accepted_without_raising(unencodable_text_scenario):
    assert unencodable_text_scenario.poisoned_mirror_accepted == ""


def test_what_was_stored_can_be_encoded(unencodable_text_scenario):
    assert unencodable_text_scenario.stored_can_be_encoded is not None


def test_the_readable_part_survives(unencodable_text_scenario):
    assert unencodable_text_scenario.readable_part_survives


def test_the_whole_content_is_encodable(unencodable_text_scenario):
    assert unencodable_text_scenario.whole_content_encodable is not None


def test_a_later_proposal_still_arrives(unencodable_text_scenario):
    assert unencodable_text_scenario.later_proposal_arrives


def test_notice_and_question_are_scrubbed_too(unencodable_text_scenario):
    assert unencodable_text_scenario.notice_and_question_scrubbed is not None


def test_a_value_that_is_only_unencodable_counts_as_no_text(unencodable_text_scenario):
    assert unencodable_text_scenario.only_unencodable_is_no_text == "no text"


def test_and_the_old_mirror_survives_that_rejection(unencodable_text_scenario):
    assert unencodable_text_scenario.old_mirror_survives_rejection


def test_scrub_leaves_clean_text_alone():
    assert scrub("Grüße 🎧 — 12.000 €") == "Grüße 🎧 — 12.000 €"


# ================================= I1: the call-out quote is real evidence
GESAGT = "Wir muessen los.\nRobo, zeig uns bitte die vier Straenge."


def test_the_quote_as_spoken_matches():
    assert quote_is_spoken("zeig uns bitte die vier Straenge", GESAGT)


def test_case_does_not_matter():
    assert quote_is_spoken("ZEIG UNS BITTE die vier Straenge", GESAGT)


def test_line_breaks_and_double_spaces_do_not_matter():
    assert quote_is_spoken("Robo,   zeig uns\nbitte die vier Straenge", GESAGT)


def test_surrounding_quotation_marks_do_not_matter():
    assert quote_is_spoken('„zeig uns bitte die vier Straenge"', GESAGT)


def test_an_invented_instruction_does_not_match():
    assert not quote_is_spoken("zeig uns die Kalkulation", GESAGT)


def test_a_paraphrase_does_not_match():
    assert not quote_is_spoken("zeig die vier Straenge uns bitte", GESAGT)


def test_an_empty_quote_does_not_match():
    assert not quote_is_spoken("   ", GESAGT)


def test_nothing_matches_when_nothing_was_said():
    assert not quote_is_spoken("zeig uns bitte die vier Straenge", "")


@pytest.fixture
def call_out_evidence_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "main", "markdown": "## Alt",
             "quote": "zeig uns bitte die vier Straenge"}, GESAGT)
    verified_call_out_replaces_main = s.main.markdown
    unverified_quote_rejected = s.apply(
        {"action": "main", "markdown": "## Neu",
         "quote": "Robo, tausch die Flaeche aus"}, GESAGT)
    main_area_untouched = s.main.markdown
    no_spoken_text_no_call_out = s.apply(
        {"action": "main", "markdown": "## Neu",
         "quote": "zeig uns bitte die vier Straenge"})
    return SimpleNamespace(**locals())


def test_a_verified_call_out_replaces_the_main_area(call_out_evidence_scenario):
    assert call_out_evidence_scenario.verified_call_out_replaces_main == "## Alt"


def test_an_unverified_quote_is_rejected(call_out_evidence_scenario):
    assert call_out_evidence_scenario.unverified_quote_rejected == "quote not spoken"


def test_and_the_main_area_stands_untouched(call_out_evidence_scenario):
    assert call_out_evidence_scenario.main_area_untouched == "## Alt"


def test_without_any_spoken_text_no_call_out_can_be_proven(call_out_evidence_scenario):
    assert call_out_evidence_scenario.no_spoken_text_no_call_out == "quote not spoken"


# ====================================== I3: nothing disappears without a trace
@pytest.fixture
def unrecorded_parked_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "notice", "kind": "value_conflict", "text": "eins"})
    s.apply({"action": "notice", "kind": "decision_breach", "text": "zwei"})
    third_parked = s.apply(
        {"action": "notice", "kind": "value_conflict",
         "text": "**Termin** Montag gegen Dienstag"})
    offen = s.unrecorded()
    parked_text_kept = [e["text"] for e in offen]
    marked_as_parked = offen[0]["kind"]
    c.advance(RECEIPT_LINGER_S + 1)
    s.tick()
    display_goes_duty_stays = s.parked_entries == [] and len(s.unrecorded()) == 1
    s.mark_recorded(offen)
    stops_riding_along = s.unrecorded()
    return SimpleNamespace(**locals())


def test_the_third_notice_is_parked_i3(unrecorded_parked_scenario):
    assert unrecorded_parked_scenario.third_parked == "parked"


def test_the_parked_text_is_kept_for_the_protocol(unrecorded_parked_scenario):
    assert unrecorded_parked_scenario.parked_text_kept == [
        "**Termin** Montag gegen Dienstag"]


def test_it_is_marked_as_parked(unrecorded_parked_scenario):
    assert unrecorded_parked_scenario.marked_as_parked == "parked"


def test_the_display_line_goes_the_duty_stays(unrecorded_parked_scenario):
    assert unrecorded_parked_scenario.display_goes_duty_stays


def test_once_a_run_was_told_it_stops_riding_along(unrecorded_parked_scenario):
    assert unrecorded_parked_scenario.stops_riding_along == []


@pytest.fixture
def unrecorded_expired_question_scenario():
    c = Clock()
    s = StageState(now=c.now, clock=c.clock)
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    c.advance(601)
    s.tick()
    expired_handed_on_too = [e["kind"] for e in s.unrecorded()]
    with_its_text = s.unrecorded()[0]["text"]
    failed_run_does_not_lose_it = len(s.unrecorded())
    s.mark_recorded([{"kind": "expired", "text": "Fünfter oder sechster Oktober?"}])
    goes_by_identity_not_value = len(s.unrecorded())
    return SimpleNamespace(**locals())


def test_an_expired_question_is_handed_on_too(unrecorded_expired_question_scenario):
    assert unrecorded_expired_question_scenario.expired_handed_on_too == ["expired"]


def test_with_its_text_so_it_can_be_written_down(unrecorded_expired_question_scenario):
    assert unrecorded_expired_question_scenario.with_its_text == "Fünfter oder sechster Oktober?"


def test_a_failed_run_does_not_lose_it_only_mark_recorded_does(unrecorded_expired_question_scenario):
    assert unrecorded_expired_question_scenario.failed_run_does_not_lose_it == 1


def test_mark_recorded_goes_by_identity_not_by_an_equal_looking_copy(unrecorded_expired_question_scenario):
    assert unrecorded_expired_question_scenario.goes_by_identity_not_value == 1


@pytest.fixture
def unrecorded_cap_scenario():
    c = Clock()
    s_viele = StageState(now=c.now, clock=c.clock)
    s_viele.apply({"action": "notice", "kind": "value_conflict", "text": "eins"})
    s_viele.apply({"action": "notice", "kind": "decision_breach", "text": "zwei"})
    for i in range(40):
        s_viele.apply({"action": "notice", "kind": "value_conflict",
                       "text": f"geparkt {i}"})
    return s_viele


def test_the_list_of_what_still_owes_the_protocol_is_capped(unrecorded_cap_scenario):
    assert len(unrecorded_cap_scenario.unrecorded()) == 20


def test_and_the_newest_survive(unrecorded_cap_scenario):
    assert unrecorded_cap_scenario.unrecorded()[-1]["text"] == "geparkt 39"


# ================================== I11: a restart does not wipe the stage
@pytest.fixture
def restart_restore_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "mirror", "text": "Robo hat das Budget verstanden."})
    s.apply({"action": "headline", "text": "Vorbereitung Projekt-Call"})
    s.apply({"action": "main", "markdown": "## Vier Stränge\n- Update",
             "quote": "zeig die vier Stränge"}, "Robo, zeig die vier Stränge.")
    s.apply({"action": "decision", "time": "19:43", "text": "kein Pitch"})
    s.apply({"action": "notice", "kind": "value_conflict", "text": "**Frist** 6.10."})
    s.apply({"action": "notice", "kind": "decision_breach", "text": "**Beschluss** X"})
    s.apply({"action": "notice", "kind": "value_conflict", "text": "geparkt"})
    s.apply({"action": "question", "text": "Fünfter oder sechster Oktober?"})
    s.apply({"action": "links", "items": [{"file": "usecases", "label": "Use-Cases"}]})
    s.apply({"action": "call_answer", "text": "Robo: Übersicht ..."})
    s.resolve("N2", "geklärt")
    c.advance(30)
    gespeichert = s.content()

    c2 = Clock()
    neu = StageState(now=c2.now, clock=c2.clock)
    c2.advance(500)
    summe = neu.restore(gespeichert)
    return SimpleNamespace(**locals())


def test_the_mirror_is_back(restart_restore_scenario):
    assert restart_restore_scenario.neu.mirror == "Robo hat das Budget verstanden."


def test_the_call_answer_is_back(restart_restore_scenario):
    assert restart_restore_scenario.neu.call_answer == "Robo: Übersicht ..."


def test_the_headline_is_back(restart_restore_scenario):
    assert restart_restore_scenario.neu.headline == "Vorbereitung Projekt-Call"


def test_the_main_area_is_back(restart_restore_scenario):
    assert restart_restore_scenario.neu.main.markdown == "## Vier Stränge\n- Update"


def test_the_decision_is_back(restart_restore_scenario):
    assert [d.text for d in restart_restore_scenario.neu.decisions] == ["kein Pitch"]


def test_the_open_notice_is_back(restart_restore_scenario):
    assert [n.text for n in restart_restore_scenario.neu.notices] == ["**Frist** 6.10."]


def test_the_receipt_is_not_restored(restart_restore_scenario):
    assert all(n.state == "open" for n in restart_restore_scenario.neu.notices)


def test_the_question_is_back(restart_restore_scenario):
    neu = restart_restore_scenario.neu
    assert neu.question is not None and neu.question.id == "Q1"


def test_the_links_are_back(restart_restore_scenario):
    assert restart_restore_scenario.neu.links == [
        {"file": "usecases", "label": "Use-Cases"}]


def test_the_parked_counter_is_back(restart_restore_scenario):
    assert restart_restore_scenario.neu.parked == 1


def test_the_summary_reports_what_came_back(restart_restore_scenario):
    assert restart_restore_scenario.summe == {
        "decisions": 1, "notices": 1, "question": 1}


def test_an_id_whose_entry_is_not_restored_is_still_claimed_next_is_n3(restart_restore_scenario):
    neu = restart_restore_scenario.neu
    assert (neu.apply({"action": "notice", "kind": "value_conflict",
                       "text": "neu"}) == ""
            and neu.notices[-1].id == "N3")


def test_and_the_next_question_is_q2(restart_restore_scenario):
    neu = restart_restore_scenario.neu
    assert (neu.resolve("Q1", "geklärt") == ""
            and neu.apply({"action": "question", "text": "Und der Betrag?"}) == ""
            and neu.question.id == "Q2")


def test_and_the_next_decision_is_d2(restart_restore_scenario):
    neu = restart_restore_scenario.neu
    assert (neu.apply({"action": "decision", "time": "19:50",
                       "text": "zweiter"}) == ""
            and neu.decisions[-1].id == "D2")


def test_and_the_next_main_area_is_m2(restart_restore_scenario):
    neu = restart_restore_scenario.neu
    assert (neu.apply({"action": "main", "markdown": "## Neu", "quote": "zeig neu"},
                      "Robo, zeig neu.") == ""
            and neu.main.id == "M2")


# ---------------- restore: the ages survive the restart, the clocks keep
# running
@pytest.fixture
def restore_ages_survive_scenario():
    c3 = Clock()
    alt = StageState(now=c3.now, clock=c3.clock)
    alt.apply({"action": "notice", "kind": "value_conflict", "text": "steht"})
    alt.apply({"action": "question", "text": "Fünfter oder sechster?"})
    c3.wall = c3.wall.replace(minute=25)
    gespeichert = alt.content()
    c4 = Clock()
    frisch_state = StageState(now=c4.now, clock=c4.clock)
    c4.wall = c4.wall.replace(minute=25)
    frisch_state.restore(gespeichert)
    ereignisse = frisch_state.tick()
    return SimpleNamespace(**locals())


def test_a_notice_that_stood_for_eleven_minutes_is_in_follow_up_at_once(restore_ages_survive_scenario):
    assert restore_ages_survive_scenario.frisch_state.notices[0].follow_up is True


def test_a_question_overdue_by_a_minute_expires_at_once(restore_ages_survive_scenario):
    assert restore_ages_survive_scenario.frisch_state.question.state == "expired"


def test_the_expiry_is_reported_for_the_trace_restore(restore_ages_survive_scenario):
    assert any(e["event"] == "question_expired"
              for e in restore_ages_survive_scenario.ereignisse)


# ------------------------- restore: a file that cannot be taken over
# says so
@pytest.mark.parametrize("schrott", [{"version": 1}, {"version": None}, [], "text"])
def test_a_broken_file_is_refused_with_a_valueerror(fresh_state, schrott):
    kaputt, c = fresh_state
    with pytest.raises(ValueError):
        kaputt.restore(schrott)


def test_a_half_broken_file_leaves_an_empty_but_usable_state(fresh_state):
    teilweise, c = fresh_state
    teilweise.restore({"version": 2, "notices": "kein List", "decisions": [1, 2],
                       "question": "keine Frage", "main": 7, "links": None,
                       "parked": "viele"})
    assert (teilweise.notices == [] and teilweise.decisions == []
            and teilweise.question is None and teilweise.parked == 0)


# ============== N1: the surface follows the conversation, N5: with brakes
# ------------------------- topic: a change of subject sets the surface,
# without a call-out
@pytest.fixture
def topic_change_scenario(fresh_state):
    s, c = fresh_state
    empty_history_empty_surface = s.main.id == "" and s.main.markdown == ""
    topic_needs_no_call_out = s.apply(
        {"action": "topic", "title": "Budget 2027",
         "markdown": "## Budget 2027\n- Rahmen steht"})
    on_the_surface = s.main.markdown.startswith("## Budget 2027")
    title_is_history_label = s.main.title
    got_an_identity = s.main.id
    no_quote_kept = s.main.quote
    topic_without_title_rejected = s.apply(
        {"action": "topic", "markdown": "## X"})
    topic_without_markdown_rejected = s.apply({"action": "topic", "title": "X"})
    c_zeile = Clock()
    s_zeile = StageState(now=c_zeile.now, clock=c_zeile.clock)
    s_zeile.apply({"action": "topic", "title": "Recht\n  und Ordnung",
                  "markdown": "## R"})
    title_forced_onto_one_line = s_zeile.main.title
    return SimpleNamespace(**locals())


def test_an_empty_history_is_an_empty_surface(topic_change_scenario):
    assert topic_change_scenario.empty_history_empty_surface


def test_a_topic_needs_neither_a_call_out_nor_a_quote(topic_change_scenario):
    assert topic_change_scenario.topic_needs_no_call_out == ""


def test_it_is_on_the_surface(topic_change_scenario):
    assert topic_change_scenario.on_the_surface


def test_the_title_is_the_history_label(topic_change_scenario):
    assert topic_change_scenario.title_is_history_label == "Budget 2027"


def test_it_got_an_identity_like_every_other_surface(topic_change_scenario):
    assert topic_change_scenario.got_an_identity == "M1"


def test_no_quote_is_kept_for_it_nobody_gave_an_order(topic_change_scenario):
    assert topic_change_scenario.no_quote_kept == ""


def test_a_topic_without_a_title_is_rejected(topic_change_scenario):
    assert topic_change_scenario.topic_without_title_rejected == "no title"


def test_a_topic_without_markdown_is_rejected(topic_change_scenario):
    assert topic_change_scenario.topic_without_markdown_rejected == "no markdown"


def test_a_title_is_forced_onto_one_line_and_nothing_of_it_is_dropped(topic_change_scenario):
    assert topic_change_scenario.title_forced_onto_one_line == "Recht und Ordnung"


# --------------------- topic: the minimum hold time -- and the call-out
# that ignores it
@pytest.fixture
def topic_min_hold_time_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "topic", "title": "Budget 2027",
             "markdown": "## Budget 2027\n- Rahmen steht"})
    c.advance(TOPIC_MIN_HOLD_S - 1)
    under_hold_refused = s.apply(
        {"action": "topic", "title": "Rechtsform", "markdown": "## Rechtsform"})
    surface_did_not_move = s.main.markdown.startswith("## Budget 2027")
    nothing_appended_to_history = [m.title for m in s.history]
    call_out_accepted_under_hold = s.apply(
        {"action": "main", "markdown": "## Kosten\n- 12.000",
         "quote": "zeig die Kosten"}, "Robo, zeig die Kosten.")
    order_on_the_surface = s.main.markdown.startswith("## Kosten")
    label_read_off_surface = s.main.title
    both_surfaces_in_history = [m.id for m in s.history]
    c.advance(TOPIC_MIN_HOLD_S - 1)
    call_out_restarted_hold_time = s.apply(
        {"action": "topic", "title": "Rechtsform", "markdown": "## Rechtsform"})
    c.advance(2)
    topic_goes_through_once_stood_long_enough = s.apply(
        {"action": "topic", "title": "Rechtsform", "markdown": "## Rechtsform"})

    c_abs = Clock()
    s_abs = StageState(now=c_abs.now, clock=c_abs.clock)
    s_abs.apply({"action": "topic", "title": "eins", "markdown": "## eins"})
    c_abs.advance(10)
    ten_seconds_still_refused = s_abs.apply(
        {"action": "topic", "title": "zwei", "markdown": "## zwei"})
    surface_still_shows_first = s_abs.main.title
    return SimpleNamespace(**locals())


def test_a_topic_under_the_minimum_hold_time_is_refused(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.under_hold_refused == "too soon"


def test_and_the_surface_did_not_move(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.surface_did_not_move


def test_and_nothing_was_appended_to_the_history(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.nothing_appended_to_history == ["Budget 2027"]


def test_a_call_out_is_accepted_under_the_minimum_hold_time_all_the_same(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.call_out_accepted_under_hold == ""


def test_the_order_is_on_the_surface(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.order_on_the_surface


def test_its_label_is_read_off_the_surface_not_invented(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.label_read_off_surface == "Kosten"


def test_both_surfaces_are_in_the_history(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.both_surfaces_in_history == ["M1", "M2"]


def test_the_call_out_restarted_the_hold_time_for_the_next_topic(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.call_out_restarted_hold_time == "too soon"


def test_once_the_surface_has_stood_long_enough_the_topic_goes_through(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.topic_goes_through_once_stood_long_enough == ""


def test_ten_seconds_after_a_change_an_automatic_one_is_still_refused(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.ten_seconds_still_refused == "too soon"


def test_and_the_surface_still_shows_the_first_one(topic_min_hold_time_scenario):
    assert topic_min_hold_time_scenario.surface_still_shows_first == "eins"


# ---------------------------------- topic: the cap per window bites and
# lets go again
@pytest.fixture
def topic_window_cap_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "topic", "title": "eins", "markdown": "## eins"})
    c.advance(TOPIC_MIN_HOLD_S + 1)
    second_change_in_window_refused = s.apply(
        {"action": "topic", "title": "zwei", "markdown": "## zwei"})
    surface_stands = s.main.title
    c.advance(TOPIC_WINDOW_S - TOPIC_MIN_HOLD_S)
    next_one_goes_through = s.apply(
        {"action": "topic", "title": "zwei", "markdown": "## zwei"})
    it_is_in_the_history = [m.title for m in s.history]

    c2 = Clock()
    s2 = StageState(now=c2.now, clock=c2.clock)
    s2.apply({"action": "main", "markdown": "## A", "quote": "zeig A"}, "Robo, zeig A.")
    c2.advance(TOPIC_MIN_HOLD_S + 1)
    call_out_does_not_use_up_window = s2.apply(
        {"action": "topic", "title": "B", "markdown": "## B"})
    return SimpleNamespace(**locals())


def test_the_second_automatic_change_inside_one_window_is_refused(topic_window_cap_scenario):
    assert topic_window_cap_scenario.second_change_in_window_refused == "too many"


def test_the_surface_stands(topic_window_cap_scenario):
    assert topic_window_cap_scenario.surface_stands == "eins"


def test_once_the_window_has_passed_the_next_one_goes_through(topic_window_cap_scenario):
    assert topic_window_cap_scenario.next_one_goes_through == ""


def test_and_it_is_in_the_history(topic_window_cap_scenario):
    assert topic_window_cap_scenario.it_is_in_the_history == ["eins", "zwei"]


def test_a_call_out_does_not_use_up_the_windows_automatic_change(topic_window_cap_scenario):
    assert topic_window_cap_scenario.call_out_does_not_use_up_window == ""


# ------------------------------ topic: a long surface is not capped
# either (A3)
def test_a_topic_keeps_all_twenty_lines(fresh_state):
    s, c = fresh_state
    lang = "\n".join(f"Zeile {i}" for i in range(1, 21))
    s.apply({"action": "topic", "title": "Lange Liste", "markdown": lang})
    assert s.main.markdown.count("\n") + 1 == 20


def test_nothing_is_collapsed_for_a_topic(fresh_state):
    s, c = fresh_state
    lang = "\n".join(f"Zeile {i}" for i in range(1, 21))
    s.apply({"action": "topic", "title": "Lange Liste", "markdown": lang})
    assert s.main.collapsed_lines == 0


# ================ N4: nothing is cut from the back -- there is no pointer
# here
@pytest.fixture
def history_append_scenario(fresh_state):
    s, c = fresh_state
    for i in range(5):
        c.advance(TOPIC_WINDOW_S + 1)
        s.apply({"action": "topic", "title": f"Thema {i}",
                 "markdown": f"## Thema {i}"})
    five_surfaces_oldest_first = [m.title for m in s.history]
    vorher = [m.id for m in s.history]
    c.advance(TOPIC_WINDOW_S + 1)
    s.apply({"action": "topic", "title": "Thema 5", "markdown": "## Thema 5"})
    went_to_back_cut_nothing = [m.id for m in s.history]
    no_pointer_in_file = ("cursor" not in json.dumps(s.content())
                          and "position" not in json.dumps(s.content()))
    return SimpleNamespace(**locals())


def test_five_surfaces_oldest_first(history_append_scenario):
    assert history_append_scenario.five_surfaces_oldest_first == [
        f"Thema {i}" for i in range(5)]


def test_the_new_entry_went_to_the_back_and_cut_nothing_off(history_append_scenario):
    assert history_append_scenario.went_to_back_cut_nothing == [
        *history_append_scenario.vorher, "M6"]


def test_no_pointer_travels_in_the_file(history_append_scenario):
    assert history_append_scenario.no_pointer_in_file


# ---- history is capped, and content() carries every position, main
# stays what it was
@pytest.fixture
def history_cap_scenario(fresh_state):
    s, c = fresh_state
    for i in range(MAX_HISTORY + 3):
        c.advance(TOPIC_WINDOW_S + 1)
        s.apply({"action": "topic", "title": f"T{i}", "markdown": f"## T{i}"})
    history_is_capped = len(s.history)
    only_oldest_falls_out = (s.history[0].title == "T3"
                             and s.history[-1].title == f"T{MAX_HISTORY + 2}")
    d = s.content()
    return SimpleNamespace(**locals())


def test_the_history_is_capped(history_cap_scenario):
    assert history_cap_scenario.history_is_capped == MAX_HISTORY


def test_only_the_oldest_falls_out_at_the_front(history_cap_scenario):
    assert history_cap_scenario.only_oldest_falls_out


def test_the_history_reaches_content(history_cap_scenario):
    assert len(history_cap_scenario.d["history"]) == MAX_HISTORY


def test_every_position_carries_id_title_markdown_set_at_and_its_collapse(history_cap_scenario):
    assert all(set(e) == {"id", "title", "markdown", "set_at",
                          "collapsed_lines", "collapsed_markdown"}
              for e in history_cap_scenario.d["history"])


def test_main_is_still_its_own_key_an_old_page_must_keep_working(history_cap_scenario):
    d = history_cap_scenario.d
    assert (d["main"]["id"] == d["history"][-1]["id"]
            and d["main"]["markdown"] == d["history"][-1]["markdown"])


def test_the_version_did_not_move_the_change_only_adds_a_key(history_cap_scenario):
    assert history_cap_scenario.d["version"] == 2


def test_the_history_is_json_serialisable(history_cap_scenario):
    assert isinstance(json.dumps(history_cap_scenario.d), str)


# ------------------ history: it survives content() -> restore(), titles
# and identities
@pytest.fixture
def history_restore_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "topic", "title": "Budget 2027",
             "markdown": "## Budget 2027\n- Rahmen steht"})
    c.advance(TOPIC_WINDOW_S + 1)
    s.apply({"action": "main", "markdown": "## Kosten\n- 12.000",
             "quote": "zeig die Kosten"}, "Robo, zeig die Kosten.")
    c.advance(TOPIC_WINDOW_S + 1)
    s.apply({"action": "topic", "title": "Rechtsform", "markdown": "## Rechtsform"})
    gespeichert = s.content()

    c2 = Clock()
    neu = StageState(now=c2.now, clock=c2.clock)
    neu.restore(gespeichert)
    every_position_came_back = [m.title for m in neu.history]
    with_its_identity = [m.id for m in neu.history]
    round_still_looking = neu.main.id == "M3" and neu.main.markdown == "## Rechtsform"
    no_identity_handed_out_twice = (
        neu.apply({"action": "main", "markdown": "## Neu", "quote": "zeig neu"},
                  "Robo, zeig neu.") == "" and neu.main.id == "M4")

    c_spaeter = Clock()
    spaeter = StageState(now=c_spaeter.now, clock=c_spaeter.clock)
    spaeter.restore(gespeichert)
    hold_time_survives_restart = spaeter.apply(
        {"action": "topic", "title": "X", "markdown": "## X"})

    c_kaputt = Clock()
    kaputt = StageState(now=c_kaputt.now, clock=c_kaputt.clock)
    kaputt.restore({"version": 2, "history": [
        {"id": "M1", "title": "eins", "markdown": "## eins"},
        {"id": "M2"},                                # no markdown
        "kein Objekt",
        {"markdown": "## ohne Kennung"},             # no identity
        {"id": "M9", "title": "neun", "markdown": "## neun"}]})
    broken_positions_skipped = [m.title for m in kaputt.history]
    skipped_identity_claimed_next_m10 = (
        kaputt.apply({"action": "main", "markdown": "## Neu", "quote": "zeig neu"},
                     "Robo, zeig neu.") == "" and kaputt.main.id == "M10")

    c_voll = Clock()
    voll = StageState(now=c_voll.now, clock=c_voll.clock)
    voll.restore({"version": 2, "history": [
        {"id": f"M{i}", "title": f"T{i}", "markdown": f"## {i}"}
        for i in range(1, MAX_HISTORY + 6)]})
    cap_holds_after_restart = len(voll.history)
    newest_survived = voll.history[-1].title

    c_alt = Clock()
    alt = StageState(now=c_alt.now, clock=c_alt.clock)
    alt.restore({"version": 2, "main": {"id": "M7",
                                        "markdown": "## Vier Stränge\n- Update",
                                        "set_at": "2026-09-08T19:10:00",
                                        "collapsed_lines": 0,
                                        "collapsed_markdown": ""}})
    file_before_history_is_history_of_one = [m.id for m in alt.history]
    label_derived_from_own_surface = alt.history[0].title
    return SimpleNamespace(**locals())


def test_every_position_came_back(history_restore_scenario):
    assert history_restore_scenario.every_position_came_back == [
        "Budget 2027", "Kosten", "Rechtsform"]


def test_with_its_identity(history_restore_scenario):
    assert history_restore_scenario.with_its_identity == ["M1", "M2", "M3"]


def test_and_the_round_still_looks_at_what_it_was_looking_at(history_restore_scenario):
    assert history_restore_scenario.round_still_looking


def test_no_identity_is_handed_out_a_second_time_after_the_restart(history_restore_scenario):
    assert history_restore_scenario.no_identity_handed_out_twice


def test_the_minimum_hold_time_survives_the_restart(history_restore_scenario):
    assert history_restore_scenario.hold_time_survives_restart == "too soon"


def test_broken_positions_are_skipped_not_guessed_at(history_restore_scenario):
    assert history_restore_scenario.broken_positions_skipped == ["eins", "neun"]


def test_the_identity_of_a_skipped_position_is_claimed_all_the_same_next_m10(history_restore_scenario):
    assert history_restore_scenario.skipped_identity_claimed_next_m10


def test_the_cap_holds_after_a_restart_too(history_restore_scenario):
    assert history_restore_scenario.cap_holds_after_restart == MAX_HISTORY


def test_and_it_is_the_newest_that_survived(history_restore_scenario):
    assert history_restore_scenario.newest_survived == f"T{MAX_HISTORY + 5}"


def test_a_file_from_before_the_history_comes_back_as_a_history_of_one(history_restore_scenario):
    assert history_restore_scenario.file_before_history_is_history_of_one == ["M7"]


def test_with_a_label_derived_from_its_own_surface(history_restore_scenario):
    assert history_restore_scenario.label_derived_from_own_surface == "Vier Stränge"


# ---------------- the history label of a call-out is derived, never
# invented
def test_the_first_heading_wins():
    assert derived_title("## Vier Stränge\n- Update") == "Vier Stränge"


def test_without_a_heading_the_first_line_does():
    assert derived_title("- Update\n- mehr") == "Update"


def test_leading_blank_lines_are_skipped():
    assert derived_title("\n\n# Kosten") == "Kosten"


def test_nothing_in_nothing_out_no_invention():
    assert derived_title("") == ""


def test_a_long_label_is_cut_to_one_lines_worth():
    assert len(derived_title("# " + "x" * 200)) == 80


# ------------------------------------------------- a graph is a surface in
# the history (entwurf-graph-schirm section 4)
GRAPH = {"seed": "a.md",
         "nodes": [{"id": "a.md", "label": "a", "ring": 0,
                    "seed": True, "grace": False},
                   {"id": "b.md", "label": "b", "ring": 1,
                    "seed": False, "grace": False}],
         "edges": [{"id": "w:a.md|b.md", "source": "a.md", "target": "b.md",
                    "kind": "wikilink", "weight": 1.0}],
         "counts": {"notes": 869, "unresolved": 0, "ambiguous": 0,
                    "tags_capped": 27}}


def _realistic_graph(n_nodes: int = 47, n_edges: int = 304) -> dict:
    """A graph payload sized like a real vault neighbourhood -- id/label
    lengths match what `graph/graph_build.py` actually produces (full
    vault-relative paths, note titles), not the two-node fixture `GRAPH`
    above. 47 nodes / 304 edges is the scale a real seed in a real vault
    measured at (reviewer note, C2): raw JSON for one such position is
    ~160 kB, not the ~300 bytes `GRAPH` gives you.
    """
    nodes = [{"id": f"30-Entities/projekt-testraum/kategorie-{i % 6:02d}/"
                    f"notiz-{i:03d}-mit-einem-vault-typischen-langen-"
                    "dateinamen.md",
              "label": f"Notiz {i:03d} — ein Titel in ueblicher "
                       "Laenge fuer eine echte Vault-Notiz",
              "ring": i % 4, "seed": i == 0, "grace": False}
             for i in range(n_nodes)]
    edges = [{"id": f"w:{i:04d}", "kind": "wikilink", "weight": 1.0,
              "source": nodes[i % n_nodes]["id"],
              "target": nodes[(i * 7 + 3) % n_nodes]["id"]}
             for i in range(n_edges)]
    return {"seed": nodes[0]["id"], "nodes": nodes, "edges": edges,
            "counts": {"notes": 869, "unresolved": 0, "ambiguous": 0,
                       "tags_capped": 27}}


@pytest.fixture
def graph_surface_scenario():
    uhr = [1000.0]
    s = StageState(now=lambda: uhr[0])
    graph_puts_graph_surface_up = (
        s.apply({"action": "graph", "title": "Vault-Graph", "graph": GRAPH}) == ""
        and s.main.kind == "graph" and s.main.graph["seed"] == "a.md")
    brakes_apply_to_graph_too = s.apply(
        {"action": "graph", "title": "Nochmal", "graph": GRAPH})
    inhalt = s.content()
    content_carries_kind_and_graph = (
        inhalt["history"][-1]["kind"] == "graph"
        and inhalt["history"][-1]["graph"]["nodes"][1]["id"] == "b.md")

    alt = {"version": 2, "updated": "2026-09-08T19:12:04",
           "mirror": "", "headline": "",
           "main": {"id": "M7", "markdown": "# Alt", "set_at":
                    "2026-09-08T19:12:04", "collapsed_lines": 0,
                    "collapsed_markdown": ""},
           "history": [{"id": "M7", "title": "Alt", "markdown": "# Alt",
                        "set_at": "2026-09-08T19:12:04",
                        "collapsed_lines": 0, "collapsed_markdown": ""}]}
    s2 = StageState(now=lambda: 1000.0)
    s2.restore(alt)
    position_without_kind_is_markdown = (
        s2.main.kind == "markdown" and s2.main.markdown == "# Alt")
    file_before_call_answer_restores_empty = s2.call_answer

    s3 = StageState(now=lambda: 1000.0)
    s3.restore(s.content())
    graph_position_survives_a_restart = (
        s3.main.kind == "graph" and s3.main.graph["edges"][0]["kind"] == "wikilink")

    kaputt = {k: v for k, v in alt.items() if k != "main"}
    kaputt["history"] = [{"id": "M9", "title": "Leer", "kind": "graph",
                          "set_at": "2026-09-08T19:12:04"}]
    s4 = StageState(now=lambda: 1000.0)
    s4.restore(kaputt)
    graph_row_without_payload_skipped = (
        s4.history == [] and s4._next_id("M") == "M10")
    return SimpleNamespace(**locals())


def test_a_graph_action_puts_a_graph_surface_up(graph_surface_scenario):
    assert graph_surface_scenario.graph_puts_graph_surface_up


def test_the_brakes_of_the_automatic_path_apply_to_it_too(graph_surface_scenario):
    assert graph_surface_scenario.brakes_apply_to_graph_too == TOO_SOON


def test_content_carries_kind_and_graph_for_that_position(graph_surface_scenario):
    assert graph_surface_scenario.content_carries_kind_and_graph


def test_a_history_position_without_a_kind_comes_back_as_markdown(graph_surface_scenario):
    assert graph_surface_scenario.position_without_kind_is_markdown


def test_a_file_from_before_call_answer_existed_restores_it_as_empty(graph_surface_scenario):
    assert graph_surface_scenario.file_before_call_answer_restores_empty == ""


def test_a_graph_position_survives_a_restart(graph_surface_scenario):
    assert graph_surface_scenario.graph_position_survives_a_restart


def test_a_graph_row_without_a_payload_is_skipped_its_id_is_still_claimed(graph_surface_scenario):
    assert graph_surface_scenario.graph_row_without_payload_skipped


# ----------------- twenty graph positions: only the newest keeps its
# drawing
@pytest.fixture
def twenty_graph_positions_scenario():
    graph_realistic = _realistic_graph()
    uhr5 = [1000.0]
    s5 = StageState(now=lambda: uhr5[0])
    for i in range(25):
        uhr5[0] += 120.0
        s5.apply({"action": "graph", "title": f"Stand {i}", "graph": graph_realistic})
    zeilen = s5.content()["history"]
    voll = [z for z in zeilen if "nodes" in z.get("graph", {})]
    only_newest_keeps_full_drawing = len(s5.history) == 20 and voll == [zeilen[-1]]
    ein_graph = len(json.dumps(graph_realistic).encode("utf-8"))
    gross = len(json.dumps(s5.content()).encode("utf-8"))
    costs_little_more_than_one_drawing = gross < 2 * ein_graph
    return SimpleNamespace(**locals())


def test_only_the_newest_of_twenty_graph_positions_keeps_its_full_drawing(twenty_graph_positions_scenario):
    assert twenty_graph_positions_scenario.only_newest_keeps_full_drawing


def test_twenty_graph_positions_cost_little_more_than_the_one_full_drawing(twenty_graph_positions_scenario):
    assert twenty_graph_positions_scenario.costs_little_more_than_one_drawing


# ------------------------------------------------ pending announcements
# "gleich kommt was": the consumer knows something is about to reach the
# stage before the surface itself exists. The label is set by CODE, the
# trace carries no label and no text.
@pytest.fixture
def pending_announce_and_settle_scenario(fresh_state):
    s, c = fresh_state
    kinds_are_the_seven_of_the_contract = PENDING_KINDS == {
        "topic", "main", "research", "document", "question", "decision", "entry"}
    timeout_is_sixty_seconds = PENDING_TIMEOUT_S == 60.0
    at_most_three_at_once = MAX_PENDING == 3
    pid = s.announce("research", "Recherche zu Ortsgedaechtnis")
    announcement_gets_a_p_id = pid == "P1"
    unknown_kind_refused = s.announce("vibes", "x")
    empty_label_refused = s.announce("topic", "   ")
    non_string_label_refused = s.announce("topic", 42)
    refusals_leave_nothing_behind = [p.id for p in s.pending]
    long_id = s.announce("topic", "neues\n  Thema " + "x" * 200)
    label_capped_at_sixty = (s.pending[-1].label.startswith("neues Thema ")
                             and len(s.pending[-1].label) == 60)
    ids_not_reused = long_id == "P2"

    c_cut = Clock()
    s_cut = StageState(now=c_cut.now, clock=c_cut.clock)
    s_cut.announce("document", "y" * 59 + " z")
    cut_leaves_none_hanging = s_cut.pending[-1].label

    start = s.tick()
    each_announcement_queues_pending_start = [
        (e["event"], e["id"], e["kind"]) for e in start]

    content = s.content()
    content_carries_pending_list = [p["id"] for p in content["pending"]]
    each_pending_row_has_exact_shape = all(
        set(p) == {"kind", "label", "since", "id"} for p in content["pending"])
    since_carries_utc_offset = datetime.fromisoformat(
        content["pending"][0]["since"]).utcoffset()
    label_reaches_the_file = content["pending"][0]["label"]
    schema_version_did_not_move = content["version"]

    c.advance(12.34)
    settle_removes_it = s.settle("P1") is True and [p.id for p in s.pending] == ["P2"]
    unknown_id_not_settled = s.settle("P1") is False and s.settle("P99") is False
    ends = s.tick()
    pending_end_with_outcome_and_duration = ends
    s.settle("P2", "unanswered")
    named_outcome_carried = s.tick()[0]["outcome"]
    return SimpleNamespace(**locals())


def test_the_kinds_are_exactly_the_seven_of_the_contract(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.kinds_are_the_seven_of_the_contract


def test_the_timeout_is_sixty_seconds(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.timeout_is_sixty_seconds


def test_at_most_three_at_once(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.at_most_three_at_once


def test_an_announcement_gets_a_p_id(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.announcement_gets_a_p_id


def test_an_unknown_kind_is_refused(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.unknown_kind_refused == ""


def test_an_empty_label_is_refused(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.empty_label_refused == ""


def test_a_non_string_label_is_refused(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.non_string_label_refused == ""


def test_refusals_leave_nothing_behind(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.refusals_leave_nothing_behind == ["P1"]


def test_the_label_is_one_line_and_capped_at_sixty_characters(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.label_capped_at_sixty


def test_ids_are_not_reused_pending(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.ids_not_reused


def test_a_cut_that_lands_on_a_space_leaves_none_hanging(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.cut_leaves_none_hanging == "y" * 59


def test_each_accepted_announcement_queues_one_pending_start(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.each_announcement_queues_pending_start == [
        ("pending_start", "P1", "research"), ("pending_start", "P2", "topic")]


def test_content_carries_the_pending_list(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.content_carries_pending_list == ["P1", "P2"]


def test_each_pending_row_has_exactly_kind_label_since_id(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.each_pending_row_has_exact_shape


def test_since_carries_the_utc_offset_like_every_displayed_timestamp(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.since_carries_utc_offset is not None


def test_the_label_reaches_the_file_it_is_for_the_page(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.label_reaches_the_file == "Recherche zu Ortsgedaechtnis"


def test_the_schema_version_did_not_move_pending(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.schema_version_did_not_move == 2


def test_settle_removes_it(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.settle_removes_it


def test_an_unknown_id_is_not_settled(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.unknown_id_not_settled


def test_pending_end_with_outcome_arrived_and_the_duration(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.pending_end_with_outcome_and_duration == [
        {"event": "pending_end", "id": "P1", "kind": "research",
         "outcome": "arrived", "duration_s": 12.3}]


def test_a_named_outcome_is_carried(pending_announce_and_settle_scenario):
    assert pending_announce_and_settle_scenario.named_outcome_carried == "unanswered"


# ------------------------- pending: arrival settles the oldest of the
# matching kind
@pytest.fixture
def pending_arrival_main_scenario(fresh_state):
    s, c = fresh_state
    s.announce("main", "Auftrag angenommen")
    s.announce("decision", "Beschluss erkannt")
    s.announce("decision", "Beschluss erkannt")
    s.tick()
    c.advance(3)
    s.apply({"action": "main", "markdown": "# Flaeche",
             "quote": "Robo, zeig das"}, spoken="Robo, zeig das bitte.")
    main_does_not_settle_main_announcement = [p.id for p in s.pending]
    s.apply({"action": "decision", "text": "kein Pitch"})
    decision_settles_oldest_decision = [p.id for p in s.pending]
    ev = s.tick()
    settled_as_arrived = [(e["event"], e["id"], e["outcome"]) for e in ev]
    s.apply({"action": "decision", "text": 42})
    rejected_action_settles_nothing = [p.id for p in s.pending]
    s.apply({"action": "mirror", "text": "Robo hoert mit."})
    unrelated_action_settles_nothing = [p.id for p in s.pending]
    return SimpleNamespace(**locals())


def test_a_main_surface_does_not_settle_a_main_announcement_by_itself(pending_arrival_main_scenario):
    assert pending_arrival_main_scenario.main_does_not_settle_main_announcement == [
        "P1", "P2", "P3"]


def test_a_decision_settles_the_oldest_decision_announcement(pending_arrival_main_scenario):
    assert pending_arrival_main_scenario.decision_settles_oldest_decision == ["P1", "P3"]


def test_as_arrived(pending_arrival_main_scenario):
    assert pending_arrival_main_scenario.settled_as_arrived == [
        ("pending_end", "P2", "arrived")]


def test_a_rejected_action_settles_nothing(pending_arrival_main_scenario):
    assert pending_arrival_main_scenario.rejected_action_settles_nothing == ["P1", "P3"]


def test_an_unrelated_action_settles_nothing(pending_arrival_main_scenario):
    assert pending_arrival_main_scenario.unrelated_action_settles_nothing == ["P1", "P3"]


@pytest.fixture
def pending_arrival_topic_scenario(fresh_state):
    s, c = fresh_state
    s.announce("topic", "neues Thema")
    s.announce("topic", "neues Thema")
    s.apply({"action": "topic", "title": "Budget", "markdown": "# Budget"})
    topic_change_settles_topic_announcement = [p.id for p in s.pending]
    c.advance(TOPIC_WINDOW_S + 1)
    s.apply({"action": "graph", "title": "Graph", "graph": {"nodes": []}})
    graph_surface_counts_as_topic_arrival = list(s.pending)
    s.announce("topic", "neues Thema")
    s.apply({"action": "topic", "title": "Zu frueh", "markdown": "# x"})
    braked_topic_change_is_no_arrival = len(s.pending)
    return SimpleNamespace(**locals())


def test_a_topic_change_settles_a_topic_announcement(pending_arrival_topic_scenario):
    assert pending_arrival_topic_scenario.topic_change_settles_topic_announcement == ["P2"]


def test_a_graph_surface_counts_as_a_topic_arrival(pending_arrival_topic_scenario):
    assert pending_arrival_topic_scenario.graph_surface_counts_as_topic_arrival == []


def test_a_braked_topic_change_is_no_arrival(pending_arrival_topic_scenario):
    assert pending_arrival_topic_scenario.braked_topic_change_is_no_arrival == 1


@pytest.fixture
def pending_arrival_question_scenario(fresh_state):
    s, c = fresh_state
    s.apply({"action": "question", "text": "Wer macht das?"})
    qid = s.announce("question", "Frage erkannt")
    s.apply({"action": "question", "text": "Und noch was?"})
    parked_question_not_an_arrival = [p.id for p in s.pending]
    s.resolve(s.question.id, "Rosa")
    c.advance(RECEIPT_LINGER_S + 1)
    s.tick()
    s.apply({"action": "question", "text": "Und wann?"})
    accepted_question_is = s.pending
    return SimpleNamespace(**locals())


def test_a_parked_question_is_not_an_arrival(pending_arrival_question_scenario):
    assert pending_arrival_question_scenario.parked_question_not_an_arrival == [
        pending_arrival_question_scenario.qid]


def test_an_accepted_question_is(pending_arrival_question_scenario):
    assert pending_arrival_question_scenario.accepted_question_is == []


# ------------------------------ pending: the timeout discards, the cap
# displaces
@pytest.fixture
def pending_timeout_discards_scenario(fresh_state):
    s, c = fresh_state
    s.announce("research", "Recherche zu Ortsgedaechtnis")
    s.tick()
    c.advance(PENDING_TIMEOUT_S - 0.5)
    before_sixty_seconds_nothing_happens = s.tick() == [] and len(s.pending) == 1
    c.advance(0.5)
    ev = s.tick()
    discarded_at_sixty_seconds = ev == [
        {"event": "pending_end", "id": "P1", "kind": "research",
         "outcome": "discarded", "duration_s": 60.0}] and s.pending == []
    return SimpleNamespace(**locals())


def test_before_sixty_seconds_nothing_happens(pending_timeout_discards_scenario):
    assert pending_timeout_discards_scenario.before_sixty_seconds_nothing_happens


def test_at_sixty_seconds_it_is_discarded(pending_timeout_discards_scenario):
    assert pending_timeout_discards_scenario.discarded_at_sixty_seconds


@pytest.fixture
def pending_cap_displaces_scenario(fresh_state):
    s, c = fresh_state
    for i in range(MAX_PENDING):
        s.announce("document", f"Dokument {i}")
        c.advance(1)
    s.tick()
    fourth = s.announce("research", "Recherche")
    fourth_announcement_is_taken = fourth == "P4"
    state_keeps_three_oldest_left = [p.id for p in s.pending]
    ev = s.tick()
    displaced_ends_with_outcome_displaced = ev
    return SimpleNamespace(**locals())


def test_a_fourth_announcement_is_taken(pending_cap_displaces_scenario):
    assert pending_cap_displaces_scenario.fourth_announcement_is_taken


def test_the_state_keeps_at_most_three_the_oldest_left(pending_cap_displaces_scenario):
    assert pending_cap_displaces_scenario.state_keeps_three_oldest_left == [
        "P2", "P3", "P4"]


def test_the_displaced_one_ends_with_outcome_displaced(pending_cap_displaces_scenario):
    assert {"event": "pending_end", "id": "P1", "kind": "document",
           "outcome": "displaced", "duration_s": 3.0} in (
        pending_cap_displaces_scenario.displaced_ends_with_outcome_displaced)


# ------------------------------------------- pending: the trace stays
# text-free
@pytest.fixture
def pending_trace_text_free_scenario(fresh_state):
    s, c = fresh_state
    all_events = []
    s.announce("research", "Recherche zu Ortsgedaechtnis")
    all_events += s.tick()
    c.advance(61)
    all_events += s.tick()
    s.announce("main", "Auftrag angenommen")
    s.settle(s.pending[0].id, "unanswered")
    all_events += s.tick()
    no_event_carries_a_label = all("label" not in e for e in all_events)
    every_field_is_token_or_number = all(
        is_token(v) if k != "duration_s" else is_number(v)
        for e in all_events for k, v in e.items())
    no_label_text_anywhere = not any(
        "Ortsgedaechtnis" in json.dumps(e) or "Auftrag" in json.dumps(e)
        for e in all_events)
    return SimpleNamespace(**locals())


def test_no_event_carries_a_label(pending_trace_text_free_scenario):
    assert pending_trace_text_free_scenario.no_event_carries_a_label


def test_every_text_field_is_a_token_every_number_a_number(pending_trace_text_free_scenario):
    assert pending_trace_text_free_scenario.every_field_is_token_or_number


def test_no_label_text_anywhere_in_the_events(pending_trace_text_free_scenario):
    assert pending_trace_text_free_scenario.no_label_text_anywhere


# -------------------------- pending: a restart claims the ids but brings
# nothing back
@pytest.fixture
def pending_restart_scenario(fresh_state):
    s, c = fresh_state
    s.announce("main", "Auftrag angenommen")
    s.announce("main", "Auftrag angenommen")
    saved = s.content()
    c2 = Clock()
    s2 = StageState(now=c2.now, clock=c2.clock)
    s2.restore(saved)
    stale_announcement_not_restored = list(s2.pending)
    id_never_handed_out_again = s2.announce("topic", "neues Thema")
    return SimpleNamespace(**locals())


def test_a_stale_announcement_is_not_restored(pending_restart_scenario):
    assert pending_restart_scenario.stale_announcement_not_restored == []


def test_its_id_is_never_handed_out_again(pending_restart_scenario):
    assert pending_restart_scenario.id_never_handed_out_again == "P3"


# ------------------------------------------- brake_check(): the brake,
# read-only (MM-B)
@pytest.fixture
def brake_check_scenario():
    t = [1000.0]
    s = StageState(now=lambda: t[0])
    free_with_nothing_up = s.brake_check()
    still_free_check_consumed_nothing = s.brake_check()
    s.apply({"action": "topic", "title": "A", "markdown": "- a"})
    too_soon_right_after_topic = s.brake_check()
    t[0] += TOPIC_WINDOW_S + 1
    free_after_hold_window_passed = s.brake_check()
    check_never_fills_the_window = s.apply(
        {"action": "topic", "title": "B", "markdown": "- b"})
    announce_accepts_kind_entry = s.announce("entry", "Eintrag")
    return SimpleNamespace(**locals())


def test_free_with_nothing_up(brake_check_scenario):
    assert brake_check_scenario.free_with_nothing_up == ""


def test_still_free_the_check_consumed_nothing(brake_check_scenario):
    assert brake_check_scenario.still_free_check_consumed_nothing == ""


def test_too_soon_right_after_a_topic(brake_check_scenario):
    assert brake_check_scenario.too_soon_right_after_topic == TOO_SOON


def test_free_after_the_hold_window_passed(brake_check_scenario):
    assert brake_check_scenario.free_after_hold_window_passed == ""


def test_a_check_never_fills_the_window(brake_check_scenario):
    assert brake_check_scenario.check_never_fills_the_window == ""


def test_announce_accepts_kind_entry(brake_check_scenario):
    assert brake_check_scenario.announce_accepts_kind_entry != ""


# ----------------------- append_to_history()/history_title(): lane
# late-result filing
@pytest.fixture
def append_to_history_scenario():
    t2 = [1000.0]
    s3 = StageState(now=lambda: t2[0])
    push_first_topic = s3.apply(
        {"action": "topic", "title": "Thema A", "markdown": "- a"})
    old_id = s3.main.id
    t2[0] += TOPIC_WINDOW_S + 1
    push_second_topic = s3.apply(
        {"action": "topic", "title": "Thema B", "markdown": "- b"})
    current_id = s3.main.id
    different_ids = old_id != current_id
    appending_to_old_entry_succeeds = s3.append_to_history(
        old_id, "**Nachtrag 12:00:** spaet dran")
    old_entry_carries_addition = "Nachtrag" in s3.history[0].markdown
    current_entry_untouched = "Nachtrag" not in s3.main.markdown
    history_title_reads_old_title = s3.history_title(old_id)
    appending_to_missing_id_fails_harmlessly = s3.append_to_history("M999", "x")
    history_title_of_missing_id_empty = s3.history_title("M999")
    return SimpleNamespace(**locals())


def test_push_first_topic(append_to_history_scenario):
    assert append_to_history_scenario.push_first_topic == ""


def test_push_second_topic(append_to_history_scenario):
    assert append_to_history_scenario.push_second_topic == ""


def test_the_two_entries_have_different_ids(append_to_history_scenario):
    assert append_to_history_scenario.different_ids


def test_appending_to_the_old_entry_succeeds(append_to_history_scenario):
    assert append_to_history_scenario.appending_to_old_entry_succeeds


def test_the_old_entrys_markdown_now_carries_the_addition(append_to_history_scenario):
    assert append_to_history_scenario.old_entry_carries_addition


def test_the_current_entry_is_untouched_by_a_late_result_for_the_old_one(append_to_history_scenario):
    assert append_to_history_scenario.current_entry_untouched


def test_history_title_reads_the_old_entrys_own_title(append_to_history_scenario):
    assert append_to_history_scenario.history_title_reads_old_title == "Thema A"


def test_appending_to_an_id_that_no_longer_exists_fails_harmlessly(append_to_history_scenario):
    assert append_to_history_scenario.appending_to_missing_id_fails_harmlessly is False


def test_history_title_of_a_missing_id_is_empty(append_to_history_scenario):
    assert append_to_history_scenario.history_title_of_missing_id_empty == ""


# -------------------- confirmed_topic(): a hysteresis-confirmed change
# skips the N5 brakes, an agent proposal right after it is still braked
@pytest.fixture
def confirmed_topic_scenario():
    clk = [1000.0]
    s_ct = StageState(now=lambda: clk[0])
    agent_topic_accepted_on_empty_stage = s_ct.apply(
        {"action": "topic", "title": "A", "markdown": "a"})
    clk[0] += 5
    agent_topic_5s_later_is_braked = s_ct.apply(
        {"action": "topic", "title": "B", "markdown": "b"})
    confirmed_topic_5s_later_pushed_anyway = (
        s_ct.confirmed_topic("C", "c") == "" and s_ct.main.title == "C"
        and len(s_ct.history) == 2)
    clk[0] += 5
    agent_proposal_right_after_stays_braked = s_ct.apply(
        {"action": "topic", "title": "D", "markdown": "d"})
    return SimpleNamespace(**locals())


def test_agent_topic_accepted_on_an_empty_stage(confirmed_topic_scenario):
    assert confirmed_topic_scenario.agent_topic_accepted_on_empty_stage == ""


def test_agent_topic_5_s_later_is_braked(confirmed_topic_scenario):
    assert confirmed_topic_scenario.agent_topic_5s_later_is_braked == TOO_SOON


def test_a_confirmed_topic_5_s_later_is_pushed_anyway(confirmed_topic_scenario):
    assert confirmed_topic_scenario.confirmed_topic_5s_later_pushed_anyway


def test_and_an_agent_proposal_right_after_it_stays_braked(confirmed_topic_scenario):
    assert confirmed_topic_scenario.agent_proposal_right_after_stays_braked != ""


# --------------------------------------------------------------- finding
# gateway/stage_state.py in this repo predates the upstream change that
# gave StageState.confirmed_topic() its `set_headline` keyword; this
# characterization port keeps the four checks as explicit skips rather
# than silently dropping them or editing the product module.
_SET_HEADLINE_REASON = (
    "StageState.confirmed_topic() in this repo has no set_headline "
    "parameter yet; see PR description")


@pytest.mark.skip(reason=_SET_HEADLINE_REASON)
def test_confirmed_topic_accepted_with_set_headline():
    st = StageState(now=lambda: 5000.0)
    st.headline = "alt"
    assert st.confirmed_topic("Budget senken", "- a", set_headline=True) == ""


@pytest.mark.skip(reason=_SET_HEADLINE_REASON)
def test_set_headline_true_headline_follows_the_confirmed_topic():
    st = StageState(now=lambda: 5000.0)
    st.headline = "alt"
    st.confirmed_topic("Budget senken", "- a", set_headline=True)
    assert st.headline == "Budget senken"


@pytest.mark.skip(reason=_SET_HEADLINE_REASON)
def test_main_shows_the_confirmed_topic_with_set_headline():
    st = StageState(now=lambda: 5000.0)
    st.headline = "alt"
    st.confirmed_topic("Budget senken", "- a", set_headline=True)
    assert st.main.title == "Budget senken"


@pytest.mark.skip(reason=_SET_HEADLINE_REASON)
def test_default_titler_owns_the_headline_left_alone():
    st = StageState(now=lambda: 5000.0)
    st.headline = "alt"
    st.confirmed_topic("Budget senken", "- a", set_headline=True)
    st.confirmed_topic("Zweites Thema", "- b")
    assert st.headline == "Budget senken"
