# Architecture decision records

One file per decision, numbered, never renumbered. Copy `0000-template.md` for a new one.
A changed decision gets a new ADR that supersedes the old one; the old file stays with status `superseded by ADR NNNN`.
**Accepted ADRs are immutable:** only the status line of an accepted ADR may change (plus added `Clarification:` /
`Consequence (added <date>):` lines). CI checks it: `python scripts/check_adr_immutable.py`. Every change of an
accepted decision is a new ADR that supersedes it. New decisions go into their own pull request.

ADRs are written in English and name no person other than the decision owner, Birk. Each ADR cites its source:
an entry number of the 2026-11-20 concept-round decision log, or a decision (D1, D2, D3) of the repo-split design.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-instance-core-module-layering.md) | Instance, core and module layering | accepted |
| [0002](0002-five-core-apis.md) | Five core APIs | accepted |
| [0003](0003-stt-engines-speechmatics-eu-and-local-whisper.md) | STT module with Speechmatics EU and optional local Whisper only | accepted |
| [0004](0004-transcript-pipeline-inside-stt-module.md) | Transcript processing inside the STT module | accepted |
| [0005](0005-agent-api-hermes-api-server.md) | Agent API via the Hermes API server, no in-process import | accepted |
| [0006](0006-surface-api-view-intents-user-actions.md) | Surface API: view intents out, user actions in | accepted |
| [0007](0007-coupling-rule-blocks-surfaces.md) | Coupling rule between blocks and surfaces | accepted |
| [0008](0008-everything-that-works-is-a-block.md) | Building blocks with a small fixed API | accepted |
| [0009](0009-methods-are-templates.md) | Methods are templates; the core runs phases | accepted |
| [0010](0010-reflect-first-template.md) | Reflect: name and first template, 12 blocks | accepted |
| [0011](0011-inscribe-one-template-two-formats.md) | InScribe: one template, long and one-day format | accepted |
| [0012](0012-external-display-surface.md) | External Display as its own surface | accepted |
| [0013](0013-telegram-parked.md) | Telegram parked | accepted |
| [0014](0014-licence-agpl-cla-dco-modules-own-licence.md) | Licence: AGPL core, CLA and DCO; modules may carry their own licence | accepted |
| [0015](0015-public-repo-fresh-export-private-instances.md) | Public repository from a fresh export, private instance repositories | accepted |
| [0016](0016-public-repo-issues-and-prs.md) | Work in the public repository through issues and pull requests | accepted |
| [0017](0017-network-seams-fastapi-blocks-in-process.md) | Network seams on FastAPI, blocks stay in-process | accepted |
| [0018](0018-product-names-interaction-migration-rules.md) | Product names, interaction and migration rules | accepted |
| [0019](0019-surface-contract-for-external-designers.md) | Surface contract for external designers | accepted |
