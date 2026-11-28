# CoThinker — tests for shared/mcp_stdio.py: MCP stdio relay (ADR 0018)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_mcp_stdio.py"""
from __future__ import annotations

import http.server
import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from shared.mcp_stdio import PROTOCOL_VERSIONS, Relay, handle, main, serve

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingOpener:
    """Stands in for urllib.request.urlopen: records every request, always
    answers with the given canned body."""

    def __init__(self, body: bytes):
        self.body = body
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self.body)


class FailingOpener:
    """Simulates an unreachable host."""

    def __call__(self, request, timeout=None):
        raise ConnectionRefusedError("no route to host")


def _relay(opener) -> Relay:
    return Relay("http://host", "g1", "tok", opener=opener)


def test_initialize_echoes_known_version_or_falls_back():
    relay = _relay(RecordingOpener(b"{}"))

    known = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-03-26"}}, relay)
    assert known["result"]["protocolVersion"] == "2025-03-26"

    unknown = handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                       "params": {"protocolVersion": "1999-01-01"}}, relay)
    assert unknown["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply():
    relay = _relay(RecordingOpener(b"{}"))
    reply = handle({"jsonrpc": "2.0", "method": "ping"}, relay)
    assert reply is None


def test_tools_list_relays_host_list():
    body = json.dumps({"tools": [{"name": "echo", "description": "d",
                                   "inputSchema": {"type": "object"},
                                   "annotations": {"readOnlyHint": True}}]}).encode()
    opener = RecordingOpener(body)
    relay = _relay(opener)

    reply = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, relay)

    assert reply["result"]["tools"][0]["name"] == "echo"
    sent = opener.requests[0]
    assert sent.full_url == "http://host/scopes/g1/tools"
    assert sent.headers["Authorization"] == "Bearer tok"


def test_tools_call_wraps_result_as_text_content_and_sets_is_error():
    ok_body = json.dumps({"ok": True, "data": {"x": 1}, "warnings": [],
                           "rev": 1, "error": None}).encode()
    relay = _relay(RecordingOpener(ok_body))
    reply = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {}}}, relay)
    assert reply["result"]["isError"] is False
    assert reply["result"]["content"][0]["type"] == "text"
    assert "x" in reply["result"]["content"][0]["text"]

    bad_body = json.dumps({"ok": False, "data": {}, "warnings": [],
                            "rev": None, "error": "bad_args: boom"}).encode()
    relay = _relay(RecordingOpener(bad_body))
    reply = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {}}}, relay)
    assert reply["result"]["isError"] is True
    assert "bad_args" in reply["result"]["content"][0]["text"]


def test_unreachable_host_becomes_tool_error_not_crash():
    relay = _relay(FailingOpener())
    reply = handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {}}}, relay)
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["type"] == "text"


def test_unknown_method_is_method_not_found():
    relay = _relay(RecordingOpener(b"{}"))
    reply = handle({"jsonrpc": "2.0", "id": 7, "method": "no/such"}, relay)
    assert reply["error"]["code"] == -32601


def test_bad_json_line_is_parse_error_and_loop_continues():
    relay = _relay(RecordingOpener(b"{}"))
    good = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "ping"})
    stdin = io.StringIO("not json at all\n" + good + "\n")
    stdout = io.StringIO()

    serve(stdin, stdout, relay)

    lines = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2
    assert lines[0]["error"]["code"] == -32700
    assert lines[0]["id"] is None
    assert lines[1]["id"] == 8
    assert lines[1]["result"] == {}


def test_missing_token_env_exits_2(monkeypatch):
    monkeypatch.delenv("COTHINKER_TOOL_TOKEN", raising=False)
    assert main(["--url", "http://host", "--scope", "g1"]) == 2


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        body = json.dumps({"tools": [{"name": "echo", "description": "d",
                                       "inputSchema": {"type": "object"},
                                       "annotations": {"readOnlyHint": True}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        self.server.seen.append(("POST", self.path, self.headers.get("Authorization"), json.loads(raw)))
        body = json.dumps({"ok": True, "data": {"echoed": True}, "warnings": [],
                            "rev": 1, "error": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def stub_host():
    server = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)


def test_end_to_end_subprocess_relays_to_stub_host(stub_host):
    port = stub_host.server_address[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "shared.mcp_stdio", "--url", f"http://127.0.0.1:{port}", "--scope", "g1"],
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={"PATH": "/usr/bin:/bin", "COTHINKER_TOOL_TOKEN": "tok123"},
    )
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        list_reply = json.loads(proc.stdout.readline())

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                      "params": {"name": "echo", "arguments": {"x": 1}}}) + "\n")
        proc.stdin.flush()
        call_reply = json.loads(proc.stdout.readline())
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)

    assert proc.returncode == 0
    assert list_reply["id"] == 1
    assert list_reply["result"]["tools"][0]["name"] == "echo"
    assert call_reply["id"] == 2
    assert call_reply["result"]["isError"] is False

    methods, paths, tokens, bodies = zip(*stub_host.seen)
    assert methods == ("GET", "POST")
    assert paths == ("/scopes/g1/tools", "/scopes/g1/tools/echo")
    assert tokens == ("Bearer tok123", "Bearer tok123")
    assert bodies[1] == {"arguments": {"x": 1}}
