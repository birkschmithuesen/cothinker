# CoThinker — characterization tests for the per-lane context window
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Per-lane context window: stable -> volatile, rolls in blocks.

    python3 -m pytest -q tests/test_lane_context.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))
from lane_context import LaneContext  # noqa: E402


_RESULTS: dict = {}


def _case_ids(names):
    return [pytest.param(i, id=n) for i, n in enumerate(names)]


def _expect(section, i, expected):
    """Run `section` once (it records its checks), then assert check `i`.

    Every check site of the section is its own pytest case; they share one
    run of the section because the scenarios are sequential and real
    (worker subprocesses, a shared fake clock)."""
    if section not in _RESULTS:
        rec: list = []

        def check(name, cond, info=""):
            rec.append((name, bool(cond), str(info)))

        err = ""
        try:
            section(check)
        except Exception:
            err = traceback.format_exc(limit=4)
        _RESULTS[section] = (rec, err)
    rec, err = _RESULTS[section]
    if i >= len(rec):
        pytest.fail(f"section stopped after {len(rec)} checks: {err}")
    name, ok, info = rec[i]
    assert ok, f"{name}: {info}"
    if i == expected - 1:
        assert len(rec) == expected, f"section recorded {len(rec)} checks"
        assert not err, err


def _run_window_grows_by_appending_no_roll_before_window_size(check) -> None:
    """window grows by appending, no roll before window_size + roll_every"""
    ctx = LaneContext(project_agenda="PROJECT", window_size=5, roll_every=3)
    rolled_flags = [ctx.add_utterance("A", f"u{i}") for i in range(7)]
    check("no roll before enough overflow", not any(rolled_flags[:6]),
          str(rolled_flags))
    check("window still holds every utterance so far (no roll yet)",
          len(ctx.window()) == 7, str(len(ctx.window())))
    check("stand is still empty", ctx.stand == "")


def _run_roll_fires_once_overflow_reaches_roll_every_folding_the(check) -> None:
    """roll fires once overflow reaches roll_every, folding the oldest"""
    ctx = LaneContext(project_agenda="PROJECT", window_size=5, roll_every=3)
    rolled = [ctx.add_utterance("A", f"u{i}") for i in range(8)]
    # after 8 utterances: overflow_end = 8-5=3, folded_upto starts at 0,
    # diff 3 >= roll_every(3) -> rolls exactly at the 8th utterance
    check("rolled exactly once, at the 8th utterance",
          rolled == [False] * 7 + [True], str(rolled))
    check("stand now mentions the folded utterances", "u0" in ctx.stand
          and "u2" in ctx.stand and "u3" not in ctx.stand, ctx.stand)
    check("window holds the rest, u3..u7", [u.text for u in ctx.window()]
          == [f"u{i}" for i in range(3, 8)], str([u.text for u in ctx.window()]))


def _run_the_prompt_is_stable_to_volatile_and_append_only(check) -> None:
    """the prompt is stable-to-volatile and append-only between rolls"""
    ctx = LaneContext(project_agenda="PROJECT\nAGENDA", window_size=5,
                      roll_every=3)
    for i in range(4):
        ctx.add_utterance("the operator", f"Satz {i}")
    p1 = ctx.build_prompt("Robo, fass zusammen")
    check("prompt starts with the fixed project/agenda text",
          p1.startswith("PROJECT\nAGENDA"))
    check("no Stand section before the first roll", "## Stand" not in p1)
    ctx.add_utterance("the operator", "Satz 4")
    p2 = ctx.build_prompt("Robo, strukturiere")
    prefix1 = p1.rsplit("\n\n## Auftrag", 1)[0]
    prefix2 = p2.rsplit("\n\n## Auftrag", 1)[0]
    check("growing the window only appends -- the old prefix is unchanged",
          prefix2.startswith(prefix1),
          "prefix changed between turns, cache would miss")


def _run_a_custom_summarizer_is_used_instead_of_the_fallback(check) -> None:
    """a custom summarizer is used instead of the fallback placeholder"""
    seen = []


    def summarizer(old_stand, folded):
        seen.append(len(folded))
        return f"STAND:{len(folded)}"


    ctx = LaneContext(project_agenda="P", window_size=2, roll_every=2,
                      summarize=summarizer)
    for i in range(4):
        ctx.add_utterance("A", f"u{i}")
    check("custom summarizer got called with the folded batch", seen == [2],
          str(seen))
    check("stand reflects the custom summarizer's output", ctx.stand == "STAND:2")


