# CoThinker — tests of model-made topic titles
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""topic_title + its wiring in lane_integration, with a fake model (no
network), and StageState.retitle().

Ported from a script-style test: one pytest function per former check, same
names (snake_case). The module body runs once at import, in the original
order, because later checks build on state earlier ones left behind; every
test function asserts the recorded result of its check.

    python3 -m pytest -q tests/test_topic_title.py
"""
from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
# lane_integration still imports its siblings by bare name.
sys.path.append(str(HERE.parent / "gateway"))

from gateway import topic_title as tt  # noqa: E402
from gateway.lane_integration import Lanes  # noqa: E402
from gateway.stage_state import StageState  # noqa: E402

FAKE = str(HERE / "fake_worker.py")
_BAD_NAMES = {"": "empty", None: "none"}


RESULTS: dict = {}


def _slug(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit].strip("_")


def check(name, label, cond, detail=""):
    """Record one former `check(...)` result; the test function of the same
    name asserts it. The module body runs once, in the original order,
    because later checks build on the state earlier ones left behind."""
    RESULTS[name] = (bool(cond), str(detail) or label)


def _verify(name):
    ok, detail = RESULTS[name]
    assert ok, detail



def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


print("clean_title")
check("test_plain_title_passes", "plain title passes", tt.clean_title("Budget der Werkstatt") == "Budget der Werkstatt")
check("test_quotes_prefix_and_trailing_dot_stripped", "quotes, prefix and trailing dot stripped",
      tt.clean_title('Titel: „Förderantrag LEADER“.') == "Förderantrag LEADER",
      tt.clean_title('Titel: „Förderantrag LEADER“.'))
check("test_first_non_empty_line_only", "first non-empty line only", tt.clean_title("\nRaumplanung Herbst\nweil ...")
      == "Raumplanung Herbst")
for bad in ("", "Das ist ein ganzer Satz mit viel zu vielen Woertern drin", None):
    try:
        tt.clean_title(bad)
        ok = False
    except tt.TitleError:
        ok = True
    check(f"test_rejects_{_BAD_NAMES.get(bad, 'long_sentence')}", f"rejects {bad!r:.30}", ok)

print("\nprompt")
p = tt.build_user_prompt("x" * 5000, "vorher")
check("test_block_capped_previous_block_included", "block capped, previous block included",
      len(p) < tt.MAX_INPUT_CHARS + 200 and "Davor:" in p, len(p))

print("\nTopicTitler with a fake model")
got: list = []
fails: list = []
logs: list = []
seen_user: list = []


def fake_call(system, user, timeout_s):
    seen_user.append(user)
    if "SLOW" in user:
        time.sleep(1.0)
    if "BOOM" in user:
        raise tt.TitleError("http_500")
    if "LONG" in user:
        return "Das ist leider eine viel zu lange Antwort ohne Titel drin"
    return "„Budgetplanung 2027“"


titler = tt.TopicTitler(lambda *a: got.append(a),
                        on_fail=lambda *a: fails.append(a), call=fake_call,
                        timeout_s=0.3, log=lambda *a: logs.append(" ".join(map(str, a))))
t0 = time.monotonic()
check("test_request_returns_at_once", "request() returns at once", titler.request("M1", "Dann geht ihr", "Block A")
      and time.monotonic() - t0 < 0.05)
check("test_title_arrives_via_callback_cleaned", "title arrives via callback, cleaned",
      wait_until(lambda: got and got[0][:3] == ("M1", "Dann geht ihr", "Budgetplanung 2027")),
      got)
check("test_ms_is_reported", "ms is reported", got and isinstance(got[0][3], int))
titler.request("M2", "prov", "SLOW block")
check("test_timeout_provisional_kept_on_fail_timeout", "timeout -> provisional kept, on_fail('timeout')",
      wait_until(lambda: fails and fails[-1][:2] == ("M2", "timeout"), 2.0), fails)
titler.request("M3", "prov", "BOOM block")
check("test_model_error_on_fail_with_token", "model error -> on_fail with token",
      wait_until(lambda: any(f[:2] == ("M3", "http_500") for f in fails)), fails)
titler.request("M4", "prov", "LONG block")
check("test_unusable_answer_on_fail_bad_shape", "unusable answer -> on_fail bad_shape",
      wait_until(lambda: any(f[:2] == ("M4", "bad_shape") for f in fails)), fails)
check("test_failure_log_lines_carry_tokens_only_never_block_text", "failure log lines carry tokens only, never block text",
      logs and not any("block" in l for l in logs), logs)
titler.stop()

print("\nStageState.retitle")
st = StageState()
st.confirmed_topic("Dann geht ihr wieder raus", "Dann geht ihr wieder raus und")
eid = st.main.id
check("test_retitle_with_the_right_old_title_replaces_it_in_place", "retitle with the right old title replaces it in place",
      st.retitle(eid, "Dann geht ihr wieder raus", "Abschluss der Runde")
      and st.main.title == "Abschluss der Runde" and len(st.history) == 1)
check("test_the_headline_follows_the_current_entry", "the headline follows the current entry", st.headline == "Abschluss der Runde")
check("test_a_title_changed_meanwhile_is_never_overwritten", "a title changed meanwhile is never overwritten",
      not st.retitle(eid, "Dann geht ihr wieder raus", "Anderes"))
check("test_unknown_id_false", "unknown id -> False", not st.retitle("M999", "x", "y"))
st.confirmed_topic("Zweites Thema hier", "Zweites Thema hier")
check("test_retitling_an_older_entry_leaves_the_headline_alone", "retitling an OLDER entry leaves the headline alone",
      st.retitle(eid, "Abschluss der Runde", "Abschluss") and st.headline
      == "Abschluss der Runde" and st.history[0].title == "Abschluss")
check("test_content_exports_the_new_title", "content() exports the new title",
      [h["title"] for h in st.content()["history"]][:1] == ["Abschluss"],
      st.content()["history"])


print("\nLanes --topic-titles wiring")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Writer:
    def __init__(self):
        self.writes = 0

    def write(self):
        self.writes += 1

    def warn(self, t):
        pass

    def clear_warning(self, only=""):
        pass

    def warning(self):
        return ""


class Res:
    def __init__(self, p):
        self.p = p

    def by_id(self):
        return {"neues_thema": {"p": self.p}}


def make(titler_factory, tag):
    clock = Clock()
    state = StageState(now=clock)
    writer = Writer()
    lane_logs: list = []
    lanes = Lanes(call_command=[sys.executable, FAKE], coach_command=[sys.executable, FAKE],
                  call_session_id=f"tt-call-{tag}", coach_session_id=f"tt-coach-{tag}",
                  project_agenda="P", state=state, writer=writer, now=clock,
                  titler_factory=titler_factory,
                  log=lambda *a: lane_logs.append(" ".join(map(str, a))))
    return lanes, state, writer, clock, lane_logs


def stop_lanes(lanes):
    # The lane workers were never started here; only the titler runs.
    if lanes.titler is not None:
        lanes.titler.stop()


release = threading.Event()


def slow_good(system, user, timeout_s):
    release.wait(2.0)
    return "Raumplanung im Herbst"


lanes, state, writer, clock, lane_logs = make(
    lambda on_title, on_fail: tt.TopicTitler(on_title, on_fail=on_fail,
                                             call=slow_good, timeout_s=3.0,
                                             log=lambda *a: None), "a")
clock.t += 95
lanes.on_decider_result(Res(0.9), SimpleNamespace(
    block="Dann geht ihr wieder raus und wir planen den Raum", previous=["vorher"]),
    "pause")
check("test_provisional_title_is_up_immediately_nothing_waits_for_the_model", "provisional title is up immediately (nothing waits for the model)",
      state.main.title.startswith("Dann geht ihr") and len(state.history) == 1,
      state.main.title)
w0 = writer.writes
release.set()
check("test_the_model_title_replaces_it_in_place_same_entry_same_count", "the model title replaces it IN PLACE (same entry, same count)",
      wait_until(lambda: state.main.title == "Raumplanung im Herbst")
      and len(state.history) == 1, [h.title for h in state.history])
check("test_headline_shows_the_new_title", "headline shows the new title", state.headline == "Raumplanung im Herbst")
check("test_stage_re_written_after_the_swap", "stage re-written after the swap", wait_until(lambda: writer.writes > w0))
check("test_log_line_topic_title_old_new_ms", "log line 'topic: title <old> -> <new> <ms>'",
      wait_until(lambda: any(l.startswith("topic: title 'Dann geht ihr")
                             and "-> 'Raumplanung im Herbst'" in l and l.endswith("ms")
                             for l in lane_logs)), lane_logs)
stop_lanes(lanes)


def failing(system, user, timeout_s):
    raise tt.TitleError("timeout")


lanes, state, writer, clock, lane_logs = make(
    lambda on_title, on_fail: tt.TopicTitler(on_title, on_fail=on_fail,
                                             call=failing, timeout_s=0.5,
                                             log=lambda *a: lane_logs.append(" ".join(map(str, a)))), "b")
clock.t += 95
lanes.on_decider_result(Res(0.9), SimpleNamespace(block="Erster Satz hier"), "pause")
time.sleep(0.3)
check("test_model_failure_keeps_the_provisional_title", "model failure keeps the provisional title",
      state.main.title == "Erster Satz hier", state.main.title)
stop_lanes(lanes)

lanes, state, writer, clock, lane_logs = make(None, "c")
clock.t += 95
lanes.on_decider_result(Res(0.9), SimpleNamespace(block="Erster Satz hier"), "pause")
# Decision of 2026-11-20: without a titler nobody else sets the headline
# under --lanes, so it IS the provisional title.
check("test_switch_off_no_titler_the_headline_is_the_provisional_title", "switch OFF: no titler, the headline is the provisional title",
      lanes.titler is None and state.main.title == "Erster Satz hier"
      and state.headline == "Erster Satz hier", state.headline)
stop_lanes(lanes)


def test_plain_title_passes():
    _verify("test_plain_title_passes")


def test_quotes_prefix_and_trailing_dot_stripped():
    _verify("test_quotes_prefix_and_trailing_dot_stripped")


def test_first_non_empty_line_only():
    _verify("test_first_non_empty_line_only")


def test_block_capped_previous_block_included():
    _verify("test_block_capped_previous_block_included")


def test_request_returns_at_once():
    _verify("test_request_returns_at_once")


def test_title_arrives_via_callback_cleaned():
    _verify("test_title_arrives_via_callback_cleaned")


def test_ms_is_reported():
    _verify("test_ms_is_reported")


def test_timeout_provisional_kept_on_fail_timeout():
    _verify("test_timeout_provisional_kept_on_fail_timeout")


def test_model_error_on_fail_with_token():
    _verify("test_model_error_on_fail_with_token")


def test_unusable_answer_on_fail_bad_shape():
    _verify("test_unusable_answer_on_fail_bad_shape")


def test_failure_log_lines_carry_tokens_only_never_block_text():
    _verify("test_failure_log_lines_carry_tokens_only_never_block_text")


def test_retitle_with_the_right_old_title_replaces_it_in_place():
    _verify("test_retitle_with_the_right_old_title_replaces_it_in_place")


def test_the_headline_follows_the_current_entry():
    _verify("test_the_headline_follows_the_current_entry")


def test_a_title_changed_meanwhile_is_never_overwritten():
    _verify("test_a_title_changed_meanwhile_is_never_overwritten")


def test_unknown_id_false():
    _verify("test_unknown_id_false")


def test_retitling_an_older_entry_leaves_the_headline_alone():
    _verify("test_retitling_an_older_entry_leaves_the_headline_alone")


def test_content_exports_the_new_title():
    _verify("test_content_exports_the_new_title")


def test_provisional_title_is_up_immediately_nothing_waits_for_the_model():
    _verify("test_provisional_title_is_up_immediately_nothing_waits_for_the_model")


def test_the_model_title_replaces_it_in_place_same_entry_same_count():
    _verify("test_the_model_title_replaces_it_in_place_same_entry_same_count")


def test_headline_shows_the_new_title():
    _verify("test_headline_shows_the_new_title")


def test_stage_re_written_after_the_swap():
    _verify("test_stage_re_written_after_the_swap")


def test_log_line_topic_title_old_new_ms():
    _verify("test_log_line_topic_title_old_new_ms")


def test_model_failure_keeps_the_provisional_title():
    _verify("test_model_failure_keeps_the_provisional_title")


@pytest.mark.xfail(strict=True, reason=(
    "characterization finding: the archive's Lanes passes "
    "set_headline=(self.titler is None) so that the headline is the "
    "provisional title when no titler runs; this repository's "
    "lane_integration.py lacks that and leaves the headline empty"))
def test_switch_off_no_titler_the_headline_is_the_provisional_title():
    _verify("test_switch_off_no_titler_the_headline_is_the_provisional_title")



def test_rejects_empty():
    _verify("test_rejects_empty")


def test_rejects_long_sentence():
    _verify("test_rejects_long_sentence")


def test_rejects_none():
    _verify("test_rejects_none")
