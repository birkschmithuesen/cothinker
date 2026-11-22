# CoThinker — tests for gateway/stage_texts.py: the German display texts
# of the operating line (issue #41, Wave B.4)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_stage_texts.py

Ported from the archive `tests/test_status_line.py` (commit 3b4a843b),
everything about the rendered German text: `status()`, `warning()`,
`trouble()`, `STATES`, `WARNINGS`, `DURATION_MARK`. The precedence section
(`pick()`) moved to `blocks/status_line/line.py` instead and is ported in
`tests/test_block_status_line.py`.
"""
from __future__ import annotations

import pytest

from gateway.stage_texts import DURATION_MARK, STATES, WARNINGS, status, trouble, warning


def test_listening_is_german_and_verbatim():
    assert status("listening", at="19:11:52")["text"] == "🎧 hört zu · zuletzt 19:11:52"


def test_working_is_german_and_verbatim():
    assert status("working")["text"] == "⚙️ Robo werkelt seit m:ss — nicht abgestürzt, nur gründlich"


def test_the_placeholder_sits_between_seit_and_the_dash():
    text = status("working")["text"]
    assert text.split(" — ")[0].endswith("seit " + DURATION_MARK)


def test_the_running_time_is_not_in_the_text():
    assert not any(ch.isdigit() for ch in status("working")["text"])


def test_working_is_the_ticking_one():
    assert status("working")["ticking"] is True


def test_listening_does_not_tick():
    assert status("listening")["ticking"] is False


def test_backlog_names_the_number():
    assert "3 Abschnitte im Rückstau" in status("backlog", count=3)["text"]


def test_backlog_explains_itself():
    assert "lieber grob und aktuell" in status("backlog", count=3)["text"]


def test_failed_reassures():
    assert status("failed")["text"] == ("💤 letzter Lauf verschluckt — Abschnitt kommt "
                                         "beim nächsten Mal wieder mit")


def test_stable_across_calls():
    assert status("failed")["text"] == status("failed")["text"]


def test_stable_for_backlog_with_the_same_count():
    assert status("backlog", count=7)["text"] == status("backlog", count=7)["text"]


# -- warnings ----------------------------------------------------------------

def test_conflict_files():
    assert WARNINGS["conflicts"] == "⚠️ Konfliktdateien im Projektordner — ein Sync-Dienst schreibt mit"


def test_restored_raw_lines():
    assert "Rohzeilen nachgetragen" in WARNINGS["restored"]


def test_discarded_proposal_line():
    assert "unlesbar" in WARNINGS["discarded"]


def test_a_dropped_speech_to_text_stream():
    assert "abgerissen" in WARNINGS["stream"]


def test_an_unwritable_protocol():
    assert "Protokoll" in WARNINGS["protocol"] and "verloren" in WARNINGS["protocol"]


def test_a_stage_that_could_not_be_restored():
    assert "Bühne" in WARNINGS["stage"]


# -- the trouble line: a warning never costs the acknowledgement -------------

def test_the_operating_line_is_untouched_by_a_warning():
    assert status("listening", at="19:11:52")["text"] == "🎧 hört zu · zuletzt 19:11:52"


def test_the_warning_is_its_own_line():
    assert trouble(warning("conflicts")) == WARNINGS["conflicts"]


def test_nothing_wrong_no_line():
    assert trouble() == ""


def test_a_failed_run_goes_on_the_trouble_line_not_the_operating_line():
    assert trouble(failed=True) == STATES["failed"]


def test_a_warning_outranks_the_failed_run():
    assert trouble(warning("conflicts"), failed=True) == WARNINGS["conflicts"]


def test_the_working_line_keeps_ticking_while_a_warning_shows():
    assert status("working")["ticking"] is True


# -- warning() formats placeholders, WARNINGS entries must not be used raw --

def test_restored_substitutes_count():
    assert warning("restored", count=3) == ("🧵 3 Rohzeilen nachgetragen — jemand anders hat "
                                             "ins Protokoll geschrieben")


def test_restored_leaves_no_unresolved_placeholder():
    rendered = warning("restored", count=3)
    assert "{" not in rendered and "}" not in rendered


def test_conflicts_has_no_placeholder_returned_unchanged():
    assert warning("conflicts") == WARNINGS["conflicts"]


def test_an_unknown_warning_key_degrades_to_empty():
    assert warning("nonsense") == ""


def test_a_missing_field_degrades_instead_of_raising():
    rendered = warning("restored")
    assert "{" not in rendered and "}" not in rendered


def test_warning_piped_through_trouble_leaves_no_unresolved_placeholder():
    warned = trouble(warning("restored", count=3))
    assert "{" not in warned and "}" not in warned


def test_an_unknown_key_leaves_the_trouble_line_empty():
    assert trouble(warning("nonsense")) == ""


# -- the state key of the returned dict --------------------------------------

def test_the_normal_case_names_its_own_state():
    assert status("backlog", count=3)["state"] == "backlog"


def test_an_unrecognised_state_falls_back_to_listening():
    assert status("nonsense")["state"] == "listening"


# -- an unhashable state does not crash --------------------------------------

def test_a_list_state_falls_back_to_listening_instead_of_raising():
    assert status(["nonsense"])["state"] == "listening"


def test_a_list_state_still_renders_the_listening_text():
    assert status(["nonsense"])["text"] == status("listening")["text"]


# -- every state is German, carries an emoji, leaves no placeholder ---------

@pytest.mark.parametrize("name", list(STATES))
def test_every_state_carries_an_emoji(name):
    assert any(ord(ch) > 0x2000 for ch in STATES[name])


@pytest.mark.parametrize("name", list(STATES))
def test_every_state_leaves_no_unresolved_placeholder(name):
    rendered = status(name, at="19:11:52", count=3)["text"]
    assert "{" not in rendered and "}" not in rendered


def test_one_formulation_per_state_no_duplicates():
    assert len(set(STATES.values())) == len(STATES)
