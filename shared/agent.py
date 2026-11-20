#!/usr/bin/env python3
# CoThinker — agent seam (the brain), framework-independent
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The agent seam (docs/architecture.md §1): one thin interface of
CoThinker's own, one adapter per agent framework. Nothing outside an
adapter module may know which framework is behind it --
`tests/test_agent_port.py` enforces that for `workshop/` with a grep for
Hermes HTTP patterns.

Moved here from `workshop/agent_port.py` and `workshop/agent_api.py` (repo
split plan task 4.3) so `gateway/` can use the seam without importing
`workshop/`; both old modules re-export these names.

Contract:

    open_session(context_id, briefing)                     -> Session
    send_turn(session, text, attachments=None, model=None) -> iterator of Event
    stop(session) / close(session)

`model` (Do item 1): an explicit per-request model override for
this one turn, e.g. so `ProfileOrchestratedRun`'s single orchestration turn can
pin `claude-opus-5-5` without relying on the shared profile's `model.default`.
`None` (the default) sends no override at all -- never a guessed model name.

`Event.kind` is one of `EVENT_KINDS`. Additive only, like the STT wire
contract (`workshop/stt_speechmatics.py`): a new field defaults to absent,
nothing already named here is ever renamed or removed.

Adapters:
- `HermesApiAgentPort` below -- the Hermes API server, the one real
  adapter built so far.
- `FakeAgentPort` below -- used by tests and (later) the S9 simulator, so
  neither needs a running agent.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator, Optional, Protocol, runtime_checkable

EVENT_KINDS = frozenset({
    "text_delta",
    "tool_started",
    "tool_finished",
    "subagent_started",
    "subagent_finished",
    "thinking",
    "done",
    "failed",
})

RETRY_WAIT_S_DEFAULT = 2.0


@dataclass(frozen=True)
class Event:
    """One normalised seam event. `kind` is one of `EVENT_KINDS`; the other
    fields are filled in as the kind calls for (e.g. `text` for a
    `text_delta`, `usage` for `done`/`failed`) and left at their default
    otherwise."""

    kind: str
    text: str = ""
    tool_name: str = ""
    label: str = ""
    usage: dict = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")


@dataclass
class Session:
    """A session handle. `handle` is adapter-specific (e.g. a Hermes
    session id); callers never interpret it, only pass it back."""

    context_id: str
    handle: str


@runtime_checkable
class AgentPort(Protocol):
    def open_session(self, context_id: str, briefing: str) -> Session: ...

    def send_turn(self, session: Session, text: str, attachments: Optional[list] = None,
                  model: Optional[str] = None) -> Iterator[Event]: ...

    def stop(self, session: Session) -> None: ...

    def close(self, session: Session) -> None: ...


class FakeAgentPort:
    """A deterministic double of `AgentPort` -- no sockets, no real agent.
    Used by tests and, later, the S9 simulator.

    `send_turn` echoes the input text as one `text_delta` followed by
    `done`, unless `script()` has queued a reply for that exact text --
    a queued reply is consumed once, then the port falls back to the echo
    again.
    """

    def __init__(self) -> None:
        self._scripts: dict[str, list[Event]] = {}
        self.opened: list[Session] = []
        self.stopped: list[Session] = []
        self.closed: list[Session] = []
        self._n = 0

    def script(self, text: str, events: list[Event]) -> None:
        self._scripts[text] = list(events)

    def open_session(self, context_id: str, briefing: str) -> Session:
        self._n += 1
        session = Session(context_id=context_id, handle=f"fake-{self._n}")
        self.opened.append(session)
        return session

    def send_turn(self, session: Session, text: str, attachments: Optional[list] = None,
                  model: Optional[str] = None) -> Iterator[Event]:
        events = self._scripts.pop(text, None)
        if events is None:
            events = [Event(kind="text_delta", text=text), Event(kind="done")]
        yield from events

    def stop(self, session: Session) -> None:
        self.stopped.append(session)

    def close(self, session: Session) -> None:
        self.closed.append(session)


