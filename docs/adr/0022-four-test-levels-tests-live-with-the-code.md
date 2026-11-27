# ADR 0022: Four test levels, and tests live with the code they test

- **Status:** accepted, amended by ADR 0025
- **Date:** 2026-11-24

## Context

Source: ADR 0002 (five core APIs), ADR 0008 (building blocks), ADR 0009 (templates), ADR 0019 (surface contract).
The code is now split into modules, building blocks, templates and surfaces, but the tests are not: all block tests
sit centrally in `tests/test_block_*.py`, so a block taken into another project leaves its tests behind. Only one of
the five core APIs (the surface API) has a contract test that an outside implementation can run. No template is
tested end to end. A deployment was accepted by hand, with a browser running on the server itself, which tests a
shortcut and not the path a participant takes.

## Decision

There are four test levels. Each one has a fixed place and a fixed trigger.

| Level | Proves | Lives in | Runs |
|---|---|---|---|
| 1. Unit | one module, block or surface in isolation; clock, state and agent are injected fakes | `<component>/tests/` next to the code, e.g. `blocks/timer/tests/` | every commit (CI) |
| 2. Contract | an implementation of one of the five core APIs (STT, agent, surface, module, instance) keeps the contract; the same test is run against outside implementations | `tests/contract/<api>/` | every commit (CI) |
| 3. Template | a template runs through all its phases with a recorded transcript; no network, no cost | `templates/<name>/tests/` | every commit (CI) |
| 4. Deployment | a running deployment works as a participant reaches it: from outside, over the public host, real browser, real call, real speech recognition | `demo/acceptance/`, documented in `docs/acceptance.md` | by hand before every live use |

Rules:

1. Every directory under `blocks/`, `modules/`, `templates/` and every surface has a `tests/` directory with at
   least one test. A module declares it in `module.toml` (`tests = "tests"`); `load_module` refuses a module whose
   declared test directory is missing. CI fails when a component directory has no tests.
2. Level 1 to 3 never touch the network and never spend money. Level 4 is the only level that may.
3. A level-2 contract test is written so that it takes the implementation as a parameter; an outside implementation
   passes the same test, not a copy of it (as ADR 0019 does for surfaces).
4. Level 4 reports PASS, FAIL or SKIPPED per check with evidence and exits non-zero on any FAIL. A check it cannot
   run for real (for example a wake phrase spoken by a synthetic voice the recogniser cannot understand) is SKIPPED,
   never PASS. It never prints or stores the access token and never deletes data on the server.
5. "Ready" means: level 1 to 3 green in CI for the deployed commit, and a level-4 report without FAIL for the
   deployment, not older than the change that was deployed.

## Consequences

- The block tests move from `tests/test_block_*.py` into `blocks/<name>/tests/`; pytest collects all test
  directories (`testpaths`).
- Contract tests for the STT, agent, module and instance APIs have to be written; the surface conformance test moves
  under `tests/contract/surface/`.
- Each template gets a recorded transcript fixture and a phase run test.
- The deployment acceptance run becomes one command with a documented report (`docs/acceptance.md`).

## Rejected options

- Keep all tests central in `tests/`: simpler today, but a component taken into another instance or repository
  then leaves its tests behind, and nobody sees which component is untested.
- Only end-to-end tests: slow, cost money, and do not show which component broke.
- Contract tests as documentation only: an outside implementer cannot run documentation.
