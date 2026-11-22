# CoThinker — tests of the web view
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The speaker queue and the speaking share on the stage (issue #37, ADR
0010, ADR 0018 intent kinds `queue`/`share`): two optional sections,
hidden while their key is absent from `buehne.json`, fed by speech
recognition and a language model -- nothing in the file is trusted.

    python3 -m pytest -q tests/test_stage_queue_share.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage"))

import stage as b  # noqa: E402


def v2(**felder) -> dict:
    """A minimal schema-v2 `buehne.json`, field for field the same shape
    `StageState.content()` writes — trimmed to what `stage_parts()` needs
    so this file does not have to carry the whole fixture from
    `tests/test_stage.py`."""
    daten = {
        "version": 2,
        "updated": "2026-11-21T10:00:00",
        "status": {"state": "listening", "text": "", "ticking": False,
                   "since": ""},
        "mirror": "",
        "headline": "",
        "main": {"markdown": "", "collapsed_lines": 0, "collapsed_markdown": ""},
        "decisions": [],
        "notices": [],
        "question": None,
        "links": [],
        "parked": 0,
        "parked_entries": [],
        "age_visible_after_s": 20,
        "follow_up_after_s": 120,
    }
    daten.update(felder)
    return daten


def setze(daten: dict) -> None:
    (b.PROJECT / "buehne.json").write_text(
        __import__("json").dumps(daten, ensure_ascii=False), encoding="utf-8")
    b.clear_cache()


@pytest.fixture(autouse=True)
def proj(tmp_path):
    """A fresh project folder, the same posture as `tests/test_stage.py`'s
    own `proj` fixture (duplicated here rather than imported — this file
    stays free-standing, no `conftest.py` change needed)."""
    saved = (b.PROJECT, b._last_good)
    folder = tmp_path / "proj"
    folder.mkdir()
    b.PROJECT = folder
    b.clear_cache()
    b._last_good = None
    yield folder
    b.PROJECT, b._last_good = saved
    b.clear_cache()


def test_queue_hidden_without_key():
    setze(v2())
    teile = b.stage_parts()
    assert teile["queue"] is None


def test_queue_lists_waiting_in_order():
    setze(v2(queue={"mode": "round_robin", "speaking": ["Anna"],
                     "waiting": ["Bert", "Carla", "Dag"], "round": 3}))
    teile = b.stage_parts()
    assert teile["queue"] is not None
    assert teile["queue"]["waiting"] == ["Bert", "Carla", "Dag"]
    assert teile["queue"]["speaking"] == ["Anna"]


def test_share_hidden_without_key():
    setze(v2())
    teile = b.stage_parts()
    assert teile["share"] is None


def test_share_bars_sum_to_one():
    setze(v2(share={"shares": [{"person": "Anna", "share": 0.3},
                                {"person": "Bert", "share": 0.3},
                                {"person": "Carla", "share": 0.3}],
                     "total_words": 900}))
    teile = b.stage_parts()
    share = teile["share"]
    assert share is not None
    total = sum(s["share"] for s in share["shares"])
    assert abs(total - 1.0) < 1e-9, total


def test_names_are_escaped():
    setze(v2(queue={"speaking": ["<b>Eva</b>"], "waiting": ["<i>Finn</i>"]},
             share={"shares": [{"person": "<script>alert(1)</script>",
                                "share": 1.0}],
                    "total_words": 10}))
    teile = b.stage_parts()
    queue = teile["queue"]
    share = teile["share"]
    assert "<b>" not in queue["speaking"][0] and "Eva" in queue["speaking"][0]
    assert "<i>" not in queue["waiting"][0] and "Finn" in queue["waiting"][0]
    person = share["shares"][0]["person"]
    assert "<script>" not in person and "alert(1)" in person
