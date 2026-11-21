# ADR 0003: STT module with Speechmatics EU and optional local Whisper only

- **Status:** accepted
- **Date:** 2026-11-20

## Context

Source: concept-round decision log 2026-11-20, entries 10, 11 and 25.

Two STT paths exist: an SSE server with an engine registry of six engines (`stt/`), and the workshop hub opening Speechmatics streams directly (gap analysis §1). Both already share one Speechmatics client (`shared/speechmatics.py`). The registry also carries Vosk, whisper-streaming, ElevenLabs Scribe and Infomaniak Whisper; the workshop has its own Infomaniak fallback. Together these are 2830 lines (gap analysis §2). Speech recognition must not switch to a US service silently.

## Decision

The STT module is the only speech recognition in CoThinker, reached through the STT API, and it has two engines: **Speechmatics EU** (realtime, diarization) and, optionally, **local Whisper** (offline). All other engines are removed. There is no automatic fallback between engines; a switch is a deliberate operator action.

## Consequences

- One STT API with two transports (SSE from a recording machine, in-process for per-table browser audio); the workshop moves onto it.
- Removing an engine removes its adapter, never a value from the wire: the `backend` field keeps accepting old names (additive-only wire).
- Live paths whose default is a removed engine (online meetings, meeting-room start command) are switched to Speechmatics EU by the operator first; the code goes after that. A running recording machine is never restarted by an agent.
- Features only one removed engine offered (word timestamps of Infomaniak Whisper) are dropped unless a kept engine provides them.

## Rejected options

- Keep the registry open for any engine: every engine is a data-protection review and a test surface; none of the removed ones is used by default after the switch.
- Automatic fallback to a second cloud engine: violates the "no silent switch" rule for speech.
