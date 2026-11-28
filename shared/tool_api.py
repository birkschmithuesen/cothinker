# CoThinker — shared/tool_api.py: session host tool endpoint (ADR 0018)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""FastAPI router serving the tools of the runtime registered for a scope
(group id or session id), authenticated with a bearer token per scope.

A block tool reaches the agent through shared.mcp_stdio, a stdio relay that
calls this router over HTTP. The block instances stay in the session host;
only this router knows which runtime answers for which scope.
"""
from __future__ import annotations

import hmac
from typing import Mapping

from fastapi import APIRouter, HTTPException, Request

from shared.modules import ToolSpec

TOKEN_HEADER = "Authorization"


class ScopeRegistry:
    """Maps a scope (group id or session id) to its bearer token and runtime."""

    def __init__(self) -> None:
        self._scopes: dict[str, tuple[str, object]] = {}

    def register(self, scope: str, token: str, runtime: object) -> None:
        self._scopes[scope] = (token, runtime)

    def unregister(self, scope: str) -> None:
        self._scopes.pop(scope, None)

    def lookup(self, scope: str, token: str) -> object:
        """Raises KeyError for an unknown scope, PermissionError for a
        known scope with the wrong token."""
        entry = self._scopes.get(scope)
        if entry is None:
            raise KeyError(scope)
        expected_token, runtime = entry
        if not hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8")):
            raise PermissionError(scope)
        return runtime


def describe(spec: ToolSpec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": spec.parameters,
        "annotations": {"readOnlyHint": spec.read_only},
    }


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get(TOKEN_HEADER)
    if not header or not header.startswith("Bearer "):
        return None
    return header[len("Bearer "):]


def _resolve(registry: ScopeRegistry, scope: str, request: Request) -> object:
    token = _bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return registry.lookup(scope, token)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown scope")
    except PermissionError:
        raise HTTPException(status_code=403, detail="wrong token")


def tool_router(registry: ScopeRegistry) -> APIRouter:
    router = APIRouter()

    @router.get("/scopes/{scope}/tools")
    def list_tools(scope: str, request: Request) -> dict:
        runtime = _resolve(registry, scope, request)
        return {"tools": [describe(spec) for spec in runtime.tools()]}

    @router.post("/scopes/{scope}/tools/{name}")
    def call_tool(scope: str, name: str, body: Mapping[str, object], request: Request) -> dict:
        runtime = _resolve(registry, scope, request)
        arguments = body.get("arguments", {}) if isinstance(body, Mapping) else {}
        return runtime.call_tool(name, arguments)

    return router
