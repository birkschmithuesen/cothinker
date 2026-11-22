# CoThinker — shared/blocks.py: block tool specs and the tool-calling host
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_block_tools.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared import blocks as sb
from shared.modules import CoreEvent, StateStore


class Echo:
    """Minimal block: turns every utterance into one block_output, logs event kinds."""
    api_version = sb.BLOCK_API_VERSION

    def __init__(self, id_):
        self.id = id_
        self.seen = []

    def start(self, ctx, config):
        pass

    def on_event(self, ev):
        self.seen.append(ev.kind)
        if ev.kind == "utterance":
            return [CoreEvent("block_output", {"from": self.id, "text": ev.payload["text"]})]
        return []

    def stop(self):
        pass


class Capture(Echo):
    """Records the full event, not just its kind; never fans out further."""

    def __init__(self, id_):
        super().__init__(id_)
        self.events = []

    def on_event(self, ev):
        self.events.append(ev)
        return []


class Toolable(Echo):
    """A block that declares one tool, bound to its own started instance."""

    def __init__(self, id_, *, tool_name="set_value", raise_on_call=False, read_only=False):
        super().__init__(id_)
        self.tool_name = tool_name
        self.raise_on_call = raise_on_call
        self.read_only = read_only
        self.calls = []

    def set_value(self, value):
        self.calls.append(value)
        if self.raise_on_call:
            raise RuntimeError("tool boom")
        return {"ok": True, "data": {"value": value}, "warnings": [], "rev": 7}

    def tools(self):
        return [sb.ToolSpec(
            name=self.tool_name,
            description="sets a value",
            parameters={"type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"]},
            fn=self.set_value,
            read_only=self.read_only,
        )]


def ctx_for(tmp):
    def make(block_id):
        return sb.BlockContext(instance=None, session_dir=Path(tmp),
                               state=StateStore(Path(tmp) / block_id), agent=None, llm=None,
                               language="de", clock=lambda: 0.0,
                               emit=lambda intent: None, artefact=lambda name, text: None)
    return make


def test_toolspec_read_only_defaults_to_false():
    spec = sb.ToolSpec(name="x", description="d", parameters={}, fn=lambda: {})
    assert spec.read_only is False


def test_tools_of_started_blocks_are_listed(tmp_path):
    a, b = Toolable("a"), Echo("b")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    listed = host.tools()
    assert [(block_id, spec.name) for block_id, spec in listed] == [("a", "set_value")]


def test_block_without_tools_method_is_fine(tmp_path):
    b = Echo("b")
    host = sb.BlockHost([b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    assert host.tools() == []


def test_duplicate_tool_name_fails_at_start(tmp_path):
    a = Toolable("a", tool_name="set_value")
    b = Toolable("b", tool_name="set_value")
    host = sb.BlockHost([a, b], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    with pytest.raises(sb.BlockError):
        host.start({})


def test_bad_tool_name_fails_at_start(tmp_path):
    a = Toolable("a", tool_name="Set Value!")
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    with pytest.raises(sb.BlockError):
        host.start({})


def test_call_reaches_bound_instance(tmp_path):
    a = Toolable("a")
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    result = host.call_tool("set_value", {"value": 5})
    assert a.calls == [5]
    assert result["ok"] is True


def test_unknown_tool_returns_error_envelope(tmp_path):
    host = sb.BlockHost([Echo("a")], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    result = host.call_tool("missing", {})
    assert result == {"ok": False, "data": {}, "warnings": [], "rev": None,
                       "error": "unknown_tool: missing"}


def test_unknown_argument_is_refused(tmp_path):
    a = Toolable("a")
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    result = host.call_tool("set_value", {"value": 1, "extra": 2})
    assert result["ok"] is False
    assert "extra" in result["error"]
    assert a.calls == []


def test_missing_required_argument_is_refused(tmp_path):
    a = Toolable("a")
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    result = host.call_tool("set_value", {})
    assert result["ok"] is False
    assert "value" in result["error"]
    assert a.calls == []


def test_raising_tool_returns_tool_failed_and_logs(tmp_path):
    a = Toolable("a", raise_on_call=True)
    logs = []
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=logs.append)
    host.start({})
    result = host.call_tool("set_value", {"value": 1})
    assert result == {"ok": False, "data": {}, "warnings": [], "rev": None,
                       "error": "tool_failed: RuntimeError"}
    assert any("a" in line and "RuntimeError" in line for line in logs)


def test_write_tool_dispatches_block_output(tmp_path):
    a = Toolable("a", read_only=False)
    cap = Capture("cap")
    host = sb.BlockHost([a, cap], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    host.call_tool("set_value", {"value": 3})
    outputs = [ev for ev in cap.events if ev.kind == "block_output"]
    assert len(outputs) == 1
    assert outputs[0].payload == {"from": "a", "tool_call": {"tool": "set_value", "rev": 7}}


def test_read_tool_does_not_dispatch_and_logs_result(tmp_path):
    a = Toolable("a", read_only=True)
    cap = Capture("cap")
    host = sb.BlockHost([a, cap], ctx_for=ctx_for(tmp_path), log=lambda line: None)
    host.start({})
    host.call_tool("set_value", {"value": 3})
    assert [ev for ev in cap.events if ev.kind == "block_output"] == []
    calls = host.tool_calls_since(0.0)
    assert len(calls) == 1
    assert calls[0]["tool"] == "set_value"
    assert calls[0]["block"] == "a"
    assert calls[0]["ok"] is True
    assert calls[0]["result"]["data"] == {"value": 3}


def test_tool_calls_since_filters_by_time(tmp_path):
    a = Toolable("a")
    times = iter([1.0, 5.0])
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=lambda line: None,
                         clock=lambda: next(times))
    host.start({})
    host.call_tool("set_value", {"value": 1})
    host.call_tool("set_value", {"value": 2})
    calls = host.tool_calls_since(3.0)
    assert len(calls) == 1
    assert calls[0]["at"] == 5.0


def test_observer_sees_every_routed_event(tmp_path):
    observed = []
    a = Echo("a")
    host = sb.BlockHost([a], ctx_for=ctx_for(tmp_path), log=lambda line: None,
                         observe=observed.append)
    host.start({})
    host.dispatch(CoreEvent("utterance", {"text": "hi"}))
    kinds = [ev.kind for ev in observed]
    assert kinds.count("utterance") == 1
    assert kinds.count("block_output") == 1
