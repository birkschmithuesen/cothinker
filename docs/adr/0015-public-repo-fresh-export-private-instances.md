# ADR 0015: Public repository from a fresh export, private instance repositories

- **Status:** accepted (D2/D3 resolved by Task 6.9's repo split)
- **Date:** 2026-11-20

## Context

Source: repo-split design 2026-11-12, §9 decisions **D2** "Name and repo identity" (recommendation (a): rename the existing repo to an archive name, private, full history; create the fresh public `cothinker` from the export; instance repos `cothinker-<instance>`) and **D3** "Host of the private instance repos" (recommendation (a): Codeberg private). Concept-round decision log 2026-11-20, entry 9 (export ok, push and public release cleared).

The existing repository's history carries instance and operator material that a scrub cannot remove from old commits.

## Decision

The public `cothinker` repository starts from a **fresh one-commit export** of a checked positive list; the existing repository becomes a private archive with full history. Each instance lives in its own **private repository on Codeberg** and pins the core version it runs.

## Consequences

- Public history starts at the export; earlier history stays private.
- Every clone of the existing repository must switch to the archive's new URL before the rename.
- Instance repositories need their own CI (suite and design gate) and read-only deploy keys.

## Rejected options

- Publishing the existing repository after a history rewrite: too much instance material in too many commits to prove clean.
- Instance repositories on GitHub next to the public repo (D3 option (b)): instance material belongs on an EU forge.
