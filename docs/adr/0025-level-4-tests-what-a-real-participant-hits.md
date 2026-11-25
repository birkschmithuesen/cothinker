# ADR 0025: Level-4 acceptance tests what a real participant hits

- **Status:** proposed
- **Date:** 2026-11-25

## Context

Source: the 2026-11-24 live trial findings (decision owner Birk). ADR 0022 defined level 4 as a run from outside
over the public path. On 2026-11-24 a deployment passed level 4 and then failed four times in live use: the call
lane ran on a silent profile after a redeploy; a spoken order with a pause after the wake phrase reached the agent
without its content; sentences were cut mid-way because the speech recogniser's end-of-utterance trigger fired
inside packet gaps of call audio; a wake sentence became the session topic. Level 4 let each one through. Its
scenarios came from the contract's fields, not from real requests: one wake order, always in one breath. Its audio
was clean synthetic speech with long silences, and segmentation was never measured. Its oracle accepted any
non-empty answer. It read no deployment configuration. It was not re-run after a redeploy. And the agent running it
could mark a gap as known on its own.

## Decision

Level 4 tests what a real participant hits. It amends ADR 0022 with these rules:

1. **Scenarios are data from real request classes.** Each scenario is a file in `demo/acceptance/scenarios/` that
   names the class of real request it stands for (an order given with pauses, an answer that needs project files,
   a list to structure, an interruption, overlapping speakers, a wake sentence that must not become the topic). A
   one-breath order may stay as a regression scenario but never counts alone.
2. **Mechanical oracle first, judge second, never judge alone.** A scenario passes only when its mechanical checks
   pass. An LLM judge can turn a pass into a fail, never a fail into a pass. The judge model is a required, pinned
   argument recorded in the report, different from the model under test, with a hard cap on calls and tokens.
   Without a judge, the judge sub-check is SKIPPED.
3. **Audio is degraded the way calls degrade it.** Scenario speech carries real pauses. Packet gaps measured on
   real call tracks are injected deterministically. Network impairment is optional and SKIPPED without the
   capability. Committed speech is licence-clean, and no real participant's recording is ever committed.
4. **Segmentation is measured.** Transcript lines are scored against a reference text (word error rate, sentence-
   boundary error, fragmentation). Every threshold is an upper bound derived from a committed baseline measurement
   that names its source; a threshold without a measurement is a required argument, never a default.
5. **Configuration invariants run first.** Before any scenario, a host-side preflight checks every lane's agent
   profile and toolsets, the speech recogniser's end-of-utterance and delay settings, and the building-block
   registry against the active template. It fails fast with the value it found.
6. **"Ready" is bound to the running deployment.** Preflight records a fingerprint of the running deployment (unit
   command lines incl. drop-ins, allow-listed lane environment, lane profile files, deployed commit and local diff,
   non-secret environment file). The go/no-go check recomputes it. It requires a level-4 report with the same
   fingerprint and no unaccepted FAIL. This replaces ADR 0022 rule 5's "not older than the change that was
   deployed".
7. **Only a human accepts a FAIL.** The tools never write an acceptance. An acceptance is an entry in a separate,
   version-controlled file that names one check id, the fingerprint it holds for, and the human who accepts it. It
   expires when the fingerprint changes. The go/no-go check ignores entries authored by an agent identity. Where the
   host cannot enforce this (a single-user host on which the agent can forge an author), the check says which
   verification it did and does not claim more.

## Consequences

- `demo/acceptance/` gains a scenario catalogue, a baseline file, a preflight with fingerprint, and a go/no-go
  check; `docs/acceptance.md` documents them.
- Level 4 costs more per run (judge calls, longer scenarios); each paid component has a cap in the report.
- Failures without a product fix stay red until the fix lands; nobody but a human can accept them.
- Some checks run on the deployment host (configuration), others from outside (participant path); both halves
  carry the same fingerprint.

## Rejected options

- Judge-only oracle: lenient; a summary is answered even by the wrong agent profile.
- Freshness from a start-script marker only: misses configuration changed by hand.
- An acceptance flag inside the report: the tool writing the report could set it.
- Committing real meeting audio for realism: violates the participants' privacy and the licence rules.
