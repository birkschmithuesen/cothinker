# CoThinker architecture

CoThinker's core does not depend on one particular agent framework or one
particular speech-to-text engine. Two seams make that possible: the **agent
seam** (the brain) and the **STT seam** (the ear). This document describes
both, the full interface map between the pieces, and the rule that decides
which transport each interface uses. The companion seams that make the core
independent of a specific deployment and of specific optional behaviour are
`docs/instance-format.md` and `docs/module-format.md`.

## 1. The agent seam

Status: decided (2026-10-22).

### Decision

CoThinker talks to its agent through **one thin interface of its own** — the
*agent seam*. Exactly one module per agent framework (an *adapter*) translates
that interface into the framework's own API. Nothing outside an adapter knows
which framework is behind it.

The first adapter talks to one agent framework through its API server: one
long-lived session per meeting. Other adapters are roadmap, not current work.

### The seam (contract, kept small on purpose)

```
open_session(context_id, briefing)          -> session handle
send_turn(session, text, attachments=None)  -> stream of events
stop(session) / close(session)
```

Normalised events, in the order a display needs them:

| Event | Meaning |
|---|---|
| `text_delta` | a piece of the answer |
| `tool_started` / `tool_finished` | the agent uses a tool (name, short label) |
| `subagent_started` / `subagent_finished` | a background helper runs |
| `thinking` | optional reasoning is available |
| `done` / `failed` | the turn finished, with token usage |

Rules:
- **Additive only**, like the STT wire contract below: new event kinds and
  fields are optional; nothing is renamed or removed. A consumer on the
  other side of a running meeting may be older than the adapter sending
  these events.
- Framework-specific extras enter the seam only deliberately, as an
  optional capability an adapter may report — never as a direct call into
  the framework from outside that one adapter.
- A **fake adapter** implements the same seam. The test suite and any
  simulator use it, so nothing needs a real agent running to exercise the
  rest of the system.

### Why a seam instead of calling the framework directly

| | Speak the framework's format directly | Thin seam + adapter (chosen) |
|---|---|---|
| Build effort now | lowest | a few hours more, once |
| Where framework knowledge lives | everywhere (runs, event names, sessions) | one adapter module |
| The framework renames an event | breaks in many places | fix one file |
| A second agent framework | rewrite across the program | one more adapter |
| Tests / simulator | need a running agent | fake adapter |

No shared standard for an agent's tool and helper event stream exists across
frameworks yet, so this seam is CoThinker's own. It is what makes a
standalone core with interchangeable agent adapters possible, instead of one
hard-wired dependency.

### Speed

- One long-lived session per meeting: the cold start happens once, before
  the event starts, not once per turn.
- A slim agent profile loads only the toolsets the current surface needs.
- Events are forwarded the moment they arrive; the seam never buffers a
  whole turn before passing anything on.
- Everything that must answer in under a second (a status line, running
  words, a quick lookup) never goes through the agent at all — it runs in
  plain code or through one direct, short-budget model call. The agent
  only receives work that can afford to take tens of seconds to minutes,
  and a surface stages that wait rather than leaving a blank space.

### Visible agent activity

Because tool and helper events travel through the seam, any surface can show
what the agent is doing right now (a tool running, a helper finished)
without any of the adapters needing to change.

## 2. The STT seam

Status: decided (2026-10-22), built in two steps.

Speech-to-text always reports to CoThinker, never directly to the agent: the
instant stages (a running name, running words, a quick summary) run in code,
and the agent wants a finished turn, not every intermediate word.

Contract — one normalised event stream:

| Event | Fields |
|---|---|
| `partial` | text, speaker label, start/end on the audio clock |
| `final` | same, plus a message id |
| `eou` | end of utterance (a sentence ends) |
| `error` | reason |
| `status` | `ok` / `degraded` / `fallback` |

Session surface: `start()`, `send_pcm(chunk)`, `events()`,
`restart(speakers)`, `close()`.

Adapters:
- A realtime speech API — the lowest-latency path, used where network
  conditions allow it.
- The bundled STT server (`stt/fundusapps/stt_server`): a Flask server with
  whisper, vosk, ElevenLabs and Infomaniak backends behind one `/events`
  server-sent-event stream — CoThinker Meeting's current path.
- A batch fallback, for when neither realtime path is available.

A fake STT implementation satisfies the same session surface, so a
simulator or test suite can exercise everything downstream of it without
any real audio or network dependency.

## 3. All interfaces

| # | From → to | Transport |
|---|---|---|
| I1 | sound → STT | WebSocket (binary PCM + JSON control), or an STT server's own intake |
| I2 | STT → CoThinker | STT seam (§2) |
| I3 | CoThinker → agent | agent seam (§1), HTTP + server-sent events |
| I4 | CoThinker → a fast model | direct HTTP call, for anything that must answer within a few seconds |
| I5 | CoThinker → displays | file-backed state polled every few seconds, or a pushed WebSocket event, depending on the surface |
| I6 | state on disk | one file per concern, exactly one writer each |
| I7 | → take-away | plain HTTP |

### Transport rule

- **Request/answer, or a stream in one direction** → HTTP (server-sent
  events for a stream). STT control, the agent, take-away.
- **A continuous stream in both directions to many clients** → WebSocket.
  Audio flows without pause and the server must push results without being
  asked; polling, or dozens of requests per second per client, would be
  both slower and heavier than one open connection.
- **Many such connections open at once** → an `asyncio`-based process, not
  a thread-per-connection server, which is the wrong shape for holding many
  mostly-idle open connections rather than handling short requests.

---

This document covers the two seams that are **decided**: the agent seam and
the STT seam. The instance seam (`docs/instance-format.md`) and the module
seam (`docs/module-format.md`) are the other two seams a deployment combines
with these to configure what the core does not hard-code — which content,
which optional behaviours, and which concrete adapters are wired in.
