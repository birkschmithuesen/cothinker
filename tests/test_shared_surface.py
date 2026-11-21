# CoThinker — shared/surface.py: Surface API types and intent board
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_surface.py"""
from __future__ import annotations

import pytest

from shared import surface as su


def make_intent(id_, kind="prompt", audience="room", payload=None, expires_at=None):
    return su.ViewIntent(
        id=id_,
        block="test-block",
        kind=kind,
        audience=audience,
        payload=payload if payload is not None else {},
        expires_at=expires_at,
    )


def test_current_keeps_insertion_order_for_two_distinct_intents():
    now = [100.0]
    board = su.IntentBoard(clock=lambda: now[0])
    first = make_intent("a")
    second = make_intent("b")
    board.put(first)
    board.put(second)
    assert board.current() == [first, second]


def test_put_replaces_existing_id_in_place_without_reordering():
    now = [100.0]
    board = su.IntentBoard(clock=lambda: now[0])
    board.put(make_intent("a", payload={"v": 1}))
    board.put(make_intent("b", payload={"v": 2}))
    updated = make_intent("a", payload={"v": 3})
    board.put(updated)
    current = board.current()
    assert [intent.id for intent in current] == ["a", "b"]
    assert current[0].payload == {"v": 3}


def test_expired_intent_is_dropped_from_current():
    now = [100.0]
    board = su.IntentBoard(clock=lambda: now[0])
    board.put(make_intent("a", expires_at=100.0))
    now[0] = 100.0
    assert board.current() == []


def test_for_surface_drops_kinds_not_in_surface_and_returns_empty_when_nothing_matches():
    now = [100.0]
    board = su.IntentBoard(clock=lambda: now[0])
    board.put(make_intent("a", kind="prompt"))
    logged = []
    result = board.for_surface(frozenset({"timer"}), log=logged.append)
    assert result == []
    assert len(logged) == 1


def test_for_surface_logs_unrenderable_id_exactly_once():
    now = [100.0]
    board = su.IntentBoard(clock=lambda: now[0])
    board.put(make_intent("a", kind="prompt"))
    logged = []
    board.for_surface(frozenset({"timer"}), log=logged.append)
    board.for_surface(frozenset({"timer"}), log=logged.append)
    assert len(logged) == 1


def test_view_intent_rejects_unknown_kind():
    with pytest.raises(ValueError):
        make_intent("a", kind="nope")


def test_view_intent_rejects_audience_not_matching_pattern():
    with pytest.raises(ValueError):
        make_intent("a", audience="everyone")


def test_view_intent_accepts_person_audience():
    intent = make_intent("a", audience="person:P3")
    assert intent.audience == "person:P3"


def test_user_action_rejects_unknown_kind():
    with pytest.raises(ValueError):
        su.UserAction(intent_id="a", kind="swipe", person="P3", payload={}, at=100.0)
