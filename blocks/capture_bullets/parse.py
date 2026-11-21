# CoThinker — blocks/capture_bullets/parse.py: the main-turn reply parser
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Parses the main agent turn's reply (issue #49).

The reply is ONE fenced JSON block (```json ... ```) or a bare JSON object
with these keys, each a list:

    verlauf, entscheidungen, aufgaben, offene_fragen, buehne

A missing section is an empty list; an unknown key is ignored and logged
(warning). Anything else is a `ParseFailure`. Text carrying one of the
worker's `FAILURE_MARKERS` is a failure even when it contains valid JSON:
an API error message is not minutes.

Item shape (the four minutes sections): a string, or an object
`{"text": "...", "topic_id": "..."}`. Both parse to the object form
`{"text": str, "topic_id": str}`; `topic_id` is "" when absent (the host
then files into the current topic). Items with empty text are dropped.
`buehne` items are stage proposals and stay as the objects the agent wrote
(`{"action": "headline", "text": ...}`, the same shape the proposal file
takes); non-object items are dropped.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Union

from gateway.worker import FAILURE_MARKERS

log = logging.getLogger(__name__)

MINUTES_SECTIONS = ("verlauf", "entscheidungen", "aufgaben", "offene_fragen")
STAGE_SECTION = "buehne"
SECTIONS = MINUTES_SECTIONS + (STAGE_SECTION,)

_FENCE_RE = re.compile(r"```(?:json|JSON)?[ \t]*\r?\n(.*?)```", re.DOTALL)


@dataclass
class CapturedTurn:
    verlauf: list = field(default_factory=list)
    entscheidungen: list = field(default_factory=list)
    aufgaben: list = field(default_factory=list)
    offene_fragen: list = field(default_factory=list)
    buehne: list = field(default_factory=list)


@dataclass
class ParseFailure:
    """`raw` is the full reply text, kept for the run trace. `failed` is
    True when the text matched a failure marker (the worker failed), False
    when it merely was not the expected JSON."""
    reason: str
    raw: str
    failed: bool = False


def _item(x: object) -> dict | None:
    if isinstance(x, str):
        text, topic = x, ""
    elif isinstance(x, dict):
        text, topic = x.get("text"), x.get("topic_id") or ""
    else:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    return {"text": text.strip(), "topic_id": str(topic)}


def _json_object(text: str) -> object:
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)]
    candidates.append(text)
    for cand in candidates:
        try:
            return json.loads(cand.strip())
        except ValueError:
            continue
    return None


def parse_reply(text: str) -> Union[CapturedTurn, ParseFailure]:
    text = text if isinstance(text, str) else ""
    if any(marker in text for marker in FAILURE_MARKERS):
        return ParseFailure("failure marker in reply", text, failed=True)
    obj = _json_object(text)
    if not isinstance(obj, dict):
        return ParseFailure("no JSON object in reply", text)
    turn = CapturedTurn()
    for key, value in obj.items():
        if key not in SECTIONS:
            log.warning("capture-bullets: unknown key %r ignored", key)
            continue
        if not isinstance(value, list):
            log.warning("capture-bullets: section %r is not a list, ignored", key)
            continue
        if key == STAGE_SECTION:
            items = [v for v in value if isinstance(v, dict)]
        else:
            items = [i for i in map(_item, value) if i is not None]
        setattr(turn, key, items)
    return turn
