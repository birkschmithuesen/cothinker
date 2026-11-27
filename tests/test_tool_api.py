# CoThinker — tests for shared/tool_api.py: session host tool endpoint
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_tool_api.py"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.modules import ToolSpec
from shared.tool_api import ScopeRegistry, describe, tool_router


class FakeRuntime:
    """A minimal stand-in for SessionRuntime: tools() + call_tool(), with its
    own state so a test can prove two scopes never share it."""

    def __init__(self, label: str):
        self.label = label
        self.calls: list[tuple[str, dict]] = []

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="echo",
                description="echoes the label",
                parameters={"type": "object", "properties": {}},
                fn=lambda: {"ok": True, "data": {"label": self.label}},
                read_only=True,
            ),
            ToolSpec(
                name="mutate",
                description="writes state",
                parameters={"type": "object", "properties": {}},
                fn=lambda: {"ok": True, "data": {}},
                read_only=False,
            ),
        ]

    def call_tool(self, name: str, args: dict) -> dict:
        self.calls.append((name, dict(args)))
        for spec in self.tools():
            if spec.name == name:
                return spec.fn(**args)
        return {"ok": False, "data": {}, "warnings": [], "rev": None,
                "error": f"unknown_tool: {name}"}


def make_app() -> tuple[FastAPI, ScopeRegistry]:
    registry = ScopeRegistry()
    app = FastAPI()
    app.include_router(tool_router(registry))
    return app, registry


def test_describe_includes_read_only_hint():
    spec = ToolSpec(name="t", description="d", parameters={"type": "object"},
                     fn=lambda: {}, read_only=True)
    assert describe(spec) == {
        "name": "t",
        "description": "d",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    }


def test_list_tools_without_token_is_401():
    app, registry = make_app()
    registry.register("g1", "secret", FakeRuntime("g1"))
    client = TestClient(app)
    resp = client.get("/scopes/g1/tools")
    assert resp.status_code == 401


def test_list_tools_wrong_token_is_403():
    app, registry = make_app()
    registry.register("g1", "secret", FakeRuntime("g1"))
    client = TestClient(app)
    resp = client.get("/scopes/g1/tools", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


def test_list_tools_unknown_scope_is_404():
    app, registry = make_app()
    client = TestClient(app)
    resp = client.get("/scopes/nope/tools", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 404


def test_list_tools_describes_with_read_only_hint():
    app, registry = make_app()
    registry.register("g1", "secret", FakeRuntime("g1"))
    client = TestClient(app)
    resp = client.get("/scopes/g1/tools", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    tools = {t["name"]: t for t in resp.json()["tools"]}
    assert tools["echo"]["annotations"]["readOnlyHint"] is True
    assert tools["mutate"]["annotations"]["readOnlyHint"] is False


def test_call_tool_unregistered_scope_is_404():
    app, registry = make_app()
    client = TestClient(app)
    resp = client.post("/scopes/nope/tools/echo",
                        headers={"Authorization": "Bearer secret"},
                        json={"arguments": {}})
    assert resp.status_code == 404


def test_call_tool_unknown_tool_is_200_with_error_envelope():
    app, registry = make_app()
    registry.register("g1", "secret", FakeRuntime("g1"))
    client = TestClient(app)
    resp = client.post("/scopes/g1/tools/no-such-tool",
                        headers={"Authorization": "Bearer secret"},
                        json={"arguments": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "unknown_tool" in body["error"]


def test_two_scopes_never_see_each_others_tools_or_state():
    app, registry = make_app()
    runtime_a = FakeRuntime("group-a")
    runtime_b = FakeRuntime("group-b")
    registry.register("g1", "secret-a", runtime_a)
    registry.register("g2", "secret-b", runtime_b)
    client = TestClient(app)

    resp_a = client.post("/scopes/g1/tools/echo",
                          headers={"Authorization": "Bearer secret-a"},
                          json={"arguments": {}})
    assert resp_a.json()["data"]["label"] == "group-a"

    resp_b = client.post("/scopes/g2/tools/echo",
                          headers={"Authorization": "Bearer secret-b"},
                          json={"arguments": {}})
    assert resp_b.json()["data"]["label"] == "group-b"

    assert len(runtime_a.calls) == 1
    assert len(runtime_b.calls) == 1

    # g1's token must not unlock g2's scope.
    resp_cross = client.post("/scopes/g2/tools/echo",
                              headers={"Authorization": "Bearer secret-a"},
                              json={"arguments": {}})
    assert resp_cross.status_code == 403
    assert len(runtime_b.calls) == 1
