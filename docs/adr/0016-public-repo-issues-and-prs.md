# ADR 0016: Work in the public repository through issues and pull requests

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entry 9 (GitHub workflow with ADRs, issues and PRs: yes).

Until now work was tracked on an internal card board and merged directly. A public repository needs a visible record of why the code changed, and Birk wants public history early.

## Decision

Work on the public core happens through **issues and pull requests** in the public repository; architecture decisions are recorded as **ADRs** in `docs/adr/`. Every change lands through a reviewed pull request that references its issue; CI runs the test suite and the publish check.

## Consequences

- The migration to the target architecture runs as issues and PRs once the public repository exists.
- Internal card ids and instance details never appear in issues, PRs or commits of the public repository; the publish check enforces the patterns.
- Accepted ADRs are immutable; a change of decision is a new ADR that supersedes the old one.

## Rejected options

- Keep developing privately and export periodically: every export would be a single opaque commit and public history would carry no reasons.
