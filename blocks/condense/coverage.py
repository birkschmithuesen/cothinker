# CoThinker — blocks/condense/coverage.py: parse the model reply, check topic coverage
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Parses the machine-readable reply and decides which topics are covered.

Reply shape: {"topics": [{"topic_id": str, "text": str, "quotes": [str]}]}.
A topic counts as covered when the reply has a non-empty text for it. Topic
ids the reply invents are ignored.
"""
from __future__ import annotations

import json
from typing import Mapping, Sequence


def parse_reply(raw: str) -> dict[str, dict]:
    """topic_id -> {"text": str, "quotes": list}. Unparseable reply -> {}."""
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(s[start:end + 1])
    except ValueError:
        return {}
    items = data.get("topics") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for it in items if isinstance(items, list) else ():
        if not isinstance(it, dict) or not isinstance(it.get("topic_id"), str):
            continue
        text = it.get("text")
        quotes = it.get("quotes")
        out[it["topic_id"]] = {
            "text": text.strip() if isinstance(text, str) else "",
            "quotes": quotes if isinstance(quotes, list) else [],
        }
    return out


def missing_topics(topic_ids: Sequence[str], covered: Mapping[str, object]) -> list[str]:
    """The ids of `topic_ids` that are not in `covered`, in order."""
    return [t for t in topic_ids if t not in covered]