class Api:
    """Stdlib `urllib` client for the Hermes API server (`gateway/platforms/
    api_server.py`). Facts verified against the installed Hermes source
    (v0.22.3+127.g8847eca, commit 8847eca, 2026-10-22) -- see
    `HermesApiAgentPort` below for the wire-shape notes moved here from
    `workshop/agent_api.py`.
    """

    def __init__(self, base: str, key: str, timeout_s: float = 600) -> None:
        self.base = base.rstrip("/")
        self.key = key
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json"}

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, headers=self._headers(), method=method)
        return urllib.request.urlopen(req, timeout=self.timeout_s)

    def create_session(self) -> str:
        with self._request("POST", "/api/sessions", {}) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["session"]["id"]

    def chat_stream(self, sid: str, text: str) -> dict:
        t0 = time.monotonic()
        try:
            resp = self._request("POST", f"/api/sessions/{sid}/chat/stream",
                                  {"input": text})
        except urllib.error.HTTPError as e:
            return {"ok": False, "t_first_delta": None, "t_total": time.monotonic() - t0,
                    "tools": [], "terminal": None, "usage": {}, "status": e.code,
                    "final": ""}

        t_first_delta = None
        terminal = None
        usage: dict = {}
        tool_started: dict[str, float] = {}
        tools: list[dict] = []
        final_chunks: list[str] = []
        final_text = ""
        event_name = None
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    event_name = None if not line else event_name
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                    continue
                if line.startswith("data:"):
                    raw_data = line[len("data:"):].strip()
                    try:
                        payload = json.loads(raw_data) if raw_data else {}
                    except ValueError:
                        payload = {}
                    if event_name == "assistant.delta" and t_first_delta is None:
                        t_first_delta = time.monotonic() - t0
                    if event_name == "assistant.delta":
                        final_chunks.append(str(payload.get("delta") or ""))
                    elif event_name == "assistant.completed":
                        final_text = str(payload.get("content") or "")
                    elif event_name == "tool.started":
                        tool_started[payload.get("tool_name") or ""] = time.monotonic()
                    elif event_name in ("tool.completed", "tool.failed"):
                        name = payload.get("tool_name") or ""
                        started = tool_started.pop(name, None)
                        tools.append({"name": name,
                                      "duration_s": (time.monotonic() - started) if started else 0.0})
                    elif event_name in ("run.completed", "run.failed", "run.cancelled"):
                        terminal = event_name
                        usage = payload.get("usage") or {}
                        break
                    event_name = None
        return {"ok": terminal == "run.completed", "t_first_delta": t_first_delta,
                "t_total": time.monotonic() - t0, "tools": tools, "terminal": terminal,
                "usage": usage, "status": 200,
                "final": final_text or "".join(final_chunks)}

    def messages(self, sid: str) -> list[dict]:
        with self._request("GET", f"/api/sessions/{sid}/messages") as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("data", [])

    def health_detailed(self) -> dict:
        with self._request("GET", "/health/detailed") as resp:
            return json.loads(resp.read().decode("utf-8"))


