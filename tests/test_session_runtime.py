# CoThinker — tests for shared/session_runtime.py: one runtime per session
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_session_runtime.py"""
from __future__ import annotations

import textwrap

import pytest

from shared.blocks import BlockError
from shared.modules import CoreEvent, ToolSpec
from shared.session_runtime import RuntimeConfig, SessionRuntime
from shared.surface import UserAction, ViewIntent
from shared.templates import load_template

HEAD = """
[template]
name = "t"
api_version = 1
rules = ["grounding", "add-dont-restructure"]
"""
TEMPLATE = HEAD + """
[[blocks]]
id = "a"
[[blocks]]
id = "b"
state_scope = "group"
[[phases]]
id = "one"
titles = { en = "One" }
blocks = ["a"]
[[phases]]
id = "two"
titles = { en = "Two" }
blocks = ["a", "b"]
"""
ONE_PHASE = HEAD + """
[[blocks]]
id = "a"
[[blocks]]
id = "ghost"
[[phases]]
id = "session"
blocks = ["a", "ghost"]
"""


class Rec:
    api_version = 1

    def __init__(self, id_):
        self.id, self.events = id_, []

    def start(self, ctx, config):
        self.ctx = ctx

    def on_event(self, ev):
        self.events.append(ev)
        if ev.kind == "tick":
            self.ctx.emit(ViewIntent(id=f"s:{self.id}", block=self.id, kind="status",
                                     audience="room", payload={"n": len(self.events)}))
            return [CoreEvent("block_output", {"from": self.id, "ticked": {"n": len(self.events)}})]
        return ()

    def stop(self):
        pass

    def tools(self):
        if self.id != "b":
            return []

        def remember(text):
            self.ctx.state.update("m.json", lambda d: {"text": text})
            return {"ok": True, "data": {}, "warnings": [], "rev": 1, "error": None}
        return [ToolSpec("remember", "store", {"type": "object", "properties": {"text": {"type": "string"}},
                                                "required": ["text"]}, remember)]


def runtime(tmp_path, template_text=TEMPLATE, session="s1", surfaces=(), logs=None, **cfg):
    p = tmp_path / "template.toml"
    p.write_text(textwrap.dedent(template_text), encoding="utf-8")
    made = {}

    def load(block_id):
        if block_id == "ghost":
            raise BlockError(f"no block registered for {block_id!r}")
        made[block_id] = Rec(block_id)
        return made[block_id]
    log = (logs.append if logs is not None else (lambda m: None))
    rt = SessionRuntime(RuntimeConfig(template=load_template(p), session_dir=tmp_path / session,
                                      group_dir=tmp_path / "group", language="en",
                                      clock=lambda: 5.0, log=log, **cfg),
                        surfaces=surfaces, load=load)
    rt.start()
    return rt, made


def test_start_activates_the_first_phase_blocks_only(tmp_path):
    rt, made = runtime(tmp_path)
    rt.tick()
    assert [e.kind for e in made["a"].events][:2] == ["phase", "tick"]
    assert made["b"].events == []


def test_advance_switches_active_blocks_and_records_history(tmp_path):
    rt, made = runtime(tmp_path)
    assert rt.advance("two", "operator") is True
    assert dict(made["b"].events[0].payload) == {"from": "one", "to": "two", "cause": "operator"}
    assert (tmp_path / "s1" / ".blocks" / "phase-history.jsonl").read_text().count("\n") == 1


def test_back_returns_to_previous_phase_and_records_it(tmp_path):
    rt, _ = runtime(tmp_path)
    rt.advance("two", "operator")
    assert rt.back() is True and rt.phases.current().id == "one"
    assert [c.cause for c in rt.phases.history()] == ["operator", "back"]


def test_restart_resumes_the_phase_from_history(tmp_path):
    rt, _ = runtime(tmp_path)
    rt.advance("two", "operator")
    rt2, _ = runtime(tmp_path)
    assert rt2.phases.current().id == "two"


def test_phase_bar_intent_after_start_and_change(tmp_path):
    rt, _ = runtime(tmp_path)
    bar = {i.id: i for i in rt.intents.current()}["phase:bar"]
    assert bar.payload["current"] == "one"
    rt.advance("two", "operator")
    assert {i.id: i for i in rt.intents.current()}["phase:bar"].payload["current"] == "two"


def test_single_phase_template_puts_no_phase_bar_and_no_phase_tools(tmp_path):
    rt, made = runtime(tmp_path, ONE_PHASE)
    assert "phase:bar" not in {i.id for i in rt.intents.current()}
    assert {s.name for s in rt.tools()} == set()
    assert not (tmp_path / "s1" / ".blocks" / "phase-history.jsonl").exists()
    assert made["a"].events == []      # no phase event either: the meeting sees nothing new


def test_unknown_block_skipped_with_the_host_log_line(tmp_path):
    logs = []
    rt, _ = runtime(tmp_path, ONE_PHASE, logs=logs)
    assert rt.missing_blocks == ("ghost",)
    assert logs.count("block host: no block registered for 'ghost'") == 1


def test_group_scoped_state_survives_a_new_session_dir(tmp_path):
    rt, _ = runtime(tmp_path, session="day1")
    rt.call_tool("remember", {"text": "kept"})
    rt2, made2 = runtime(tmp_path, session="day2")
    assert made2["b"].ctx.state.read("m.json") == {"text": "kept"}


def test_session_scoped_state_lives_under_dot_blocks(tmp_path):
    rt, made = runtime(tmp_path, session="day1")
    made["a"].ctx.state.write("x.json", {"v": 1})
    assert (tmp_path / "day1" / ".blocks" / "a" / "x.json").exists()
    rt2, made2 = runtime(tmp_path, session="day2")
    assert made2["a"].ctx.state.read("x.json") == {}


def test_unknown_state_scope_fails_at_construction(tmp_path):
    with pytest.raises(BlockError, match="state_scope"):
        runtime(tmp_path, TEMPLATE.replace('state_scope = "group"', 'state_scope = "room"'))


def test_injected_artefact_writer_and_sink_are_used(tmp_path):
    written, sunk = [], []
    rt, made = runtime(tmp_path, sink=sunk.append,
                       artefact_for=lambda bid: (lambda n, t: written.append((bid, n, t))))
    made["a"].ctx.artefact("protokoll.md", "x")
    rt.tick()
    assert written == [("a", "protokoll.md", "x")] and "s:a" in [i.id for i in sunk]


def test_own_artefact_writer_writes_per_block_and_rejects_bad_names(tmp_path):
    rt, made = runtime(tmp_path)
    made["a"].ctx.artefact("script.en.md", "# Title\n")
    assert (tmp_path / "s1" / "artefacts" / "a" / "script.en.md").read_text() == "# Title\n"
    with pytest.raises(ValueError):
        made["a"].ctx.artefact("../escape.md", "x")
    with pytest.raises(ValueError):
        made["a"].ctx.artefact("..", "x")


def test_on_output_sees_block_output_with_from(tmp_path):
    rt, _ = runtime(tmp_path)
    seen = []
    rt.on_output(seen.append)
    rt.tick()
    assert seen and seen[0]["from"] == "a"
    assert [o["from"] for o in rt.collect("tick", {"at": 5.0}, "a")] == ["a"]
    assert rt.collect("tick", {"at": 5.0}, "zz") == []


def test_a_raising_listener_does_not_stop_the_others(tmp_path):
    logs, seen = [], []
    rt, _ = runtime(tmp_path, logs=logs)

    def broken(payload):
        raise RuntimeError("boom")
    rt.on_output(broken)
    rt.on_output(seen.append)
    rt.dispatch("tick", {"at": 5.0})
    assert seen and seen[0]["from"] == "a"
    assert "block host: output listener failed: RuntimeError: boom" in logs


def test_dispatch_never_raises_on_unknown_kind(tmp_path):
    rt, _ = runtime(tmp_path)
    assert rt.dispatch("no-such-kind", {}) == 0


def test_get_returns_started_block_or_none(tmp_path):
    rt, made = runtime(tmp_path)
    assert rt.get("a") is made["a"] and rt.get("zz") is None


def test_action_on_phase_bar_advances_with_person_cause(tmp_path):
    rt, _ = runtime(tmp_path)
    rt.act(UserAction(intent_id="phase:bar", kind="choose", person="person:g1", payload={"to": "two"}, at=5.0))
    assert rt.phases.current().id == "two" and rt.phases.history()[-1].cause == "person:g1"


def test_other_action_reaches_blocks_as_action_event(tmp_path):
    rt, made = runtime(tmp_path)
    rt.act(UserAction(intent_id="x", kind="tap", person="person:g1", payload={"k": 1}, at=5.0))
    ev = made["a"].events[-1]
    assert ev.kind == "action" and ev.payload["intent_id"] == "x" and ev.payload["kind"] == "tap"


def test_set_phase_tool_advances_with_cause_agent(tmp_path):
    rt, _ = runtime(tmp_path)
    assert rt.call_tool("set_phase", {"to": "two"})["ok"] is True
    assert rt.phases.history()[-1].cause == "agent"
    assert {s.name: s for s in rt.tools()}["phase_status"].read_only is True


def test_render_hands_each_surface_only_its_kinds(tmp_path):
    class S:
        name, kinds = "s", frozenset({"status"})

        def __init__(self):
            self.got = []

        def render(self, intents):
            self.got = sorted(i.id for i in intents)

        def actions(self):
            return iter(())
    s = S()
    rt, _ = runtime(tmp_path, surfaces=[s])
    rt.tick()
    rt.render()
    assert s.got == ["phase:bar", "s:a"]
