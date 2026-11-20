#!/usr/bin/env python3
# CoThinker — prolog skill script: turn lektor answers into a verdict
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Takes the raw JSON answers the `lektor` role-profile children returned
(one `delegate_task` per question, per `lektorat/SKILL.md`'s schema) and
turns them into the same `EditorAnswer`/count/no-verdict the legacy path's
`workshop.prologue_editor.ask_question()` produces -- without re-asking a
model, because the model was already asked via `delegate_task`.

The actual judgement calls are `workshop.prologue_editor.quote_verbatim()`
(an invented/paraphrased quote invalidates the answer) and the same
count > 2 -> "no" threshold `_ask_count_question()` uses; both imported,
neither reimplemented. Only the glue -- routing one raw answer dict to the
right rule by its shape -- is new.

    python3 collect_answers.py <table_dir> <scene_filename> <answers.json>

`answers.json`: a JSON array of {"question_id", "answer"|"count",
"quote"|"quotes", "recommendation"?} objects, one per lektor reply.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(REPO))

from workshop import prologue_editor as pe  # noqa: E402


def _to_answer(raw: dict) -> pe.EditorAnswer:
    qid = raw.get("question_id", "")
    if qid in pe.COUNT_IDS:
        quotes = raw.get("quotes") or []
        count = raw.get("count")
        joined = " | ".join(quotes)
        if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                or not isinstance(quotes, list) or len(quotes) != count
                or not all(isinstance(q, str) for q in quotes)):
            return pe.EditorAnswer(qid, "error", token="bad_shape")
        if not all(pe.quote_verbatim(q, _SCENE_TEXT) for q in quotes):
            return pe.EditorAnswer(qid, "invalid", quote=joined, count=count, token="quote_not_verbatim")
        if count > 2:
            return pe.EditorAnswer(qid, "no", quote=joined, recommendation=pe.COUNT_RECOMMENDATION, count=count)
        return pe.EditorAnswer(qid, "yes", quote=joined, count=count)

    answer, quote, recommendation = raw.get("answer"), raw.get("quote"), raw.get("recommendation", "")
    if answer not in ("yes", "no") or not isinstance(quote, str):
        return pe.EditorAnswer(qid, "error", token="bad_shape")
    if not pe.quote_verbatim(quote, _SCENE_TEXT):
        return pe.EditorAnswer(qid, "invalid", quote=quote, recommendation=recommendation or "",
                               token="quote_not_verbatim")
    return pe.EditorAnswer(qid, answer, quote=quote, recommendation=recommendation or "")


_SCENE_TEXT = ""


def main() -> None:
    global _SCENE_TEXT
    if len(sys.argv) != 4:
        print("usage: collect_answers.py <table_dir> <scene_filename> <answers.json>", file=sys.stderr)
        raise SystemExit(2)
    table_dir = Path(sys.argv[1])
    _SCENE_TEXT = (table_dir / sys.argv[2]).read_text(encoding="utf-8")
    raw_answers = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))

    answers = [_to_answer(raw) for raw in raw_answers]
    print(json.dumps({
        "summary": pe.summarize(answers),
        "recommendations": pe.recommendations_text(answers),
        "answers": [asdict(a) for a in answers],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
