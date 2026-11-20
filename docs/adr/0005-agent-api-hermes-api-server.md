# ADR 0005: Agent API via the Hermes API server, no in-process import

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entry 14.

`shared/agent.py` defines `AgentPort` and `HermesApiAgentPort` (Hermes API server over HTTP); the workshop and the meeting's coach lane use it. The meeting's main turn runs a worker process that imports Hermes in-process and lets the agent edit the minutes file and append stage proposals itself (gap analysis §4). That ties the session host to the agent's Python environment and to file tools on the machine holding the session folder.

## Decision

Every agent call goes through the Agent API (`AgentPort`); the first adapter is the Hermes API server. No component imports an agent runtime in-process. The agent returns content; blocks write it.

## Consequences

- The meeting's main turn must first return structured content (bullets, decisions, stage proposals) that blocks write; only then does the transport switch.
- The failure-text check (`API call failed`, `HTTP 4`) moves into the core adapter and runs on every returned text, next to the structured `failed` event.
- Other agent runtimes become possible as further adapters.
- The choice of model provider stays an explicit instance configuration; this ADR does not change which provider runs.

## Rejected options

- Keep the in-process worker for the meeting only: two agent paths, and the worker's file edits bypass the single-writer rule for session files.
- Direct model API calls without an agent runtime: loses tools, skills and session memory the methods rely on.
