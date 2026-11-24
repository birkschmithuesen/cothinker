# CoThinker — shared/actions_spool.py: the one write path for the action spool
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Appends one line to a session's action spool (issue #183): the file
`gateway/actions_reader.py` reads, `<spool dir>/<session folder>/actions.jsonl`.

Every surface that can act (the stage's own `/action` endpoint, the surface
contract's `POST /actions` in live mode) writes through this one function,
so the file format and the single-write-under-`flock` guarantee live in one
place instead of being duplicated per writer.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ACTIONS_FILE = "actions.jsonl"


def append_action(spool_dir: Path, session_folder_name: str, person: str,
                   intent_id: str, kind: str, payload: dict) -> None:
    """Appends one JSON line to `<spool_dir>/<session_folder_name>/actions.jsonl`.

    Line: `{"v": 1, "at", "person", "intent_id", "kind", "payload"}`; `at`
    is ISO-8601 UTC with milliseconds and a `Z` suffix, e.g.
    `2026-09-08T19:12:04.123Z`. The append happens under an exclusive
    `flock` as a single write, so two writers never interleave a line.
    """
    at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    at = at.replace("+00:00", "Z")
    line = json.dumps({"v": 1, "at": at, "person": person,
                       "intent_id": intent_id, "kind": kind,
                       "payload": payload}, ensure_ascii=False) + "\n"
    folder = Path(spool_dir) / session_folder_name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder / ACTIONS_FILE,
                 os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
