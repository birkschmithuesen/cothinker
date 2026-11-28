# CoThinker — shared/mcp_stdio.py: MCP stdio relay to the session host (ADR 0018)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A stdlib MCP stdio server that relays initialize/ping/tools/list/tools/call
over newline-delimited JSON-RPC 2.0 to shared.tool_api on the session host.

The started block instances live in the session host; the agent may run
elsewhere and passes a stdio server only its configured environment
variables, so this process carries no state of its own beyond the HTTP
connection details -- it relays every call for `--scope` using the bearer
token from COTHINKER_TOOL_TOKEN.

    python3 -m shared.mcp_stdio --url <host> --scope <id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

TOKEN_ENV = "COTHINKER_TOOL_TOKEN"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_NAME = "cothinker-mcp-stdio-relay"
SERVER_VERSION = "0.1.0"

_PARSE_ERROR = -32700
_METHOD_NOT_FOUND = -32601
_RELAY_ERROR = -32000


class RelayError(Exception):
    """The session host could not be reached or answered with an error."""


class Relay:
    def __init__(self, base_url: str, scope: str, token: str, timeout_s: float = 120,
                 opener: Callable = urllib.request.urlopen) -> None:
        self.base_url = base_url.rstrip("/")
        self.scope = urllib.parse.quote(scope, safe="")
        self.token = token
        self.timeout_s = timeout_s
        self._opener = opener

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
        )
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RelayError(str(e)) from e

    def list_tools(self) -> list[dict]:
        body = self._request("GET", f"/scopes/{self.scope}/tools")
        return body["tools"]

    def call(self, name: str, arguments: dict) -> dict:
        quoted = urllib.parse.quote(str(name), safe="")
        return self._request("POST", f"/scopes/{self.scope}/tools/{quoted}",
                              {"arguments": arguments})


def _result(msg_id, value: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": value}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict, relay: Relay) -> dict | None:
    """Handles one JSON-RPC request. Returns None for a notification (a
    message without an "id")."""
    is_notification = "id" not in msg
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        value = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else _result(msg_id, value)

    if method == "ping":
        return None if is_notification else _result(msg_id, {})

    if method == "tools/list":
        try:
            tools = relay.list_tools()
        except RelayError as e:
            return None if is_notification else _error(msg_id, _RELAY_ERROR, str(e))
        return None if is_notification else _result(msg_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            envelope = relay.call(name, arguments)
            is_error = not envelope.get("ok", True)
            text = json.dumps(envelope)
        except RelayError as e:
            is_error = True
            text = f"relay unreachable: {e}"
        value = {"content": [{"type": "text", "text": text}], "isError": is_error}
        return None if is_notification else _result(msg_id, value)

    return None if is_notification else _error(msg_id, _METHOD_NOT_FOUND,
                                                f"Method not found: {method}")


def serve(stdin, stdout, relay: Relay) -> None:
    """Reads newline-delimited JSON-RPC requests from `stdin`, writes replies
    to `stdout`. A malformed line is a parse error; the loop continues."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, _PARSE_ERROR, "Parse error")) + "\n")
            stdout.flush()
            continue
        reply = handle(msg, relay)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m shared.mcp_stdio")
    parser.add_argument("--url", required=True, help="session host base URL")
    parser.add_argument("--scope", required=True, help="group id or session id")
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV)
    if not token:
        print(f"{TOKEN_ENV} is not set", file=sys.stderr)
        return 2

    relay = Relay(args.url, args.scope, token)
    serve(sys.stdin, sys.stdout, relay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
