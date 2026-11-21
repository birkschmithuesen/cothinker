#!/usr/bin/env python3
# CoThinker — gateway/actions_reader.py: participant actions into the block host
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Reads the stage's action spool (`<spool>/<session folder>/actions.jsonl`,
issue #47) and hands each line to the block host as an `action` event.

The stage is the file's only writer; this reader only keeps a byte offset.
A partial last line (no newline yet) is left for the next poll. A
malformed line is skipped with one log line that names the file, the byte
offset and the reason -- never the line itself, because it may carry a
participant's name. The reader writes nothing to the event trace; the
host's dispatch does not trace payloads either.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from shared.surface import ACTION_KINDS, UserAction


def dispatcher_for(blocks) -> Callable[[UserAction], object]:
    """The session host's dispatch path: `SessionBlocks.dispatch("action", ...)`."""
    return lambda action: blocks.dispatch("action", {"action": action})


def _parse(raw: bytes) -> UserAction:
    """Raises ValueError (message = reason, no line content) if invalid."""
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("not valid JSON") from None
    if not isinstance(d, dict):
        raise ValueError("not a JSON object")
    if d.get("v") != 1:
        raise ValueError("unsupported version")
    for key in ("person", "intent_id", "kind"):
        if not isinstance(d.get(key), str) or not d[key]:
            raise ValueError(f"missing or invalid {key}")
    if d["kind"] not in ACTION_KINDS:
        raise ValueError("unknown kind")
    if not isinstance(d.get("payload"), dict):
        raise ValueError("missing or invalid payload")
    try:
        at = datetime.fromisoformat(str(d["at"]).replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise ValueError("missing or invalid at") from None
    return UserAction(intent_id=d["intent_id"], kind=d["kind"],
                      person=d["person"], payload=d["payload"], at=at)


class ActionsReader:
    def __init__(self, path, dispatch: Callable[[UserAction], object],
                 log: Callable[[str], None]) -> None:
        self.path = Path(path)
        self._dispatch = dispatch
        self._log = log
        self._offset = 0

    def poll(self) -> int:
        """Dispatches every complete new line; returns how many valid ones."""
        try:
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return 0
        done = 0
        pos = 0
        while True:
            end = data.find(b"\n", pos)
            if end < 0:
                break
            raw, at_offset = data[pos:end], self._offset + pos
            pos = end + 1
            if raw.strip():
                try:
                    action = _parse(raw)
                except ValueError as e:
                    self._log(f"actions: skipped line at byte {at_offset} "
                              f"of {self.path}: {e}")
                else:
                    try:
                        self._dispatch(action)
                    except Exception as e:  # noqa: BLE001 -- type only, the message may carry a name
                        self._log(f"actions: dispatch failed for line at byte "
                                  f"{at_offset} of {self.path}: {type(e).__name__}")
                    else:
                        done += 1
        self._offset += pos
        return done
