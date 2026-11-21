# ADR 0009: Methods are templates; the core runs phases

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 8, 23 and 24.

Phase logic exists several times in private form across method-specific applications. The meeting method is the session host's `main()`. A separate group-process method was dropped (entry 8). Birk asked for methods to be bundles of blocks (entry 23) and decided it (entry 24).

## Decision

A method is a **template**: a file that lists blocks, phases and rules, with no code of its own. A phase says which blocks run, with which prompt and rules, on which surfaces. The core phase engine runs the phases; every transition is recorded with its cause and can be reversed.

## Consequences

- Logic unique to a method is a block.
- Private phase/cue machinery across method-specific applications becomes phase definitions in templates.
- Automatic phase changes are allowed because each has a history entry and an undo.
- Every template carries the core rules `grounding` and `add-dont-restructure`.

## Rejected options

- Method modules with their own code: superseded by entry 24.
- A dedicated group-process method: dropped by entry 8.
