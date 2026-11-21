# ADR 0008: Building blocks with a small fixed API

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 18, 19, 20, 21, 23 and 24 (24 supersedes the rule of two from 20/21).

The inventory finds functions built two or three times: capture bullets, capture terms, condense, graph, speaker labels, interview round (blocks-inventory.md). Timer and speaking share were first planned as separate feature modules (entries 18/19). A "rule of two" (extract a block only when two methods need it) was accepted and then replaced: everything is a block from the start, with one guard against premature generalisation (entry 24).

## Decision

Everything that does work is a **block**. A block has a small, fixed external API (`start`, `on_event`, `stop`, a context with state, agent, language, clock, intent emitter and artefact writer). Its internals are not generalised beyond their first use.

## Consequences

- Blocks communicate only through core events the block host routes; they never import each other.
- A block writes only through its context; the host owns the session folder.
- Functions built more than once converge on one block when the second user moves onto the contract, not before.
- Timer, speaking share and speaker queue are ordinary blocks, not a separate module kind.

## Rejected options

- Rule of two (entries 20/21): superseded by entry 24.
- Feature modules as a third module kind (entries 18/19): folded into blocks.
- A rich generic block API up front: risks abstractions no second user needs.
