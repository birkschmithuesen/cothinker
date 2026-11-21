# ADR 0007: Coupling rule between blocks and surfaces

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, "Still open" list (coupling rule); architecture diagram v3; confirmed by Birk 2026-11-20.

ADR 0006 defines the Surface API. It does not say whether a template may bring rendering that the generic surfaces lack — for example, a workbench-style view or a term board, which have no generic equivalent. The diagram v3 draws the rule below; Birk has confirmed it.

## Decision

Blocks reach surfaces only through the Surface API. A template **may** ship a surface extension: a renderer that renders exactly one intent kind, lives in the template, and plugs into an extension point the generic surface declares. First cases: the workbench (InScribe Production) and the term board.

## Consequences

- Template-specific renderers can be ported as extensions without making the generic surface specific to one template.
- No block imports surface code; a template-shipped extension still reaches surfaces only through the Surface API's extension point.

## Rejected options

- Blocks write surface files or call surface code directly: the coupling ADR 0006 removes.
- Every renderer must live in a generic surface, with template-specific intent kinds becoming core intent kinds: rejected, as it would make the generic surfaces specific to one template's needs.
