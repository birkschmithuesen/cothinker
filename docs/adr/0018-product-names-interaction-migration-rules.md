# ADR 0018: Product names, interaction and migration rules

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20 (later decisions) and Birk's confirmation 2026-11-20, recorded after the ADR import (pull request #23).

Several points that earlier ADRs and `docs/architecture/target.md` left open needed a final call before the port could start: what to call the two InScribe surfaces, how much of the phase design the port redoes, how the speaker queue and stage identity work in Reflect, whether migration PRs may run old and new code side by side, and which Surface API intent kinds and Agent API tool transport the plan still listed as open.

## Decision

- **Product names.** The two InScribe formats/surfaces are named **InScribe Workshop** (the one-day format) and **InScribe Production** (the tool groups use for real productions, the long format). This supersedes the working names "workshop web app" and "InScribe web app" used in ADR 0011 and in `docs/architecture/target.md`.
- **Phase scope.** The phases of both formats reproduce the last state of the two previous productions 1:1; the port is not a phase redesign.
- **Speaker queue (Reflect).** Joining the speaker queue is tap only, a button on the stage; there is no voice-input path into the queue.
- **Stage identity (Reflect).** A session has one shared link (also shown as a QR code in a room); a participant enters a name once and the browser keeps a session-scoped participant id; there are no accounts. InScribe Workshop keeps its existing model of one individual link per seat.
- **Migration rule.** No dual structure: each new path replaces the old one in the same pull request — code, flags and tests together. No fallback paths and no switch-over gates.
- **Surface API and Agent API.** The Surface API's intent kinds gain `queue`, `share` and `graph` alongside the existing kinds. Block tools reach the agent as an MCP stdio server started per session, generated from the block tool specs.

## Consequences

- ADR 0011's "final names of the two web apps are open" is resolved; `docs/architecture/target.md` and all later documents use InScribe Workshop and InScribe Production instead of the working names.
- The port reads out the last state of each format's phases rather than designing new ones; no phase-design task is needed before the port.
- The Reflect stage surface needs only a tap affordance for the queue, not a voice-command path or diarization at the point of joining.
- The stage's session model needs a session-scoped participant id, not accounts; InScribe Workshop's per-seat link model needs no change.
- Every migration task lands as one replacement PR; the plan carries no feature flag and no switch-over gate for a surface, block or path it ports.
- `docs/architecture/target.md` §2.3's "proposed additions" (`queue`, `share`, `graph`) become part of `INTENT_KINDS`; §2.2's open question on module-tool transport is settled as an MCP stdio server per session.

## Rejected options

- Voice input into the speaker queue: rejected, a tap is unambiguous and needs no diarization at the moment of joining.
- Per-participant accounts for stage identity: rejected, a session-scoped id reached through one shared link is enough and keeps setup to a single link or QR code.
- Running the old and the new path side by side behind a flag during the port: rejected, a dual structure doubles the code and the tests to keep green for a migration that only ever goes one direction.
- Module tools reaching the agent through per-module skill scripts: rejected, an MCP stdio server generated from the same block tool specs needs no second tool-description format.
