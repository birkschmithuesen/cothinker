#!/usr/bin/env python3
# CoThinker — prolog skill script: question selection for one genre
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Thin CLI wrapper around `workshop.prologue_editor.questions_for()`:
which editor questions a draft of this genre needs to be asked, from the
one catalogue `workshop/prompts/prologue_editor.md`. Reimplements no
logic.

    python3 pick_questions.py <genre>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(REPO))

from workshop import prologue_editor as pe  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: pick_questions.py <genre>", file=sys.stderr)
        raise SystemExit(2)
    genre = sys.argv[1]
    questions = pe.questions_for(genre)
    print(json.dumps([{"id": qid, "text": text} for qid, text in questions], ensure_ascii=False))


if __name__ == "__main__":
    main()
