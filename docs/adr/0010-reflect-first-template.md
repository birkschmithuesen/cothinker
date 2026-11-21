# ADR 0010: Reflect: name and first template, 12 blocks

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 17, 27, 29, 30 and 31.

The meeting method needed a name (entry 17). Birk decided it is built first (entry 27) and confirmed its block list group by group (entries 29–31). Four of the twelve blocks have no meeting code today: condense, timer, speaker queue and speaking share (blocks-inventory.md, Reflect check).

## Decision

The meeting method is called **Reflect** ("Reflect – a method in CoThinker"; "CoThinker Meeting" stays the access name) and is the **first template** built on the block contract. It has exactly 12 blocks: capture bullets, topic change, decisions / tasks / open questions, condense, minutes document, mirroring / coach question, knowledge link, timer, speaker queue, speaking share, status line, session graph.

## Consequences

- The migration builds the block contract, template loader and phase engine against Reflect first.
- Timer, speaker queue and speaking share move out of their current home into blocks and gain a meeting-side input and a stage rendering.
- Not in Reflect: own question, intro / interview / setting / feedback rounds, release to guests, choice list (they belong to another template).

## Rejected options

- A different template as first: more blocks and surfaces, and the meeting runs weekly, the faster feedback loop.
- A smaller Reflect without the workshop-only blocks: Birk required them (entry 27).