class HermesApiAgentPort:
    """The agent seam over the Hermes API server -- the only real adapter
    built so far (docs/architecture.md §1). `/v1/runs` does not exist on
    this path; this speaks the verified `/api/sessions` + `/chat/stream`
    shape instead.

    - Auth: `Authorization: Bearer <API_SERVER_KEY>` (`_check_auth`).
    - `POST /api/sessions` returns `{"object": "hermes.session", "session":
      {"id": ..., ...}}` -- the id is nested under `session.id`, NOT a bare
      top-level `id` as an earlier S0 brief assumed
      (`_handle_create_session`).
    - `POST /api/sessions/{id}/chat/stream` emits SSE events `run.started`,
      `message.started`, `assistant.delta` (`{"delta": ...}`),
      `tool.started` / `tool.completed` / `tool.failed` (`{"tool_name":
      ..., "preview": ...}`), `assistant.completed`, then the terminal
      `run.completed` / `run.failed` / `run.cancelled` carrying
      `{"usage": ..., "messages": ..., "runtime": ...}`, followed by
      `done`. Keepalive lines are `: keepalive` with no `event:`/`data:`
      (`_handle_session_chat_stream`, `CHAT_COMPLETIONS_SSE_
      KEEPALIVE_SECONDS`).
    - `subagent.start` / `subagent.complete` exist in this Hermes version,
      but only on the `/v1/runs` and local-viewer paths
      (`gateway/platforms/api_server_runs.py`, `gateway/local_viewer_events.
      py`) -- the `/chat/stream` SSE bridge (`_tool_progress` in
      `_handle_session_chat_stream`) does not forward them. The observable
      signal for a `delegate_task` dispatch on this path is a
      `tool.started` / `tool.completed` pair named `delegate_task`, not two
      `subagent.start` events; `HermesApiAgentPort` maps that pair onto
      `subagent_started` / `subagent_finished` below, the same pair M2
      (`scripts/measure_hermes/m2_delegation.py`) checks.
    - `GET /api/sessions/{id}/messages` returns `{"object": "list", "data":
      [...]}`; a background-delegation delivery row carries `display_kind
      == "async_delegation_complete"` (`gateway/wake.py`,
      `persist_delegation_delivery`), not a distinct role. The adapter
      below does not poll this -- the `delegate_task` pair it maps is the
      dispatch, not the child's actual result; fetching that delivery is
      later work.
    - `GET /health/detailed` nests the delegation count at
      `readiness.checks.background_queues.active_delegations`
      (`gateway/readiness.py: collect_runtime_readiness`), not a top-level
      `active_delegations` key.
    - `gateway.api_server.max_concurrent_runs` (config.yaml) defaults to
      10; over it, `/chat` and `/chat/stream` answer HTTP 429
      (`_concurrency_limited_response`). `send_turn` waits `retry_wait_s`
      (default 2 s) and retries once; a second 429 (or any other HTTP
      error) ends the turn with a `failed` event instead of raising.
    - `delegation.max_concurrent_children` (config.yaml, top-level
      `delegation:`) defaults to 10.
    - `POST /api/sessions/{id}/chat/stream` accepts an optional `model` key
      in the request body as a per-request override (verified against the
      installed source, `gateway/platforms/api_server.py`'s
      `_extract_request_model_overrides()`: `body.get("model")`, honored
      unless it equals the virtual alias) -- Do item 1 uses
      this so a `Run` can pin its own turn's model without touching the
      profile's shared `model.default`.
    - `API_SERVER_ENABLED` / `API_SERVER_KEY` / `API_SERVER_PORT` are
      `.env` variables (default port 8642), NOT `config.yaml` keys.
    """

    def __init__(self, base: str, key: str, timeout_s: float = 600,
                 retry_wait_s: float = RETRY_WAIT_S_DEFAULT) -> None:
        self._api = Api(base, key, timeout_s)
        self._retry_wait_s = retry_wait_s

    def open_session(self, context_id: str, briefing: str) -> Session:
        sid = self._api.create_session()
        if briefing:
            # Cold start happens once, before the event (docs/architecture.md
            # §1 "Speed") -- prime the session now; the reply is not surfaced.
            self._api.chat_stream(sid, briefing)
        return Session(context_id=context_id, handle=sid)

    def send_turn(self, session: Session, text: str, attachments: Optional[list] = None,
                  model: Optional[str] = None) -> Iterator[Event]:
        if attachments:
            raise NotImplementedError(
                "HermesApiAgentPort does not support attachments yet")
        resp, err_code = self._open_stream(session.handle, text, model)
        if resp is None:
            yield Event(kind="failed", reason=f"http_{err_code}")
            return
        tool_started: dict[str, str] = {}
        with resp:
            event_name = None
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    event_name = None if not line else event_name
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                raw_data = line[len("data:"):].strip()
                try:
                    payload = json.loads(raw_data) if raw_data else {}
                except ValueError:
                    payload = {}
                yield from self._map(event_name, payload, tool_started)
                if event_name in ("run.completed", "run.failed", "run.cancelled"):
                    return
                event_name = None

    def stop(self, session: Session) -> None:
        # No cancel endpoint is verified on this Hermes version (class
        # docstring) -- nothing to call yet.
        pass

    def close(self, session: Session) -> None:
        pass

    def _open_stream(self, sid: str, text: str, model: Optional[str] = None):
        body: dict = {"input": text}
        if model:
            body["model"] = model
        try:
            return self._api._request(
                "POST", f"/api/sessions/{sid}/chat/stream", body), None
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return None, e.code
        time.sleep(self._retry_wait_s)
        try:
            return self._api._request(
                "POST", f"/api/sessions/{sid}/chat/stream", body), None
        except urllib.error.HTTPError as e:
            return None, e.code

    @staticmethod
    def _map(event_name: Optional[str], payload: dict,
              tool_started: dict[str, str]) -> Iterator[Event]:
        if event_name == "assistant.delta":
            yield Event(kind="text_delta", text=str(payload.get("delta") or ""))
        elif event_name == "reasoning.available":
            yield Event(kind="thinking")
        elif event_name == "tool.started":
            name = str(payload.get("tool_name") or "")
            label = str(payload.get("preview") or "")
            tool_started[name] = label
            kind = "subagent_started" if name == "delegate_task" else "tool_started"
            yield Event(kind=kind, tool_name=name, label=label)
        elif event_name in ("tool.completed", "tool.failed"):
            name = str(payload.get("tool_name") or "")
            label = tool_started.pop(name, str(payload.get("preview") or ""))
            kind = "subagent_finished" if name == "delegate_task" else "tool_finished"
            reason = "tool_failed" if event_name == "tool.failed" else ""
            yield Event(kind=kind, tool_name=name, label=label, reason=reason)
        elif event_name == "run.completed":
            yield Event(kind="done", usage=payload.get("usage") or {})
        elif event_name in ("run.failed", "run.cancelled"):
            yield Event(kind="failed", reason=event_name, usage=payload.get("usage") or {})
