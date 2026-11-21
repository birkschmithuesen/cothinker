# ADR 0014: Licence: AGPL core, CLA and DCO; modules may carry their own licence

- **Status:** accepted (the core part is already in effect: `LICENSE`, `CLA.md`, `CONTRIBUTING.md`)
- **Date:** 2026-11-20

## Context

Source: repo-split design 2026-11-12, §9 decision **D1** "Licence of the public `cothinker` (and of modules)", options (a) AGPL-3.0-only stays + `CLA.md` stays, (c) AGPL core + modules in own repos under their own licence; and the D1 gate (repo-split plan Task 6.4) as executed after Birk's answer: core stays AGPL-3.0-only, `CLA.md` unchanged, `CONTRIBUTING.md` requires a DCO sign-off in addition to the CLA.

## Decision

The core is licensed **AGPL-3.0-only**. Contributions need the **CLA** (`CLA.md`) and a **DCO** `Signed-off-by:` trailer on every commit. A module in its own repository may carry its own licence, provided it connects to the core only through the five APIs.

## Consequences

- The network clause covers hosted use of the core.
- The CLA keeps a commercial dual licence possible; the DCO records who submitted what.
- Module authors choose their licence; a module that copies core code inherits AGPL for that code.
- The DCO is enforced by a CI check (`scripts/check_dco.py`, `.github/workflows/dco.yml`), in addition to the requirement text in `CONTRIBUTING.md`.

## Rejected options

- Apache-2.0 for the core (D1 option (b)): an Apache release cannot be taken back into AGPL later; AGPL with a CLA is the reversible choice.
