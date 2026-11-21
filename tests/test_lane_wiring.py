# CoThinker — tests for the consumer's lane wiring (build_lanes, agent_seam)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_lane_wiring.py

The agent seam (COTHINKER_AGENT=api) versus the worker-command path, through
the real `build_lanes` and `agent_seam`; nothing is started or contacted."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))

import agent_lane  # noqa: E402
from gateway import consumer  # noqa: E402
from lanes import LaneQueue  # noqa: E402
from stage_state import StageState  # noqa: E402

ENV = ("COTHINKER_AGENT", "COTHINKER_AGENT_CALL", "COTHINKER_AGENT_URL",
       "COTHINKER_AGENT_KEY_ENV", "API_SERVER_KEY", "COTHINKER_EXTRA_DIRS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def _build(tmp_path, seam):
    args = SimpleNamespace(topic_threshold=0.6, topic_min_s=0.0)
    return consumer.build_lanes(
        args, lambda *a, **k: None, call_command=["true"], session_id="s",
        project_agenda="P", state=StageState(), writer=SimpleNamespace(),
        folder=tmp_path, path=tmp_path / "p.md", seam=seam)


def test_without_the_seam_env_there_is_no_seam(tmp_path):
    assert consumer.agent_seam(lambda *a, **k: None) is None
    assert isinstance(_build(tmp_path, None).call_lane, LaneQueue)


def test_the_seam_needs_a_url_and_a_key(monkeypatch):
    monkeypatch.setenv("COTHINKER_AGENT", "api")
    with pytest.raises(SystemExit):
        consumer.agent_seam(lambda *a, **k: None)


def _api_env(monkeypatch):
    monkeypatch.setenv("COTHINKER_AGENT", "api")
    monkeypatch.setenv("COTHINKER_AGENT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("API_SERVER_KEY", "k")


def test_the_seam_alone_leaves_the_call_lane_on_the_worker(monkeypatch, tmp_path):
    _api_env(monkeypatch)
    logs: list = []
    seam = consumer.agent_seam(lambda *a, **k: logs.append(" ".join(map(str, a))))
    assert seam is not None and callable(seam[0]) and isinstance(seam[1], str)
    assert any("call=worker" in line for line in logs)
    assert isinstance(_build(tmp_path, seam).call_lane, LaneQueue)


def test_the_call_lane_follows_the_seam_when_asked(monkeypatch, tmp_path):
    _api_env(monkeypatch)
    monkeypatch.setenv("COTHINKER_AGENT_CALL", "api")
    seam = consumer.agent_seam(lambda *a, **k: None)
    lane = _build(tmp_path, seam).call_lane
    assert not isinstance(lane, LaneQueue)
    assert type(lane).__module__ == agent_lane.__name__
