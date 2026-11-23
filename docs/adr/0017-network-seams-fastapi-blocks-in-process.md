# ADR 0017: Network seams on FastAPI, blocks stay in-process

- **Status:** accepted (confirmed 2026-11-23)
- **Date:** 2026-11-20

## Context

Source: ADR 0002 (five core APIs), ADR 0005 (Agent API over HTTP), ADR 0006 (Surface API), ADR 0008 (blocks with a
small fixed API), `docs/architecture.md` §3 (interface map and transport rule). The core APIs that cross a process
boundary (Agent, parts of Surface, parts of Instance) already need a concrete HTTP server technology; the APIs
that stay inside one process (a block talking to the block host, a template talking to the phase engine) do not,
and giving them one anyway would add a network hop with no second process on the other end.

## Decision

Where a core API's transport rule (`docs/architecture.md` §3) calls for HTTP, the core uses **FastAPI** as the one
server framework for that seam. Where it calls for an in-process call (a block's `start`/`on_event`/`stop`, the
phase engine running a template), the call stays a **plain Python call through the block host**, never a network
request to itself.

## Consequences

- One HTTP framework across the core's network seams, instead of each adapter picking its own.
- Blocks are not required to run as separate services or expose a port; the block contract (ADR 0008) does not
  change.
- A future block that genuinely needs its own process (a heavy model, a sandboxed executor) reaches the rest of
  the system through one of the five APIs like any other adapter, not through a new ad-hoc network path.

## Rejected options

- Every block behind its own small HTTP server: turns a single-process host into a service mesh for no current
  need, and reintroduces per-block transport choices ADR 0002 removed.
- A second web framework for the Surface API's browser-facing parts: one framework for the core's network seams
  is easier to operate and to review than two.
