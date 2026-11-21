# ADR 0001: Instance, core and module layering

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 16, 19 and 24; repo-split design 2026-11-12 (instance seam, module seam).

Today one repository holds the generic tool, the meeting method, the workshop method and the configuration of concrete deployments. The repo split already moved instance material behind `shared/instance.py` (`instances/<name>/`) and added a module seam (`shared/modules.py`). The meeting method was named as the first real module to cut out of the core (entry 16). Feature modules were introduced as a third module kind (entry 19) and then folded into "everything is a block" (entry 24).

## Decision

CoThinker has three layers: a **core** (five APIs, phase engine, block host, session runtime, no method logic), **modules** (packaging unit for blocks, templates, surfaces and STT engines), and **instances** (configuration and content of one deployment, kept outside the public repository).

## Consequences

- The core can be published and tested without any instance material; an invented example instance runs the test suite.
- A method cannot add code to the core; what it needs beyond the core is a block in a module.
- `instance.toml` grows tables for the template, block overrides, STT and surfaces (see ADR 0002, Instance API).
- A session is an episode of an instance; state that spans sessions lives in the instance's project, not in the session.

## Rejected options

- Methods as code modules next to the core: superseded by ADR 0009 (methods are templates).
- One repository per method with its own copy of the runtime: duplicates the session runtime, STT and agent plumbing that the inventory already shows built twice or three times.
