"""Agent API contract (ADR 0022 level 2; behaviour: docs/architecture/target.md §2.2).

A subject is a callable `make(scenario)` returning a context manager that
yields an `AgentPort` whose next `send_turn` behaves as the scenario says:

- "reply":          a plain answer, at least one `text_delta`, ends `done`
- "tool":           one tool call (`tool_started`/`tool_finished`), ends `done`
- "failed":         the backend reports the run failed, ends `failed`
- "failure_marker": the backend reports success, but the reply text carries
                    one of `FAILURE_MARKERS`; the adapter must end `failed`
- "cancelled":      the backend reports the run cancelled, ends `failed`

Every assertion message starts with a rule id (AGENT-...), so a red run
names the rule that broke. Outside adapters: `COTHINKER_CONTRACT_AGENT=
package.module:make` (docs/contract-tests.md).
"""
from __future__ import annotations

import contextlib
import http.server
import json
import pkgutil
import threading

import pytest

from shared.agent import (EVENT_KINDS, FAILURE_MARKERS, AgentPort, Event, FakeAgentPort,
                          HermesApiAgentPort)

CONTRACT_API = "agent"
SCENARIOS = ("reply", "tool", "failed", "failure_marker", "cancelled")
TURN = "contract turn"
CONTEXT_ID = "contract-ctx"
TERMINAL = frozenset({"done", "failed"})
PAIRS = {"tool_started": "tool_finished", "subagent_started": "subagent_finished"}
CLOSERS = {finish: start for start, finish in PAIRS.items()}
EXPECTED_END = {"reply": "done", "tool": "done", "failed": "failed",
                "failure_marker": "failed", "cancelled": "failed"}


# ------------------------------------------------------------------ the contract
def check_pairs(events) -> None:
    open_pairs: list[tuple[str, str]] = []
    for e in events:
        if e.kind in PAIRS:
            open_pairs.append((e.kind, e.tool_name))
        elif e.kind in CLOSERS:
            match = (CLOSERS[e.kind], e.tool_name)
            assert match in open_pairs, (
                f"AGENT-PAIR: {e.kind} {e.tool_name!r} without a matching {CLOSERS[e.kind]}")
            open_pairs.remove(match)
    if events and events[-1].kind == "done":
        assert not open_pairs, f"AGENT-PAIR: done with unfinished {open_pairs}"


def check_turn(events, scenario=None) -> None:
    """The order rules hold for every turn; `scenario` adds its outcome rule."""
    kinds = [e.kind for e in events]
    unknown = [k for k in kinds if k not in EVENT_KINDS]
    assert not unknown, f"AGENT-KIND: unknown event kinds {unknown}"
    terminals = [i for i, k in enumerate(kinds) if k in TERMINAL]
    assert len(terminals) == 1, f"AGENT-TERMINAL: expected exactly one done|failed, got {kinds}"
    assert terminals[0] == len(kinds) - 1, f"AGENT-LAST: events after the terminal one: {kinds}"
    check_pairs(events)
    reply = "".join(e.text for e in events if e.kind == "text_delta")
    if any(marker in reply for marker in FAILURE_MARKERS):
        assert kinds[-1] == "failed", f"AGENT-MARKER: reply carries a failure marker but ends {kinds[-1]}"
    if scenario is None:
        return
    assert kinds[-1] == EXPECTED_END[scenario], (
        f"AGENT-OUTCOME: scenario {scenario!r} must end {EXPECTED_END[scenario]}, got {kinds}")
    if scenario == "reply":
        assert "text_delta" in kinds, f"AGENT-REPLY: a reply without text_delta: {kinds}"
    if scenario == "tool":
        assert "tool_started" in kinds, f"AGENT-TOOL: a tool scenario without tool_started: {kinds}"


# ------------------------------------------------------------ built-in subjects
FAKE_SCRIPTS = {
    "tool": [Event(kind="tool_started", tool_name="search"),
             Event(kind="tool_finished", tool_name="search"),
             Event(kind="text_delta", text="gefunden"), Event(kind="done")],
    "failed": [Event(kind="text_delta", text="..."), Event(kind="failed", reason="run.failed")],
    "failure_marker": [Event(kind="text_delta", text="API call failed: HTTP 404"),
                       Event(kind="done")],
    "cancelled": [Event(kind="text_delta", text="..."),
                  Event(kind="failed", reason="run.cancelled")],
}


@contextlib.contextmanager
def fake_port(scenario):
    port = FakeAgentPort()
    if scenario in FAKE_SCRIPTS:
        port.script(TURN, FAKE_SCRIPTS[scenario])
    yield port


