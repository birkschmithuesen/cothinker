# ADR 0002: Five core APIs

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 1, 11, 14, 15 and 16.

Entry 1 calls the plug points "APIs". Today each application wires its own STT client, agent call, view protocol and configuration reader (gap analysis §1, §4, §5, §11). Entries 11, 14, 15 and 16 each asked for one shared interface in place of the private ones.

## Decision

The core exposes exactly five APIs: **STT** (transcript events), **Agent** (prompt in, reply events out), **Surface** (view intents out, user actions in), **Module** (load and version modules), **Instance** (read configuration and content). Every block, template and surface reaches the core only through these five.

## Consequences

- Each API gets a versioned interface in `shared/` and a test that pins it; changes are additive.
- The workshop hub's direct Speechmatics stream, the meeting's in-process worker and both private view protocols have to move behind the APIs (migration plan waves).
- A sixth interface needs a new ADR.

## Rejected options

- One generic plugin interface for everything: hides the direction of data flow (audio in, intents out) that the safety rules depend on.
- Keeping per-application interfaces and sharing only libraries: the inventory shows this produced two STT paths and three copies of several functions.
