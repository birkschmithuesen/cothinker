# ADR 0012: External Display as its own surface

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 7 and 15.

Wall and floor projection, and other wall-graph/dream-screen style surfaces, each live inside their own application today, bound to it.

## Decision

Wall, floor, projection and screen output form one surface, **External Display**, which renders view intents and can be attached to any template.

## Consequences

- A meeting can show its session graph or timer on a projection without workshop-specific code.
- Existing wall layouts move out of their application into this surface; the design gate applies to every change of it.
- Other screen-based surfaces become External Display layouts.

## Rejected options

- Keep wall and floor as part of one specific web app: ties projection to one format.
