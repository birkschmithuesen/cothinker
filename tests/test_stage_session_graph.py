# CoThinker — tests of stage/stage.py::session_graph_payload() (issue #48)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_stage_session_graph.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "stage") not in sys.path:
    sys.path.insert(0, str(REPO / "stage"))

import stage  # noqa: E402


def _project(tmp_path, monkeypatch, buehne: dict | None) -> None:
    monkeypatch.setattr(stage, "PROJECT", tmp_path)
    monkeypatch.setattr(stage, "_last_good", None)
    if buehne is not None:
        (tmp_path / stage.BUEHNE_JSON).write_text(
            json.dumps(buehne), encoding="utf-8")
    stage.clear_cache()


def test_session_graph_payload_reads_buehne_json(tmp_path, monkeypatch) -> None:
    graph = {"seed": "x", "nodes": [{"id": "n1"}], "edges": [], "hidden": 3}
    _project(tmp_path, monkeypatch, {"session_graph": graph})

    assert stage.session_graph_payload() == graph


def test_session_graph_payload_falls_back_without_session_graph(
        tmp_path, monkeypatch) -> None:
    _project(tmp_path, monkeypatch, {"main": {"markdown": ""}})

    assert stage.session_graph_payload() == stage.EMPTY_SESSION_GRAPH


def test_session_graph_payload_falls_back_without_buehne_json(
        tmp_path, monkeypatch) -> None:
    _project(tmp_path, monkeypatch, None)

    assert stage.session_graph_payload() == stage.EMPTY_SESSION_GRAPH
