# CoThinker — tests for gateway/actions_reader.py (issue #47)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_actions_reader.py"""
from __future__ import annotations

import json

from gateway.actions_reader import ActionsReader, dispatcher_for
from gateway.block_host_wiring import build_session_blocks
from shared.blocks import BLOCK_API_VERSION
from shared.surface import UserAction


def line(intent_id="queue:room", kind="tap", payload=None, person="0123456789abcdef", **over):
    d = {"v": 1, "at": "2026-09-08T19:12:04.123Z", "person": person,
         "intent_id": intent_id, "kind": kind,
         "payload": {"op": "request"} if payload is None else payload}
    d.update(over)
    return json.dumps(d) + "\n"


def make(tmp_path):
    got, logs = [], []
    path = tmp_path / "actions.jsonl"
    return path, got, logs, ActionsReader(path, got.append, logs.append)


def test_reads_from_offset(tmp_path):
    path, got, logs, r = make(tmp_path)
    assert r.poll() == 0                      # file does not exist yet
    path.write_text(line() + line("participant:name", "edit", {"name": "Testperson"}))
    assert r.poll() == 2
    assert r.poll() == 0                      # nothing is read twice
    with open(path, "a") as f:
        f.write(line(payload={"op": "done"}))
    assert r.poll() == 1
    assert [a.payload for a in got] == [{"op": "request"}, {"name": "Testperson"}, {"op": "done"}]
    assert isinstance(got[0], UserAction)
    assert (got[0].intent_id, got[0].kind, got[0].person) == (
        "queue:room", "tap", "0123456789abcdef")
    assert not logs


def test_partial_line_waits(tmp_path):
    path, got, logs, r = make(tmp_path)
    full = line()
    path.write_text(full[:20])
    assert r.poll() == 0
    with open(path, "a") as f:
        f.write(full[20:])
    assert r.poll() == 1
    assert not logs


def test_malformed_line_skipped_once_logged(tmp_path):
    path, got, logs, r = make(tmp_path)
    bad = ['not json Geheimname\n', line(v=2), line(kind="nope"),
           line(person=""), line(payload=[1]), '[1]\n']
    path.write_text("".join(bad) + line())
    assert r.poll() == 1
    assert len(logs) == len(bad)
    assert r.poll() == 0
    assert len(logs) == len(bad)              # not logged again
    assert all("Geheimname" not in l and str(path) in l for l in logs)


class Echo:
    """A block that traces exactly what it sees of an action event."""
    id = "echo"
    api_version = BLOCK_API_VERSION

    def __init__(self):
        self.ctx = None

    def start(self, ctx, config):
        self.ctx = ctx

    def on_event(self, ev):
        a = ev.payload["action"]
        self.ctx.trace("action", {"intent_id": a.intent_id, "kind": a.kind})
        return ()

    def stop(self):
        pass


def test_names_not_in_trace(tmp_path):
    traced, logs = [], []
    echo = Echo()
    from shared.templates import load_template
    from gateway import block_host_wiring as bhw
    blocks = build_session_blocks(
        session_dir=tmp_path, sink=lambda i: None, log=logs.append,
        trace=lambda e, f: traced.append((e, dict(f))),
        registry={ref.id: (lambda: echo)
                  for ref in load_template(bhw.REFLECT_TEMPLATE).blocks[:1]})
    path = tmp_path / "actions.jsonl"
    path.write_text(line("participant:name", "edit", {"name": "Testperson Eins"}))
    r = ActionsReader(path, dispatcher_for(blocks), logs.append)
    assert r.poll() == 1
    assert traced and traced[0][0] == "action"
    assert "Testperson" not in json.dumps(traced) + "".join(logs)
