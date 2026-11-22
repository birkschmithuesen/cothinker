# CoThinker — tests for the Surface API "message" user action kind
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_surface_message_action.py"""
from __future__ import annotations

from shared import surface as su


def test_user_action_accepts_message_kind():
    action = su.UserAction(
        intent_id="a",
        kind="message",
        person="P3",
        payload={"text": "hello"},
        at=100.0,
    )
    assert action.kind == "message"


def test_message_is_in_action_kinds():
    assert "message" in su.ACTION_KINDS
