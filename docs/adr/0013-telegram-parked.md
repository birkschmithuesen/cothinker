# ADR 0013: Telegram parked

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entry 3.

Some applications have Telegram front ends. Chat as an output channel failed in real use: the room does not read it. The web apps replace it.

## Decision

Telegram is **parked**: it is not a target surface, no migration task ports it, and no block depends on it.

## Consequences

- Existing Telegram code stays in the application repositories it lives in and is not moved into the core.
- Reviving it needs a new ADR that supersedes this one, and it would come back as a surface behind the Surface API.

## Rejected options

- Port Telegram as a surface now: no current use, and chat output is the channel the room ignores.
