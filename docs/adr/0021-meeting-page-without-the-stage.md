# ADR 0021: Meeting page without the stage

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Source: ADR 0020 (host names and paths of a deployment). ADR 0020 put the integrated page (video call plus stage side
by side) under `/meeting/`. In practice most participants only want the call; the stage is watched on its own
screen or on the room display. A full-screen call is the more common entry point.

## Decision

The path table of ADR 0020 changes in two rows; everything else in ADR 0020 stays:

| Path | What |
|---|---|
| `/meeting/` | the video call only, full screen (name asked once, per-user token as before) |
| `/meeting-split/` | video call and stage side by side (the former `/meeting/`) |

## Consequences

- Links handed to participants point to `/meeting/`; the split view is for single-screen setups.
- Both pages share one session endpoint (`/meeting/api/session`) and the same token gate.

## Rejected options

- A query switch on one page (`/meeting/?layout=split`): a link that loses its query falls back silently to the
  other layout; two paths are explicit.
- Naming the split page `meeting-old`: it is not deprecated, it is a second layout.
