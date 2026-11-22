# CoThinker — tests for gateway/stage_surface.py (issue #46, Wave C.1)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_stage_surface.py"""
from __future__ import annotations

from gateway.stage_state import StageState


def test_content_has_no_optional_keys_by_default():
    content = StageState().content()
    for key in ("queue", "share", "session_graph"):
        assert key not in content


def test_content_exports_optional_keys_when_set():
    state = StageState()
    state.queue = {"mode": "tap", "speaking": None, "waiting": [], "round": None}
    state.share = {"shares": [], "total_words": 0}
    state.session_graph = {"nodes": [], "edges": []}
    content = state.content()
    assert content["queue"]["mode"] == "tap"
    assert content["share"]["total_words"] == 0
    assert content["session_graph"] == {"nodes": [], "edges": []}
