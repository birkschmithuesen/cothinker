# ADR 0004: Transcript processing inside the STT module

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 12, 13 and 22 (22 supersedes 12/13, which had placed the pipeline in the core).

Thought end (audio and text variants), correction and speaker labels exist only on the meeting path, wired by hand in the session host; the workshop has a second copy of speaker labelling and none of the other stages (gap analysis §3). Correction is narrow by design (punctuation, capitalisation, fillers, glossary names) and writes only to its own file, never back into model input.

## Decision

Transcript processing is part of the STT module, behind the STT API, as a pipeline of pluggable stages — **thought end → speaker labels → correction** — each switched on or off per instance. **Correction is display-only**: its output goes to a display sink and never into the event stream blocks and agents consume.

## Consequences

- Every template gets the same stages; the workshop and other instances gain thought end and correction by configuration.
- The correction stage has a different return type from the other stages, so routing its output into model input is a type error, not a convention.
- Every utterance reaches the raw transcript before any stage runs.
- The two speaker-labelling copies merge into one stage.

## Rejected options

- Transcript pipeline as its own core layer (entries 12/13): superseded by entry 22; it is STT-near processing and belongs with the engines.
- Correction feeding the agent: a corrected text is model output; feeding it back is a prompt-injection path.