def _run_cache_read_ratio(check) -> None:
    """cache_read_ratio() reports the unchanged-prefix / fraction (point: 'report cache read ratio per lane') -- a simplified, literal common-prefix measurement over the actually-built prompt text, since the real per-request cache-hit metric is not exposed by the worker protocol (gateway/worker.py speaks only turn/result/log/ready)"""
    AGENDA = ("PROJEKT: Herbstplanung\nTeilnehmende: the operator, the designer, Alex\n"
             "Agenda: Budget, Zeitplan, Verantwortlichkeiten")
    ctx = LaneContext(project_agenda=AGENDA, window_size=10, roll_every=5)
    check("before any prompt was built, the ratio is 0.0 (nothing to "
         "compare against yet)", ctx.last_cache_read_ratio == 0.0)
    for i in range(3):
        ctx.add_utterance("the operator", f"Das ist Satz Nummer {i} im Gespraech.")
    p1 = ctx.build_prompt("erste Anfrage an die Spur")
    check("the very first build still reports 0.0 -- no previous prompt "
         "existed to share a prefix with",
          ctx.last_cache_read_ratio == 0.0, ctx.last_cache_read_ratio)
    ctx.add_utterance("the operator", "Das ist Satz Nummer 3 im Gespraech.")
    p2 = ctx.build_prompt("zweite Anfrage an die Spur")
    check("an append-only turn (no roll) reuses most of the previous "
         "prompt as a literal prefix -- a high ratio",
          ctx.last_cache_read_ratio > 0.5, ctx.last_cache_read_ratio)
    no_roll_ratio = ctx.last_cache_read_ratio
    for i in range(4, 20):
        rolled = ctx.add_utterance("the operator", f"Das ist Satz Nummer {i} im Gespraech.")
        if rolled:
            break
    check("a roll actually happened in this run", rolled, "never rolled")
    ctx.build_prompt("Anfrage nach dem Roll")
    check("the turn right after a roll breaks the prefix (the Stand text "
         "itself just changed) -- a markedly lower ratio than a plain "
         "append-only turn",
          ctx.last_cache_read_ratio < no_roll_ratio, ctx.last_cache_read_ratio)


_NAMES_window_grows_by_appending_no_roll_before_window_size = ['no roll before enough overflow',
 'window still holds every utterance so far (no roll yet)',
 'stand is still empty']


@pytest.mark.parametrize("i", _case_ids(_NAMES_window_grows_by_appending_no_roll_before_window_size))
def test_window_grows_by_appending_no_roll_before_window_size(i):
    _expect(_run_window_grows_by_appending_no_roll_before_window_size, i, len(_NAMES_window_grows_by_appending_no_roll_before_window_size))


_NAMES_roll_fires_once_overflow_reaches_roll_every_folding_the = ['rolled exactly once, at the 8th utterance',
 'stand now mentions the folded utterances',
 'window holds the rest, u3..u7']


@pytest.mark.parametrize("i", _case_ids(_NAMES_roll_fires_once_overflow_reaches_roll_every_folding_the))
def test_roll_fires_once_overflow_reaches_roll_every_folding_the(i):
    _expect(_run_roll_fires_once_overflow_reaches_roll_every_folding_the, i, len(_NAMES_roll_fires_once_overflow_reaches_roll_every_folding_the))


_NAMES_the_prompt_is_stable_to_volatile_and_append_only = ['prompt starts with the fixed project/agenda text',
 'no Stand section before the first roll',
 'growing the window only appends -- the old prefix is unchanged']


@pytest.mark.parametrize("i", _case_ids(_NAMES_the_prompt_is_stable_to_volatile_and_append_only))
def test_the_prompt_is_stable_to_volatile_and_append_only(i):
    _expect(_run_the_prompt_is_stable_to_volatile_and_append_only, i, len(_NAMES_the_prompt_is_stable_to_volatile_and_append_only))


_NAMES_a_custom_summarizer_is_used_instead_of_the_fallback = ['custom summarizer got called with the folded batch',
 "stand reflects the custom summarizer's output"]


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_custom_summarizer_is_used_instead_of_the_fallback))
def test_a_custom_summarizer_is_used_instead_of_the_fallback(i):
    _expect(_run_a_custom_summarizer_is_used_instead_of_the_fallback, i, len(_NAMES_a_custom_summarizer_is_used_instead_of_the_fallback))


_NAMES_cache_read_ratio = ['before any prompt was built, the ratio is 0.0 (nothing to compare '
 'against yet)',
 'the very first build still reports 0.0 -- no previous prompt existed to '
 'share a prefix with',
 'an append-only turn (no roll) reuses most of the previous prompt as a '
 'literal prefix -- a high ratio',
 'a roll actually happened in this run',
 'the turn right after a roll breaks the prefix (the Stand text itself '
 'just changed) -- a markedly lower ratio than a plain append-only turn']


@pytest.mark.parametrize("i", _case_ids(_NAMES_cache_read_ratio))
def test_cache_read_ratio(i):
    _expect(_run_cache_read_ratio, i, len(_NAMES_cache_read_ratio))


SECTIONS = [_run_window_grows_by_appending_no_roll_before_window_size, _run_roll_fires_once_overflow_reaches_roll_every_folding_the, _run_the_prompt_is_stable_to_volatile_and_append_only, _run_a_custom_summarizer_is_used_instead_of_the_fallback, _run_cache_read_ratio]
