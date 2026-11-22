# CoThinker — tests of the level-1 question set
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The fixed question set of docs/entwurf-meeting-modi.md, "Ebene 1".

Covered: blocks/decisions_tasks_questions/questions.py only -- no LLM backend, no network. The
decision interface that asks these questions of Jev/laya/fake is a later
card's file (blocks/decisions_tasks_questions/decide.py); this module is nothing but the data.

    python3 -m pytest -q tests/test_questions.py
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import pytest  # noqa: E402
from script_checks import check, collected, done  # noqa: E402
from shared.events import is_token  # noqa: E402
import blocks.decisions_tasks_questions.questions as q  # noqa: E402

print("the set is 10 noul questions plus exactly one choice question")
check("eleven questions in total", len(q.QUESTIONS) == 11, str(len(q.QUESTIONS)))
noul = [f for f in q.QUESTIONS if f.kind == "noul"]
choice = [f for f in q.QUESTIONS if f.kind == "choice"]
check("ten noul questions", len(noul) == 10, str(len(noul)))
check("one choice question", len(choice) == 1, str(len(choice)))
check("no third kind slipped in", len(noul) + len(choice) == len(q.QUESTIONS))

print("\nevery question id is unique and a valid event-trace token")
ids = [f.id for f in q.QUESTIONS]
check("ids are unique", len(set(ids)) == len(ids), str(ids))
for f in q.QUESTIONS:
    check(f"id '{f.id}' passes the events.py token rule", is_token(f.id))

print("\neach question carries non-empty German text")
for f in q.QUESTIONS:
    check(f"'{f.id}' has text", isinstance(f.text, str) and f.text.strip() != "")

print("\nthe questions are frozen (immutable) dataclasses")
check("Question is a frozen dataclass",
      dataclasses.is_dataclass(q.Question)
      and q.Question.__dataclass_params__.frozen)
try:
    object.__setattr__  # sanity: dataclass frozen raises on direct set
    q.QUESTIONS[0].text = "geaendert"
    check("mutating a question raises", False)
except dataclasses.FrozenInstanceError:
    check("mutating a question raises", True)

print("\nthe choice question carries the four meeting phases")
[phase] = choice
check("phase choices are exactly the four meeting phases",
      phase.choices == ("einstieg", "diskussion", "entscheidung", "abschluss"),
      str(phase.choices))
check("a noul question carries no choices",
      all(f.choices == () for f in noul))

print("\nQUESTIONS_VERSION exists and is a small stable marker")
check("QUESTIONS_VERSION is set", q.QUESTIONS_VERSION == 1, str(q.QUESTIONS_VERSION))

print("\nthe hash is a stable, short, token-safe digest of the question set")
h1 = q.questions_hash()
h2 = q.questions_hash()
check("the hash is stable across calls", h1 == h2, f"{h1} != {h2}")
check("the hash is 12-16 hex chars", 12 <= len(h1) <= 16
      and all(c in "0123456789abcdef" for c in h1), h1)
check("the hash itself is a valid event-trace token", is_token(h1))

print("\nthe hash changes when a question's text changes")
changed = tuple(
    dataclasses.replace(f, text=f.text + " ") if i == 0 else f
    for i, f in enumerate(q.QUESTIONS)
)
h_changed = q.questions_hash(changed)
check("a text change changes the hash", h_changed != h1, f"{h_changed} == {h1}")

print("\nthe hash changes when a choice value changes")
changed_choice = tuple(
    dataclasses.replace(f, choices=("einstieg", "diskussion", "entscheidung",
                                     "andere")) if f.kind == "choice" else f
    for f in q.QUESTIONS
)
h_choice = q.questions_hash(changed_choice)
check("a choice change changes the hash", h_choice != h1, f"{h_choice} == {h1}")

print("\nthe hash does NOT change for an unrelated field (purpose)")
changed_purpose = tuple(
    dataclasses.replace(f, purpose="anders") if i == 0 else f
    for i, f in enumerate(q.QUESTIONS)
)
h_purpose = q.questions_hash(changed_purpose)
check("purpose is not part of the hashed identity", h_purpose == h1,
      f"{h_purpose} != {h1}")

print("\nlevel-2 extra question sets (MM-B): separate, level-1 unchanged")
check("QUESTIONS_HASH unchanged by MM-B",
      q.QUESTIONS_HASH == q.questions_hash(q.QUESTIONS)
      and q.CONFIRM_ID not in [f.id for f in q.QUESTIONS]
      and q.MODE_ID not in [f.id for f in q.QUESTIONS])
check("CONFIRM_QUESTIONS: one noul, id rueckfrage_bejaht",
      [(f.id, f.kind) for f in q.CONFIRM_QUESTIONS]
      == [("rueckfrage_bejaht", "noul")])
check("MODE_QUESTIONS: one choice over the three modes",
      [(f.id, f.kind, f.choices) for f in q.MODE_QUESTIONS]
      == [("termin_art", "choice", ("sitzung", "arbeit", "folge"))])
from shared import minutes_format as _pd  # noqa: E402
check("MEETING_MODES == protocol_doc.MODES", q.MEETING_MODES == _pd.MODES)
for f in (*q.CONFIRM_QUESTIONS, *q.MODE_QUESTIONS):
    check(f"{f.id}: ASCII token id, ASCII text",
          f.id.isascii() and f.text.isascii())
import blocks.decisions_tasks_questions.decide as _d  # noqa: E402
check("termin_art has criteria for every mode",
      set(_d.CHOICE_CRITERIA.get("termin_art", {})) == set(q.MEETING_MODES))

done()


@pytest.mark.parametrize("name,ok,info", collected())
def test_check(name, ok, info):
    assert ok, info
