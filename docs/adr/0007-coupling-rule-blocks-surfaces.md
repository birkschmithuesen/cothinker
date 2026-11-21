# ADR 0007: Coupling rule between blocks and surfaces

- **Status:** proposed — **open point: not confirmed by Birk**
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, "Still open" list (coupling rule); architecture diagram v3.

ADR 0006 defines the Surface API. It does not say whether a template may bring rendering that the generic surfaces lack — for example, a workbench-style view or a term board, which have no generic equivalent. The diagram v3 draws the rule below; Birk has not confirmed it.

## Decision

Blocks reach surfaces only through the Surface API. A template **may** ship its own surface extension: a renderer for an intent kind, packaged in the template's module and loaded by a surface that declares the extension point.

**Open point:** whether templates may ship surface extensions at all, or whether every renderer must live in a generic surface. Until Birk decides, no task in the migration depends on a template-shipped extension.

## Consequences

- If confirmed: template-specific renderers can be ported as extensions without making the generic web app specific to one template.
- If rejected: those renderers become generic surface features, and their intent kinds become core intent kinds.
- Either way, no block imports surface code.

## Rejected options

- Blocks write surface files or call surface code directly: the coupling ADR 0006 removes.
