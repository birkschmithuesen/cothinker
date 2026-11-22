#!/usr/bin/env python3
# CoThinker — agent seam: fake adapter, Hermes API adapter, no-leak grep
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The agent seam (docs/architecture.md §1).

    python3 -m pytest -q tests/test_agent_port.py
"""
from __future__ import annotations

import http.server
import json
import subprocess
import threading
from pathlib import Path

import pytest

from gateway.worker import FAILURE_MARKERS as WORKER_FAILURE_MARKERS
from shared.agent import AgentPort, Event, FakeAgentPort, FAILURE_MARKERS, HermesApiAgentPort

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Event
# --------------------------------------------------------------------------

def test_a_known_kind_constructs():
    assert Event(kind="done").kind == "done"


def test_an_unknown_kind_raises_valueerror():
    with pytest.raises(ValueError):
        Event(kind="bogus")


# --------------------------------------------------------------------------
# FakeAgentPort: open_session / default echo / scripted events / stop+close
# --------------------------------------------------------------------------

def test_fake_agent_port_satisfies_the_agent_port_shape():
    assert isinstance(FakeAgentPort(), AgentPort)


def test_open_session_returns_the_context_id_back():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "Du bist Assistant.")
    assert session.context_id == "tisch-01"


def test_open_session_is_recorded():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "Du bist Assistant.")
    assert session in fake.opened


def test_default_reply_is_text_delta_then_done():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    events = list(fake.send_turn(session, "hallo"))
    assert [e.kind for e in events] == ["text_delta", "done"]


def test_the_text_delta_carries_the_input_text():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    events = list(fake.send_turn(session, "hallo"))
    assert events[0].text == "hallo"


def test_a_scripted_subagent_round_trip_plays_back_in_order():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    fake.script("schreib die szene", [
        Event(kind="subagent_started", tool_name="delegate_task", label="Szene schreiben"),
        Event(kind="subagent_finished", tool_name="delegate_task", label="Szene fertig"),
        Event(kind="done", usage={"input_tokens": 3}),
    ])
    scripted = list(fake.send_turn(session, "schreib die szene"))
    assert [e.kind for e in scripted] == [
        "subagent_started", "subagent_finished", "done"]


def test_done_carries_the_scripted_usage():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    fake.script("schreib die szene", [
        Event(kind="subagent_started", tool_name="delegate_task", label="Szene schreiben"),
        Event(kind="subagent_finished", tool_name="delegate_task", label="Szene fertig"),
        Event(kind="done", usage={"input_tokens": 3}),
    ])
    scripted = list(fake.send_turn(session, "schreib die szene"))
    assert scripted[-1].usage == {"input_tokens": 3}


def test_a_scripted_reply_is_consumed_not_replayed():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    fake.script("schreib die szene", [Event(kind="done")])
    list(fake.send_turn(session, "schreib die szene"))
    # a script is consumed once -- the next turn with the same text falls
    # back to the default echo
    replay = list(fake.send_turn(session, "schreib die szene"))
    assert [e.kind for e in replay] == ["text_delta", "done"]


def test_stop_is_recorded():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    fake.stop(session)
    assert session in fake.stopped


def test_close_is_recorded():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-01", "")
    fake.close(session)
    assert session in fake.closed


# --------------------------------------------------------------------------
# no Hermes call outside the one adapter module (shared/agent.py)
# --------------------------------------------------------------------------

@pytest.fixture
def no_leak_grep() -> subprocess.CompletedProcess:
    # Bare "urllib.request"/"urllib.error" would also catch any other
    # stdlib HTTP client that has nothing to do with Hermes. Match Hermes
    # API specifics instead: the endpoint path and the auth header it
    # sends. "import requests" (not bare "requests") so unrelated prose
    # disclaiming a dependency doesn't trip the guard.
    forbidden = ("import requests", "/api/sessions", "Authorization: Bearer")
    args = ["git", "-C", str(ROOT), "grep", "--untracked", "-n", "-F"]
    for pat in forbidden:
        args += ["-e", pat]
    args += ["--", "gateway/*.py", "blocks", "surfaces"]
    return subprocess.run(args, capture_output=True, text=True)


def test_the_no_leak_grep_ran(no_leak_grep):
    # 0 = matches found, 1 = none -- both are a completed run, not an error.
    assert no_leak_grep.returncode in (0, 1), no_leak_grep.stderr


def test_no_forbidden_hermes_pattern_outside_the_one_adapter_module(no_leak_grep):
    assert no_leak_grep.stdout.splitlines() == []


# --------------------------------------------------------------------------
# HermesApiAgentPort against a fake Hermes API server
# --------------------------------------------------------------------------

SSE_NORMAL = (
    ": keepalive\n\n"
    "event: assistant.delta\n"
    'data: {"delta": "Hallo"}\n\n'
    "event: tool.started\n"
    'data: {"tool_name": "search", "preview": "nachschlagen"}\n\n'
    "event: tool.completed\n"
    'data: {"tool_name": "search", "preview": "fertig"}\n\n'
    "event: tool.started\n"
    'data: {"tool_name": "delegate_task", "preview": "Szene entwerfen"}\n\n'
    "event: tool.completed\n"
    'data: {"tool_name": "delegate_task", "preview": "Entwurf fertig"}\n\n'
    "event: run.completed\n"
    'data: {"usage": {"input_tokens": 12}}\n\n'
).encode("utf-8")

SSE_FAILED = (
    "event: assistant.delta\n"
    'data: {"delta": "..."}\n\n'
    "event: run.failed\n"
    'data: {"usage": {}}\n\n'
).encode("utf-8")


def _sse_with_text(text: str) -> bytes:
    return (
        "event: assistant.delta\n"
        f'data: {json.dumps({"delta": text})}\n\n'
        "event: run.completed\n"
        'data: {"usage": {"input_tokens": 1}}\n\n'
    ).encode("utf-8")


class _BaseHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_429(self) -> None:
        self._send_json(429, {"error": "too many requests"})

    def do_POST(self) -> None:          # noqa: N802 - stdlib handler name
        self._read_body()
        if self.path == "/api/sessions":
            self._send_json(201, {"object": "hermes.session", "session": {"id": "s1"}})
            return
        if self.path.endswith("/chat/stream"):
            self._handle_chat_stream()
            return
        self.send_response(404)
        self.end_headers()

    def _handle_chat_stream(self) -> None:
        raise NotImplementedError

    def log_message(self, *a) -> None:  # noqa: D102 - silence stdlib logging
        pass


class _NormalHandler(_BaseHandler):
    def _handle_chat_stream(self) -> None:
        self._send_sse(SSE_NORMAL)


class _FailHandler(_BaseHandler):
    def _handle_chat_stream(self) -> None:
        self._send_sse(SSE_FAILED)


class _FlakyHandler(_BaseHandler):
    attempts = 0

    def _handle_chat_stream(self) -> None:
        type(self).attempts += 1
        if type(self).attempts == 1:
            self._send_429()
            return
        self._send_sse(SSE_NORMAL)


class _Always429Handler(_BaseHandler):
    def _handle_chat_stream(self) -> None:
        self._send_429()


class _ModelEchoHandler(_BaseHandler):
    last_body: dict = {}

    def do_POST(self) -> None:          # noqa: N802 - stdlib handler name
        body = self._read_body()
        if self.path.endswith("/chat/stream"):
            type(self).last_body = json.loads(body.decode("utf-8")) if body else {}
            self._send_sse(SSE_NORMAL)
            return
        if self.path == "/api/sessions":
            self._send_json(201, {"object": "hermes.session", "session": {"id": "s1"}})
            return
        self.send_response(404)
        self.end_headers()


class _TextHandler(_BaseHandler):
    """Replies with whatever text the request body carries as `input`."""

    def do_POST(self) -> None:          # noqa: N802 - stdlib handler name
        body = self._read_body()
        if self.path == "/api/sessions":
            self._send_json(201, {"object": "hermes.session", "session": {"id": "s1"}})
            return
        if self.path.endswith("/chat/stream"):
            payload = json.loads(body.decode("utf-8")) if body else {}
            self._send_sse(_sse_with_text(str(payload.get("input") or "")))
            return
        self.send_response(404)
        self.end_headers()


def _start(handler_cls) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def server_factory():
    servers: list[http.server.ThreadingHTTPServer] = []

    def start(handler_cls) -> tuple[http.server.ThreadingHTTPServer, int]:
        server = _start(handler_cls)
        servers.append(server)
        return server, server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()


@pytest.fixture
def normal_turn(server_factory):
    server, port = server_factory(_NormalHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-02", "")
    events = list(adapter.send_turn(session, "Erzähl von deinem Tag."))
    yield adapter, session, events
    adapter.stop(session)
    adapter.close(session)


def test_open_session_reads_session_id_as_the_handle(normal_turn):
    _, session, _ = normal_turn
    assert session.handle == "s1"


def test_event_kinds_follow_run_completed_tool_star_and_the_delegate_task_mapping(normal_turn):
    _, _, events = normal_turn
    assert [e.kind for e in events] == [
        "text_delta", "tool_started", "tool_finished",
        "subagent_started", "subagent_finished", "done",
    ]


def test_the_subagent_pair_carries_the_delegate_task_tool_name(normal_turn):
    _, _, events = normal_turn
    assert events[3].tool_name == "delegate_task"
    assert events[4].tool_name == "delegate_task"


def test_done_carries_the_runs_usage(normal_turn):
    _, _, events = normal_turn
    assert events[-1].usage == {"input_tokens": 12}


def test_hermes_api_agent_port_maps_run_failed_to_a_failed_event(server_factory):
    server, port = server_factory(_FailHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-03", "")
    events = list(adapter.send_turn(session, "x"))
    assert events[-1].kind == "failed"
    assert events[-1].reason == "run.failed"


@pytest.fixture
def flaky_turn(server_factory):
    _FlakyHandler.attempts = 0
    server, port = server_factory(_FlakyHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-04", "")
    return list(adapter.send_turn(session, "x"))


def test_a_single_http_429_is_retried_once_then_succeeds(flaky_turn):
    assert flaky_turn[-1].kind == "done"


def test_the_retry_after_a_single_429_reaches_the_handler_twice(flaky_turn):
    assert _FlakyHandler.attempts == 2


def test_http_429_twice_in_a_row_ends_the_turn_with_a_failed_event_not_an_exception(server_factory):
    server, port = server_factory(_Always429Handler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-05", "")
    events = list(adapter.send_turn(session, "x"))
    assert [e.kind for e in events] == ["failed"]
    assert events[0].reason == "http_429"


def test_hermes_api_agent_port_satisfies_the_agent_port_shape(server_factory):
    server, port = server_factory(_NormalHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    assert isinstance(adapter, AgentPort)


def test_send_turn_model_puts_model_in_the_chat_stream_request_body(server_factory):
    server, port = server_factory(_ModelEchoHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-06", "")
    list(adapter.send_turn(session, "x", model="claude-opus-5-5"))
    assert _ModelEchoHandler.last_body.get("model") == "claude-opus-5-5"


def test_send_turn_without_model_sends_no_model_key_at_all(server_factory):
    server, port = server_factory(_ModelEchoHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-06", "")
    _ModelEchoHandler.last_body = {}
    list(adapter.send_turn(session, "x"))
    assert "model" not in _ModelEchoHandler.last_body


def test_fake_agent_port_send_turn_accepts_model_too():
    fake = FakeAgentPort()
    session = fake.open_session("tisch-07", "")
    events = list(fake.send_turn(session, "hallo", model="claude-opus-5-5"))
    assert [e.kind for e in events] == ["text_delta", "done"]


# --------------------------------------------------------------------------
# New for this card: failure markers in the Hermes API adapter's reply text
# --------------------------------------------------------------------------

@pytest.mark.parametrize("marker", FAILURE_MARKERS)
def test_failure_marker_in_reply_ends_turn_failed(server_factory, marker):
    server, port = server_factory(_TextHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-08", "")
    events = list(adapter.send_turn(session, f"irrelevant, the reply text carries: {marker}"))
    assert events[-1].kind == "failed"
    assert events[-1].reason == "failure_marker"


def test_reply_without_marker_ends_done(server_factory):
    server, port = server_factory(_TextHandler)
    adapter = HermesApiAgentPort(f"http://127.0.0.1:{port}", "k", retry_wait_s=0.01)
    session = adapter.open_session("tisch-09", "")
    events = list(adapter.send_turn(session, "a perfectly normal reply"))
    assert events[-1].kind == "done"


def test_worker_uses_the_shared_failure_markers():
    assert WORKER_FAILURE_MARKERS is FAILURE_MARKERS
