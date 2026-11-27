# CoThinker — tests for blocks/release: publish a session's results to
# guests (issue #102)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q blocks/release/tests/test_release.py

Golden characterization of `build_release`/`build_withdrawal` against
`tests/golden/release/*.json` (ported from the archive's
`workshop/release.py`), plus the block-level guarantee the issue names:
nothing is released without an operator action.
"""
from __future__ import annotations

import json
from pathlib import Path

from blocks.release import ReleaseBlock, RELEASE_INTENT_ID
from blocks.release.logic import build_release, build_withdrawal
from shared.blocks import BlockContext
from shared.modules import CoreEvent, StateStore

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "tests" / "golden" / "release"


def _load(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def test_build_release_matches_golden():
    case = _load("release.input.json")
    expected = _load("release.expected.json")
    released = build_release(version=case["version"], quote=case["quote"],
                              prologue=case["prologue"], at_ms=case["at_ms"])
    assert released == expected


def test_build_withdrawal_matches_golden():
    case = _load("withdraw.input.json")
    expected = _load("withdraw.expected.json")
    withdrawn = build_withdrawal(case["released"], case["at_ms"])
    assert withdrawn == expected


def make_block(tmp_path, target):
    emitted = []
    config = {} if target is None else {"target": target}
    ctx = BlockContext(
        instance=None, session_dir=tmp_path,
        state=StateStore(tmp_path / ".blocks" / "release"),
        agent=None, llm=None, language="de", clock=lambda: 1700000000.0,
        emit=emitted.append, artefact=lambda name, text: None)
    block = ReleaseBlock()
    block.start(ctx, config)
    return block, emitted


def operator_release_action():
    case = _load("release.input.json")
    return CoreEvent("action", {
        "intent_id": RELEASE_INTENT_ID, "kind": "accept", "person": "operator",
        "payload": {"version": case["version"], "quote": case["quote"],
                     "prologue": case["prologue"]},
        "at": 1700000000.0})


def test_nothing_is_released_without_an_operator_action(tmp_path):
    block, emitted = make_block(tmp_path, target="lobby-screen")

    # Reaching the goodbye cue by itself releases nothing.
    out = block.on_event(CoreEvent("phase", {"from": "closing", "to": "goodbye", "cause": "agent"}))
    assert out == ()
    assert emitted == []

    # An unconfigured target makes the operator's action a no-op too.
    unconfigured, unconfigured_emitted = make_block(tmp_path, target=None)
    out = unconfigured.on_event(operator_release_action())
    assert out == ()
    assert unconfigured_emitted == []

    # The operator's own action, with a configured target, does release.
    out = block.on_event(operator_release_action())
    assert len(out) == 1
    assert out[0].kind == "block_output"
    expected = _load("release.expected.json")
    assert out[0].payload == {"from": "release", "released": expected}
    assert len(emitted) == 1
    assert emitted[0].kind == "artefact"
    assert emitted[0].payload == {"target": "lobby-screen", "released": expected}
