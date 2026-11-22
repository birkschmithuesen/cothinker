# CoThinker — shared/phases.py: phase engine with history
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_phases.py"""
from __future__ import annotations

import pytest

from shared import phases as sp


def _phases():
    return [sp.PhaseSpec(id=i, titles={"de": i}, blocks=("x",), surfaces=("stage",))
            for i in ("a", "b", "c")]


def test_starts_in_the_first_phase(tmp_path):
    e = sp.PhaseEngine(_phases(), tmp_path / "phase-history.jsonl", clock=lambda: 10.0)
    assert e.current().id == "a"


def test_advance_records_a_change(tmp_path):
    e = sp.PhaseEngine(_phases(), tmp_path / "phase-history.jsonl", clock=lambda: 10.0)
    assert e.advance("c", "operator") is True
    assert e.current().id == "c"


def test_advance_to_the_current_phase_is_a_noop(tmp_path):
    e = sp.PhaseEngine(_phases(), tmp_path / "phase-history.jsonl", clock=lambda: 10.0)
    e.advance("c", "operator")
    assert e.advance("c", "operator") is False


def test_back_reverses_the_last_change(tmp_path):
    t = [10.0]
    e = sp.PhaseEngine(_phases(), tmp_path / "phase-history.jsonl", clock=lambda: t[0])
    e.advance("c", "operator")
    t[0] = 20.0
    assert e.back() is True
    assert e.current().id == "a"


def test_back_is_itself_history(tmp_path):
    t = [10.0]
    e = sp.PhaseEngine(_phases(), tmp_path / "phase-history.jsonl", clock=lambda: t[0])
    e.advance("c", "operator")
    t[0] = 20.0
    e.back()
    hist = [(c.from_id, c.to_id, c.cause) for c in e.history()]
    assert hist == [("a", "c", "operator"), ("c", "a", "back")], hist


def test_a_restart_derives_the_current_phase_from_the_history_file(tmp_path):
    h = tmp_path / "phase-history.jsonl"
    t = [10.0]
    e = sp.PhaseEngine(_phases(), h, clock=lambda: t[0])
    e.advance("c", "operator")
    t[0] = 20.0
    e.back()
    e2 = sp.PhaseEngine(_phases(), h, clock=lambda: t[0])
    assert e2.current().id == "a"


def test_a_second_back_toggles_the_first_one(tmp_path):
    h = tmp_path / "phase-history.jsonl"
    t = [10.0]
    e = sp.PhaseEngine(_phases(), h, clock=lambda: t[0])
    e.advance("c", "operator")
    t[0] = 20.0
    e.back()
    e2 = sp.PhaseEngine(_phases(), h, clock=lambda: t[0])
    assert e2.back() is True
    assert e2.current().id == "c"


def test_unknown_phase_id_rejected(tmp_path):
    e = sp.PhaseEngine(_phases(), tmp_path / "phase-history.jsonl", clock=lambda: 10.0)
    with pytest.raises(KeyError):
        e.advance("zzz", "operator")


def test_back_on_an_empty_history_is_false(tmp_path):
    e = sp.PhaseEngine(_phases(), tmp_path / "fresh.jsonl", clock=lambda: 10.0)
    assert e.back() is False


def test_empty_phase_list_rejected(tmp_path):
    with pytest.raises(ValueError):
        sp.PhaseEngine([], tmp_path / "x.jsonl", clock=lambda: 0.0)