def _sse(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


HERMES_STREAMS = {
    "reply": _sse(("assistant.delta", {"delta": "Hallo"}), ("run.completed", {"usage": {}})),
    "tool": _sse(("tool.started", {"tool_name": "search", "preview": "suchen"}),
                 ("tool.completed", {"tool_name": "search", "preview": "fertig"}),
                 ("assistant.delta", {"delta": "gefunden"}), ("run.completed", {"usage": {}})),
    "failed": _sse(("assistant.delta", {"delta": "..."}), ("run.failed", {"usage": {}})),
    "failure_marker": _sse(("assistant.delta", {"delta": "API call failed: HTTP 404"}),
                           ("run.completed", {"usage": {}})),
    "cancelled": _sse(("assistant.delta", {"delta": "..."}), ("run.cancelled", {"usage": {}})),
}


def _hermes_handler(stream: bytes):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, ctype: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            if self.path == "/api/sessions":
                self._send(201, "application/json", b'{"session": {"id": "s1"}}')
            elif self.path.endswith("/chat/stream"):
                self._send(200, "text/event-stream", stream)
            else:
                self._send(404, "text/plain", b"")

        def log_message(self, *args) -> None:
            pass

    return Handler


@contextlib.contextmanager
def hermes_port(scenario):
    """`HermesApiAgentPort` against a fake Hermes API server on 127.0.0.1."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _hermes_handler(HERMES_STREAMS[scenario]))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield HermesApiAgentPort(f"http://127.0.0.1:{server.server_address[1]}", "k",
                                 retry_wait_s=0.01)
    finally:
        server.shutdown()
        server.server_close()


BUILTIN_SUBJECTS = {"fake": fake_port, "hermes-api": hermes_port}


def resolve_outside(value: str):
    return pkgutil.resolve_name(value)


# ---------------------------------------------------------------- contract tests
def test_the_port_has_the_agent_port_shape(subject):
    with subject("reply") as port:
        assert isinstance(port, AgentPort), "AGENT-SHAPE: not an AgentPort"
        session = port.open_session(CONTEXT_ID, "")
        assert session.context_id == CONTEXT_ID, "AGENT-SESSION: context_id not handed back"
        port.close(session)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_turn_keeps_the_event_contract(subject, scenario):
    with subject(scenario) as port:
        session = port.open_session(CONTEXT_ID, "")
        events = list(port.send_turn(session, TURN))
        port.close(session)
    check_turn(events, scenario)


def test_stop_is_safe_idle_mid_turn_and_twice(subject):
    with subject("reply") as port:
        session = port.open_session(CONTEXT_ID, "")
        port.stop(session)
        stream = iter(port.send_turn(session, TURN))
        first = next(stream)
        port.stop(session)
        port.stop(session)
        events = [first, *stream]
        port.close(session)
    check_turn(events)


# ------------------------------------------------- red proof: the broken fake
class TwoTerminalsAgentPort(FakeAgentPort):
    """Breaks exactly one rule, AGENT-TERMINAL: every turn ends `done`, `done`."""

    def send_turn(self, session, text, attachments=None, model=None):
        yield Event(kind="text_delta", text=text)
        yield Event(kind="done")
        yield Event(kind="done")


@contextlib.contextmanager
def broken_port(scenario):
    yield TwoTerminalsAgentPort()


def test_the_broken_fake_fails_the_contract_for_the_terminal_rule():
    with pytest.raises(AssertionError, match=r"^AGENT-TERMINAL:"):
        test_a_turn_keeps_the_event_contract(broken_port, "reply")


class _Ev:
    """An event an outside adapter might emit; `Event` itself refuses unknown kinds."""

    def __init__(self, kind, text="", tool_name=""):
        self.kind, self.text, self.tool_name = kind, text, tool_name


VIOLATIONS = {
    "unknown-kind": ("AGENT-KIND", [_Ev("chunk"), _Ev("done")], None),
    "two-terminals": ("AGENT-TERMINAL", [_Ev("text_delta", "a"), _Ev("done"), _Ev("failed")], None),
    "no-terminal": ("AGENT-TERMINAL", [_Ev("text_delta", "a")], None),
    "event-after-terminal": ("AGENT-LAST", [_Ev("done"), _Ev("text_delta", "a")], None),
    "finish-without-start": ("AGENT-PAIR", [_Ev("tool_finished", tool_name="x"), _Ev("done")], None),
    "start-open-at-done": ("AGENT-PAIR", [_Ev("tool_started", tool_name="x"), _Ev("done")], None),
    "pair-name-mismatch": ("AGENT-PAIR", [_Ev("tool_started", tool_name="x"),
                                          _Ev("tool_finished", tool_name="y"), _Ev("done")], None),
    "subagent-name-mismatch": ("AGENT-PAIR", [_Ev("subagent_started", tool_name="x"),
                                              _Ev("subagent_finished", tool_name="y"), _Ev("done")], None),
    "marker-ends-done": ("AGENT-MARKER", [_Ev("text_delta", "API call failed"), _Ev("done")], None),
    "wrong-outcome": ("AGENT-OUTCOME", [_Ev("text_delta", "a"), _Ev("done")], "failed"),
    "reply-without-text": ("AGENT-REPLY", [_Ev("done")], "reply"),
    "tool-without-tool": ("AGENT-TOOL", [_Ev("text_delta", "a"), _Ev("done")], "tool"),
}


@pytest.mark.parametrize("case", sorted(VIOLATIONS))
def test_every_rule_rejects_its_violation(case):
    rule, events, scenario = VIOLATIONS[case]
    with pytest.raises(AssertionError, match=rf"^{rule}:"):
        check_turn(events, scenario)
