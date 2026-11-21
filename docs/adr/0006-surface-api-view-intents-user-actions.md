# ADR 0006: Surface API: view intents out, user actions in

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 2, 7 and 15.

There is no surface interface today. The meeting writes a stage file that the stage server renders and cannot write back to; the workshop hub pushes 25 private message types to its own pages and reads user actions from its sockets (gap analysis §5). Surfaces and method logic are coupled through these private formats. The meeting stage view stays as it is (entry 2); wall and floor output become their own surface (entry 7).

## Decision

Blocks and the phase engine emit **view intents** — prompt to a person, choice list, timer, status, artefact — and surfaces render them. Surfaces send **user actions** (choose, accept, reject, edit, tap, voice) back. A voice command is a user action like a tap, so everything reachable by touch is reachable by speech.

## Consequences

- The stage becomes the first surface; the session host stays the only writer of the stage file.
- The workshop hub has to be taken apart into blocks and a web-app surface; this is the largest rebuild of the migration.
- A surface that cannot render an intent kind drops it and logs; the block does not fail.
- New intent kinds are additive; the speaker queue and speaking share need kinds beyond the five above (open point in the migration plan's gate list).

## Rejected options

- Blocks render their own HTML: every surface would need every block's markup; the coupling the inventory found would stay.
- Shared state file read by all surfaces: invites a second writer, which the sync layer answers with conflict copies.
