# CoThinker — tests for shared/actions_spool.py (issue #183)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_actions_spool.py

L1 (ADR 0022): the spool writer every surface write path shares (the
stage's own `/action` handler, and the surface contract's `POST /actions`
in live mode) in isolation -- format, file placement, and that concurrent
callers never interleave into a torn line.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from gateway.actions_reader import _parse
from shared.actions_spool import ACTIONS_FILE, append_action

AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_append_action_writes_one_well_formed_line(tmp_path: Path):
    append_action(tmp_path, "2026-11-24-today", "0123456789abcdef",
                  "queue:room", "tap", {"op": "request"})

    path = tmp_path / "2026-11-24-today" / ACTIONS_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d == {
        "v": 1, "at": d["at"], "person": "0123456789abcdef",
        "intent_id": "queue:room", "kind": "tap",
        "payload": {"op": "request"},
    }
    assert AT_RE.match(d["at"]), f"not ISO-8601 UTC ms Z: {d['at']!r}"


def test_append_action_creates_the_session_folder(tmp_path: Path):
    folder = tmp_path / "2026-11-24-today"
    assert not folder.exists()

    append_action(tmp_path, "2026-11-24-today", "0123456789abcdef",
                  "participant:name", "edit", {"name": "Mira"})

    assert folder.is_dir()
    assert oct(folder.stat().st_mode & 0o777) == oct(0o700)


def test_append_action_line_parses_via_the_actions_reader(tmp_path: Path):
    """Byte-compatible with the stage's own line: whatever writes it, the
    reader must parse it into the same `UserAction`."""
    append_action(tmp_path, "2026-11-24-today", "0123456789abcdef",
                  "queue:room", "tap", {"op": "done"})

    path = tmp_path / "2026-11-24-today" / ACTIONS_FILE
    raw = path.read_bytes().splitlines()[0]
    action = _parse(raw)
    assert (action.person, action.intent_id, action.kind, action.payload) == (
        "0123456789abcdef", "queue:room", "tap", {"op": "done"})


def test_append_action_appends_without_truncating(tmp_path: Path):
    append_action(tmp_path, "s", "p1", "queue:room", "tap", {"op": "request"})
    append_action(tmp_path, "s", "p2", "queue:room", "tap", {"op": "request"})

    lines = (tmp_path / "s" / ACTIONS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["person"] == "p1"
    assert json.loads(lines[1])["person"] == "p2"


def test_concurrent_appends_never_interleave_into_a_torn_line(tmp_path: Path):
    """The append happens under an exclusive `flock` as a single write: N
    threads appending at once must still produce N clean, parseable lines,
    never a line split or merged by a concurrent writer."""
    n = 40
    barrier = threading.Barrier(n)

    def write(i: int) -> None:
        barrier.wait()
        append_action(tmp_path, "s", f"p{i}", "queue:room", "tap", {"op": "request"})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "s" / ACTIONS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == n
    persons = {json.loads(line)["person"] for line in lines}
    assert persons == {f"p{i}" for i in range(n)}
