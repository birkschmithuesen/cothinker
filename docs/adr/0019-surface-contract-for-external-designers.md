# ADR 0019: Surface contract for external designers

- **Status:** accepted
- **Date:** 2026-11-23

## Context

Source: ADR 0006 (Surface API), ADR 0012 (External Display), ADR 0017 (FastAPI), and the experience of the first
workshop surface built by an external designer.

Surfaces are designed and built outside the core team. In the previous project the designer worked against a
self-made simulation: a fake data hub on an old copy of the page, with its own event names. The result looked right,
but its script listened to events only the simulation sent, sat more than a hundred commits behind, and broke the
skin rules. It could not be deployed; every screen had to be ported by hand, and the gap only showed days before
the event. The cause was not the design, it was the missing contract: nothing told the designer what the core
really sends, and nothing checked a design against it before it was finished.

## Decision

The **only** meeting point between a surface and the core is a machine-readable contract in this repository:

1. **Schema.** Every surface payload (view intents, state snapshots, user actions) is one Pydantic model in
   `surfaces/contract/`. Each field has a description and an example. The FastAPI app serves the generated
   OpenAPI document and its interactive docs at `/docs`; the generated `surfaces/contract/openapi.json` is
   committed, and CI fails when code and file drift apart. Every push channel (server-sent events, WebSocket) is
   described in `surfaces/contract/asyncapi.yaml` (AsyncAPI), generated from the same models by a script in this
   repository and drift-checked in CI the same way. Request/answer lives in OpenAPI, push lives in AsyncAPI, the
   models are shared, nothing is written twice.
2. **Fixtures.** For every intent kind there is a fixture file with realistic, invented data
   (`surfaces/contract/fixtures/`). A fixture mode (`--fixtures <scenario>`) makes the real server replay a
   scenario from these files over the real endpoints. A designer builds against the real server in fixture mode,
   never against a self-made simulation.
3. **Not yet filled.** A field the core cannot fill yet is still part of the schema, marked
   `x-status: planned` with the issue that will fill it, and carries an example. The fixtures include it, so the
   design can use it; the live server sends it empty.
4. **Conformance test.** A surface is done when `tests/test_surface_conformance.py` passes for it: the surface is
   loaded headless against every fixture scenario, must render or explicitly drop every intent kind (ADR 0006),
   must not request anything outside the contract, and must pass the skin rules and the design gate screenshots.
5. **Change path.** A surface that needs data the contract does not have opens an issue against the contract
   first. The core adds it additively (schema, example, fixture), then the surface uses it. A surface never
   invents its own fields or events.
6. **Look versus layout.** The generic layout lives in this repository under `surfaces/`. Names, logos, colours and
   texts of a specific instance live in the private instance repository as design-token overrides and content
   (ADR 0015); the public layout ships with neutral example data only.

## Consequences

- An external designer and their agent need nothing but this repository: contract, docs, fixtures, test.
- The stage moves from the standard-library HTTP server to FastAPI when it is next touched (ADR 0017); until then
  its payloads get schema models first, so the contract exists before the rewrite.
- New dependencies: `fastapi`, `uvicorn`, `pydantic`, declared in a dependency file.
- OpenAPI describes request/answer endpoints and only weakly push streams; a room display reacts to events
  (a decision, a new agenda item), so its push channels need AsyncAPI from the start.
- The schema is the single source; hand-written payload descriptions in other documents are replaced by links.

## Rejected options

- Designer builds against a self-made simulation: proven not to integrate (see Context).
- Hand-written API documentation: drifts from the code; nothing fails when it does.
- Push channels described only in prose or only in OpenAPI: the event-driven part of a surface would have no
  checked contract.
- Leave unfillable fields out of the schema: the designer would design around the gap or invent the field.
