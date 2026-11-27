# CoThinker — tests for blocks/choice_list/: the choice-list block (issue
# #86, plan task E.2.18)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_choice_list.py

The golden case (`test_selection_view_matches_golden`) is hand-written
(`tests/golden/choice_list/accept_reject_edit.*.json`), not replayed from
the archive's `auswahl.py`: that frozen source is not reachable from this
worktree. It encodes the same row/state/counter shape `views.py` builds.
"""
from __future__ import annotations

import json
from pathlib import Path

from blocks.choice_list import ChoiceListBlock
from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent, StateStore
from shared.surface import ViewIntent

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "choice_list"


def make_block(tmp_path):
    block = ChoiceListBlock()
    emitted = []
    ctx = BlockContext(
        instance=None, session_dir=Path(tmp_path),
        state=StateStore(Path(tmp_path) / block.id), agent=None, llm=None,
        language="de", clock=lambda: 0.0, emit=emitted.append,
        artefact=lambda name, text: None)
    block.start(ctx, {})
    return block, emitted


def offer(block, **offer_kwargs):
    return block.on_event(CoreEvent("block_output", {"offer_choices": offer_kwargs}))


def act(block, intent_id, kind, payload):
    return block.on_event(CoreEvent("action", {
        "intent_id": intent_id, "kind": kind, "person": "ada", "payload": payload, "at": 0.0}))


SAMPLE_GROUPS = [{"heading": "Themen", "items": [
    {"id": "ada", "text": "Soll Ada das Budget pruefen?"},
    {"id": "ben", "text": "Soll Ben die Agenda schreiben?"},
    {"id": "cleo", "text": "Soll Cleo das Protokoll fuehren?"},
]}]


def test_offer_emits_choice_list_intent(tmp_path):
    block, emitted = make_block(tmp_path)
    offer(block, list_id="q1", title="Fragen zur Auswahl", groups=SAMPLE_GROUPS,
          allow_edit=True, done_label="Fertig")

    assert len(emitted) == 1
    intent = emitted[0]
    assert isinstance(intent, ViewIntent)
    assert intent.id == "choices:q1"
    assert intent.block == "choice-list"
    assert intent.kind == "choice_list"
    payload = intent.payload
    assert payload["title"] == "Fragen zur Auswahl"
    rows = payload["groups"][0]["rows"]
    assert [r["state"] for r in rows] == ["open", "open", "open"]
    assert payload["counter"] == {"open": 3, "accepted": 0, "rejected": 0, "edited": 0, "total": 3}


def test_accept_reject_edit_update_counter(tmp_path):
    block, emitted = make_block(tmp_path)
    offer(block, list_id="q1", title="Fragen zur Auswahl", groups=SAMPLE_GROUPS,
          allow_edit=True, done_label="Fertig")

    act(block, "choices:q1", "accept", {"item": "ada"})
    act(block, "choices:q1", "reject", {"item": "ben"})
    act(block, "choices:q1", "edit", {"item": "cleo", "text": "Soll Cleo die Nachbereitung uebernehmen?"})

    final = emitted[-1].payload
    rows_by_id = {r["id"]: r for r in final["groups"][0]["rows"]}
    assert rows_by_id["ada"]["state"] == "accepted"
    assert rows_by_id["ben"]["state"] == "rejected"
    assert rows_by_id["cleo"]["state"] == "edited"
    assert rows_by_id["cleo"]["text"] == "Soll Cleo die Nachbereitung uebernehmen?"
    assert final["counter"] == {"open": 0, "accepted": 1, "rejected": 1, "edited": 1, "total": 3}


def test_done_emits_choices_done_with_accepted_and_edits(tmp_path):
    block, emitted = make_block(tmp_path)
    offer(block, list_id="q1", title="Fragen zur Auswahl", groups=SAMPLE_GROUPS,
          allow_edit=True, done_label="Fertig")
    act(block, "choices:q1", "accept", {"item": "ada"})
    act(block, "choices:q1", "reject", {"item": "ben"})
    act(block, "choices:q1", "edit", {"item": "cleo", "text": "Soll Cleo die Nachbereitung uebernehmen?"})

    out = act(block, "choices:q1", "choose", {"done": True})

    assert len(out) == 1
    done = out[0]
    assert done.kind == "block_output"
    result = done.payload["choices_done"]
    assert result["list_id"] == "q1"
    assert sorted(result["accepted"]) == ["ada", "cleo"]
    assert result["edited"] == {"cleo": "Soll Cleo die Nachbereitung uebernehmen?"}


def test_reoffer_keeps_previous_states(tmp_path):
    block, emitted = make_block(tmp_path)
    offer(block, list_id="q1", title="Fragen zur Auswahl", groups=SAMPLE_GROUPS,
          allow_edit=True, done_label="Fertig")
    act(block, "choices:q1", "accept", {"item": "ada"})

    reoffer_groups = [{"heading": "Themen", "items": [
        {"id": "ada", "text": "Soll Ada das Budget pruefen?"},
        {"id": "ben", "text": "Soll Ben die Agenda schreiben?"},
        {"id": "cleo", "text": "Soll Cleo das Protokoll fuehren?"},
        {"id": "dag", "text": "Soll Dag die Einladung verschicken?"},
    ]}]
    offer(block, list_id="q1", title="Fragen zur Auswahl", groups=reoffer_groups,
          allow_edit=True, done_label="Fertig")

    final = emitted[-1].payload
    rows_by_id = {r["id"]: r for r in final["groups"][0]["rows"]}
    assert rows_by_id["ada"]["state"] == "accepted"
    assert rows_by_id["ben"]["state"] == "open"
    assert rows_by_id["dag"]["state"] == "open"
    assert final["counter"]["total"] == 4


def test_choice_status_tool_read_only(tmp_path):
    block, emitted = make_block(tmp_path)
    offer(block, list_id="q1", title="Fragen zur Auswahl", groups=SAMPLE_GROUPS,
          allow_edit=True, done_label="Fertig")
    act(block, "choices:q1", "accept", {"item": "ada"})

    tools_by_name = {t.name: t for t in block.tools()}
    status_tool = tools_by_name["choice_status"]
    assert status_tool.read_only is True

    before = len(emitted)
    result = status_tool.fn(list_id="q1")
    assert len(emitted) == before  # a read-only call never emits a new intent
    assert result["ok"] is True
    assert result["data"]["counter"]["accepted"] == 1

    offer_tool = tools_by_name["offer_choices"]
    assert offer_tool.read_only is False


def test_selection_view_matches_golden(tmp_path):
    block, emitted = make_block(tmp_path)
    case = json.loads((GOLDEN_DIR / "accept_reject_edit.input.json").read_text(encoding="utf-8"))
    expected = json.loads((GOLDEN_DIR / "accept_reject_edit.expected.json").read_text(encoding="utf-8"))

    offer(block, **case["offer"])
    for action in case["actions"]:
        act(block, f"choices:{case['offer']['list_id']}", action["kind"], action["payload"])

    assert emitted[-1].payload == expected
