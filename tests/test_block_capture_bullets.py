# CoThinker — tests for blocks/capture_bullets (issue #49, Wave B.8)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `capture-bullets` block with invented agent replies.

    python3 -m pytest -q tests/test_block_capture_bullets.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from blocks.capture_bullets import CaptureBulletsBlock  # noqa: E402
from blocks.capture_bullets.parse import (  # noqa: E402
    CapturedTurn, ParseFailure, parse_reply)
from shared.modules import CoreEvent  # noqa: E402

FULL = """Hier die Eintraege.
```json
{
  "verlauf": ["Die Runde startet mit dem Budget."],
  "entscheidungen": [{"text": "Das Budget bleibt bei 5000 Euro.", "topic_id": "t1"}],
  "aufgaben": ["Anna schickt die Zahlen bis Freitag."],
  "offene_fragen": ["Wer prueft den Vertrag?"],
  "buehne": [{"action": "headline", "text": "Budget"}]
}
```
"""


def _block() -> CaptureBulletsBlock:
    b = CaptureBulletsBlock()
    b.start(None, {})
    return b


def _outputs(events) -> list[dict]:
    return [e.payload for e in events if e.kind == "block_output"]


def test_full_reply_parses():
    got = parse_reply(FULL)
    assert isinstance(got, CapturedTurn)
    assert got.verlauf == [{"text": "Die Runde startet mit dem Budget.", "topic_id": ""}]
    assert got.entscheidungen == [
        {"text": "Das Budget bleibt bei 5000 Euro.", "topic_id": "t1"}]
    assert got.aufgaben[0]["text"] == "Anna schickt die Zahlen bis Freitag."
    assert got.offene_fragen[0]["text"] == "Wer prueft den Vertrag?"
    assert got.buehne == [{"action": "headline", "text": "Budget"}]


def test_missing_section_is_empty():
    got = parse_reply('```json\n{"verlauf": ["Nur Verlauf."]}\n```')
    assert isinstance(got, CapturedTurn)
    assert got.entscheidungen == [] and got.aufgaben == []
    assert got.offene_fragen == [] and got.buehne == []
    assert len(got.verlauf) == 1
    assert isinstance(parse_reply('{"aufgaben": ["Bare object."]}'), CapturedTurn)


def test_non_json_is_parse_failure():
    got = parse_reply("Das war ein guter Termin, nichts zu tragen.")
    assert isinstance(got, ParseFailure)
    assert got.failed is False
    assert got.raw == "Das war ein guter Termin, nichts zu tragen."


def test_failure_marker_text_is_failure():
    text = 'API call failed: HTTP 429 ```json\n{"verlauf": ["x"]}\n```'
    got = parse_reply(text)
    assert isinstance(got, ParseFailure)
    assert got.failed is True
    assert got.raw == text


def test_bullets_become_block_outputs():
    out = _outputs(_block().on_event(
        CoreEvent("agent_reply", {"text": FULL, "turn": 3})))
    bullets = [p for p in out if p["kind"] == "bullet"]
    assert [(p["section"], p["text"], p["topic_id"]) for p in bullets] == [
        ("verlauf", "Die Runde startet mit dem Budget.", ""),
        ("entscheidungen", "Das Budget bleibt bei 5000 Euro.", "t1"),
        ("aufgaben", "Anna schickt die Zahlen bis Freitag.", ""),
        ("offene_fragen", "Wer prueft den Vertrag?", ""),
    ]
    assert all(p["from"] == "capture-bullets" for p in out)


def test_buehne_items_go_to_proposals():
    out = _outputs(_block().on_event(
        CoreEvent("agent_reply", {"text": FULL, "turn": 3})))
    props = [p for p in out if p["kind"] == "stage_proposal"]
    assert props == [{"from": "capture-bullets", "kind": "stage_proposal",
                      "turn": 3, "action": {"action": "headline", "text": "Budget"}}]
    assert not any(p["kind"] == "bullet" and p["section"] == "buehne" for p in out)


def test_unknown_key_ignored_and_logged(caplog):
    text = '```json\n{"verlauf": ["A."], "stimmung": ["gut"]}\n```'
    with caplog.at_level(logging.WARNING):
        got = parse_reply(text)
    assert isinstance(got, CapturedTurn)
    assert len(got.verlauf) == 1
    assert "stimmung" in caplog.text
