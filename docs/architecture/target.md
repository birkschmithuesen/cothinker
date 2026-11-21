# CoThinker target architecture

Status: accepted as a concept by Birk on 2026-11-20; the individual decisions
are recorded as ADRs under [`docs/adr/`](../adr/README.md) with status
`proposed` until each one is accepted. This document describes the shape and
the interfaces; it does not repeat the reasoning — each section names the ADR
that carries it.

Companion documents:

- [blocks-inventory.md](blocks-inventory.md) — every source module of today's
  four applications mapped to exactly one block, STT module part, phase
  engine, surface or core API (machine-checked).
- [gap-analysis.md](gap-analysis.md) — today's code against this target,
  measured per layer.
- Diagrams: [arch-en-v3.png](arch-en-v3.png) (layers and APIs),
  [stt-module-en.png](stt-module-en.png) (STT module internals),
  [blocks-inventory.png](blocks-inventory.png) (block inventory).

Interface sketches below are Python `Protocol`/`dataclass` shapes. They fix
names, directions and ownership; field lists are the minimum and grow
additively.

## 1. Layers (ADR 0001)

```
 instance            instance.toml + content + profiles     private repo per instance
 ───────────────────────────────────────────────────────────────────────────────────
 templates           Reflect · workshop (one-day) · InScribe (long) · Kollektivgedächtnis
                     = blocks + phases + rules, no code
 blocks              capture bullets · condense · timer · speaker queue · …
 surfaces            stage · workshop web app · InScribe web app · External Display
 STT module          engines + transcript pipeline, behind the STT API
 ───────────────────────────────────────────────────────────────────────────────────
 core                five APIs · phase engine · block host · session runtime
```

- **Core** (public repository): the five APIs (§2), the phase engine (§4),
  the block host that runs a template's blocks, and the session runtime
  (session folder, event bus, traces, sync safety). The core contains no
  method logic.
- **Modules** are the packaging unit for everything that plugs into the core:
  blocks, templates, surfaces and STT engines. A module may live in the core
  repository or in its own repository (licence per ADR 0014).
- **Instance**: configuration and content for one deployment (which
  template, which blocks configured how, which surfaces, which STT engine and
  pipeline stages). Instances live in private repositories (ADR 0015).

A session is an episode of an instance, not its lifespan: state that spans
sessions (an InScribe piece across three workshop days, a Kollektivgedächtnis
graph across two days) belongs to the instance's project, not to the session.

## 2. The five core APIs (ADR 0002)

| API | Direction | Who implements | Who calls |
|---|---|---|---|
| STT API | audio → transcript events | STT module | block host |
| Agent API | prompt → reply events | adapters (Hermes API server first) | blocks |
| Surface API | view intents out, user actions in | surfaces | block host, phase engine |
| Module API | load, version, describe | modules | core loader |
| Instance API | read configuration and content | core | everything |

### 2.1 STT API (ADR 0003, ADR 0004)

One event type that *is* today's SSE wire (`stt/fundusapps/stt_server/events.py`,
additive only — a field is never renamed or removed), and two transports.

```python
@dataclass(frozen=True)
class TranscriptEvent:               # one line of the /events wire
    type: str                        # "partial" | "final" | "thought_end" | "mic_gate"
    recognizer_id: str
    timestamp: float
    text: str = ""
    turn_id: str | None = None
    t_start: float | None = None     # audio clock, seconds
    t_end: float | None = None
    audio_epoch: float | None = None
    speaker: str | None = None       # set by the speaker-labels stage
    channel: int | None = None
    backend: str | None = None       # engine name; old values stay valid on the wire

class SttSource(Protocol):           # what every consumer sees
    def events(self) -> Iterator[TranscriptEvent]: ...
    def close(self) -> None: ...

class SttAudioSession(SttSource, Protocol):   # in-process transport, audio pushed by the caller
    def send_pcm(self, pcm16k: bytes) -> None: ...

def open_stt(cfg: "SttConfig") -> SttSource: ...
# cfg.transport == "sse":       remote STT server (a recording machine), reads /events
# cfg.transport == "inprocess": engine runs in the caller's process (per-table browser audio)
```

Engines (ADR 0003): **Speechmatics EU** (realtime, diarization) and,
optionally, **local Whisper** (offline). No other engine. No automatic
fallback between engines; switching is a deliberate operator action.

### 2.2 Agent API (ADR 0005)

Today's `shared/agent.py` `AgentPort` is the API; the meeting's in-process
worker moves onto it.

```python
class AgentPort(Protocol):
    def open_session(self, context_id: str, briefing: str) -> Session: ...
    def send_turn(self, session: Session, text: str, attachments: list | None = None,
                  model: str | None = None) -> Iterator[Event]: ...
    def stop(self, session: Session) -> None: ...
    def close(self, session: Session) -> None: ...
# Event.kind in {"text_delta", "tool_started", "tool_finished", "subagent_started",
#                "subagent_finished", "thinking", "done", "failed"}
```

Rules on top of the transport:

- The agent **returns** content; a block writes it. No agent edits a session
  file itself (the meeting's main turn does today — see gap analysis §4).
- The core adapter checks the returned text for failure markers
  (`API call failed`, `HTTP 4`) in addition to the structured `failed` event;
  a run that fails this check counts as failed.
- How module tools reach the agent (MCP server per session vs. skill scripts)
  is open — see the plan's gate list.

### 2.3 Surface API (ADR 0006, ADR 0007)

Blocks and the phase engine emit **view intents**; surfaces render them and
send **user actions** back. A surface never calls a block, a block never
writes a surface's file.

```python
INTENT_KINDS = {"prompt", "choice_list", "timer", "status", "artefact"}   # decision 15
# proposed additions (gate list): "queue" (speaker queue), "share" (speaking share), "graph"

@dataclass(frozen=True)
class ViewIntent:
    id: str                    # stable per block instance; a new value replaces the old one
    block: str                 # emitting block id, e.g. "timer"
    kind: str                  # one of INTENT_KINDS
    audience: str              # "room" | "person:<id>" | "operator"
    payload: Mapping[str, object]
    expires_at: float | None = None

@dataclass(frozen=True)
class UserAction:
    intent_id: str
    kind: str                  # "choose" | "accept" | "reject" | "edit" | "tap" | "voice"
    person: str | None
    payload: Mapping[str, object]
    at: float

class Surface(Protocol):
    name: str
    kinds: frozenset[str]      # intent kinds this surface can render
    def render(self, intents: Sequence[ViewIntent]) -> None: ...
    def actions(self) -> Iterator[UserAction]: ...
```

- An intent kind a surface cannot render is dropped for that surface and
  logged; it never fails the block.
- "Talk on" is a user action too: a voice command is a `UserAction` with
  `kind="voice"`, so everything reachable by tap is also reachable by speech.
- The stage keeps its single-writer rule: the session host is the only
  writer of `buehne.json`; the stage surface renders from it.
- **Coupling rule (ADR 0007, OPEN):** blocks reach surfaces only through the
  Surface API; a template *may* ship its own surface extension (an extra
  renderer for an intent kind). Not confirmed by Birk.

### 2.4 Module API (ADR 0002, ADR 0008)

Today's `shared/modules.py` (`CORE_API_VERSION = 1`) is the seed. Target:

```python
# module.toml
# [module]  name = "reflect-blocks"  api_version = 2  entry = "__init__:Module"
# provides = ["block:capture-bullets", "template:reflect", "surface:stage"]

class Module(Protocol):
    name: str
    api_version: int
    def blocks(self) -> list["BlockFactory"]: ...
    def templates(self) -> list[Path]: ...       # template.toml files
    def surfaces(self) -> list["SurfaceFactory"]: ...
    def profile_dir(self) -> Path | None: ...     # agent profile parts (SOUL sections, skills)
```

A module is loaded only through `api_version` match; a mismatch is an error
at start, never at first use.

### 2.5 Instance API (ADR 0001)

Today's `shared/instance.py` (`InstanceConfig`, `instance.toml`) grows
additively:

```toml
[instance]   name = "example"  languages = ["de", "en"]
[template]   name = "reflect"                 # which template this instance runs
[stt]        engine = "speechmatics-eu"       # or "whisper-local"
             transport = "sse"                 # or "inprocess"
             stages = ["thought-end", "speaker-labels", "correction"]
[surfaces]   stage = true  external_display = false
[blocks.timer]   default_minutes = 60          # per-block overrides of the template
[modules]    search = ["modules", "../inscribe"]
```

`InstanceConfig.block_config(id) -> Mapping` and
`InstanceConfig.module_config(name) -> Mapping` read the tables above.

## 3. STT module internals (ADR 0003, ADR 0004)

Diagram: [stt-module-en.png](stt-module-en.png).

```
audio source ─► audio stage ─► engine ─► transcript pipeline ─► STT API (TranscriptEvent)
(mic, BBB tap,   (clock, VAD,   Speechmatics EU   thought end ─► speaker labels      │
 browser PCM)     mic gate)     | local Whisper   ─► correction (display only) ──────┘─► display sink
```

- **Pipeline stages** are pluggable and switched per instance (`[stt]
  stages`): thought end (audio smart-turn or text-based), speaker labels
  (diarization label → name; enrollment), correction.
- **Correction is display-only.** Its output goes to a display sink (its own
  file, a surface intent) and **never** back into the event stream that blocks
  and the agent consume. This is a prompt-injection boundary: a corrected text
  is model output and must not become model input. The type system enforces
  it — the correction stage returns `CorrectionNote`, not `TranscriptEvent`.

```python
class TranscriptStage(Protocol):
    name: str
    def feed(self, ev: TranscriptEvent) -> Iterable[TranscriptEvent]: ...

class DisplayStage(Protocol):            # correction
    name: str
    def feed(self, ev: TranscriptEvent) -> Iterable["CorrectionNote"]: ...
```

- Every utterance reaches the raw transcript before any stage runs; a stage
  crash never costs raw material.
- Both transports share engines and stages: the SSE server runs the pipeline
  before publishing; the in-process transport runs the same stage objects in
  the caller's process.

## 4. Phase engine (ADR 0009)

The core runs a template's phases. A phase says which blocks run, with which
prompts and rules, on which surfaces.

```python
@dataclass(frozen=True)
class PhaseSpec:
    id: str
    titles: Mapping[str, str]          # language -> title
    blocks: tuple[str, ...]            # block ids active in this phase
    surfaces: tuple[str, ...]
    goal: Mapping[str, str] = {}
    enter_when: tuple[str, ...] = ()   # rule ids, e.g. "operator", "voice:next", "timer:elapsed"
    exit_when: tuple[str, ...] = ()

class PhaseEngine(Protocol):
    def current(self) -> PhaseSpec: ...
    def advance(self, to: str, cause: str) -> None: ...   # cause recorded in history
    def back(self) -> None: ...                           # reverses the last advance
    def history(self) -> Sequence["PhaseChange"]: ...
```

- **Every transition is recorded** (`phase-history.jsonl` in the session
  folder: from, to, cause, time) and **reversible** with `back()`. No
  automatic switch without that history.
- The engine may advance on its own (a rule fires); not advancing needs a
  reason, not advancing.
- Phase changes are core events every block receives.

## 5. Building blocks (ADR 0008)

Everything that does work is a block. A block has a **small fixed external
API**; its internals stay as they were written for their first user and are
not generalised ahead of a second one.

```python
@dataclass
class BlockContext:
    instance: InstanceConfig
    session_dir: Path
    state: StateStore                 # block-private, atomic write, update under lock
    agent: AgentPort | None
    llm: "LlmClient | None"
    language: str                     # the session's output language
    clock: Callable[[], float]        # injected, so blocks are testable
    emit: Callable[[ViewIntent], None]
    artefact: Callable[[str, str], None]   # (name, text) -> written by the host

@dataclass(frozen=True)
class CoreEvent:
    kind: str     # "utterance" | "segment" | "phase" | "action" | "tick" | "block_output"
    payload: Mapping[str, object]

class Block(Protocol):
    id: str
    api_version: int
    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None: ...
    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]: ...   # may fan out to other blocks
    def stop(self) -> None: ...
```

- Blocks talk to each other only through `CoreEvent`s the host routes
  (`block_output`), never by import.
- A block writes only through `ctx.artefact` and `ctx.state`; the host owns
  the session folder (single writer per file).
- Inventory of blocks and their sources: [blocks-inventory.md](blocks-inventory.md).

## 6. Templates (ADR 0009)

A method is a template: blocks + phases + rules, no code of its own.
Anything unique to a method (the InScribe piece model, dramaturgy checks) is
a block.

```toml
# templates/reflect/template.toml
[template]
name = "reflect"
api_version = 1
surfaces = ["stage"]
optional_surfaces = ["external-display"]
rules = ["grounding", "add-dont-restructure"]

[[blocks]]
id = "capture-bullets"
prompt = "prompts/capture-bullets.{lang}.md"

[[blocks]]
id = "timer"

[[phases]]
id = "session"
blocks = ["capture-bullets", "topics", "decisions-tasks-questions", "condense",
          "minutes-document", "mirroring-coach", "knowledge-link", "timer",
          "speaker-queue", "speaking-share", "status-line", "graph"]
surfaces = ["stage"]
```

**Rules every template carries:**

- `grounding` — the agent states facts about the system, the material and
  the saved state only from block or tool results, never from its own
  memory of the conversation.
- `add-dont-restructure` — while the room reads along, a new insight adds a
  line; it never reorders what is shown.

## 7. Surfaces (ADR 0006, ADR 0012)

| Surface | Today | Renders |
|---|---|---|
| stage (meeting web view) | `stage/stage.py` + `gateway/stage_state.py` | intents of Reflect; read-only for viewers |
| workshop web app (one-day format) | `workshop/hub.py` + `workshop/static/` | phones, operator desk |
| InScribe web app (long format) | InScribe's own web stack | phases, chat, workbench |
| External Display | `workshop/wall*.py`, floor pages; Kollektivgedächtnis screens | projection or screen, attachable to any template |
| guest view | `workshop/conference_app.py`, public mirror | read-only |

The final names of the two web apps are open (gate list). Telegram is not a
target surface (ADR 0013).

## 8. Instance examples

| Instance | Template | Surfaces | Notes |
|---|---|---|---|
| a meeting | Reflect (ADR 0010): 12 blocks, one phase | stage (+ External Display optional) | STT over SSE from a recording machine |
| Assistant (one-day workshop) | InScribe, one-day format (ADR 0011) | workshop web app, External Display (wall, floor) | STT in-process per table |
| Example-Instance (InScribe long format) | InScribe, long format (ADR 0011) | InScribe web app | 6–7 phases over several days |
| Kollektivgedächtnis | Kollektivgedächtnis: interview round → capture terms → merge terms → graph → condense to sentence + image generation | External Display (wall graph, dream screen), operator view | STT over SSE; lives in its own repository |

## 9. Safety invariants the target keeps

- The STT wire is additive only; a running recording machine is never
  restarted by the software.
- One writer per session file; the stage file has exactly one writer.
- No conversation text in the event trace (`gateway/events.py` filter).
- The raw transcript section of a minutes document is written only by the
  session host.
- No automatic fallback to a US service for speech recognition; text models
  follow the instance's explicit configuration.
- Correction output never flows back into model input (§3).

## 10. Proposed ADRs

| ADR | Title | Sections |
|---|---|---|
| [0001](../adr/0001-instance-core-module-layering.md) | Instance, core and module layering | §1, §2.5 |
| [0002](../adr/0002-five-core-apis.md) | Five core APIs | §2 |
| [0003](../adr/0003-stt-engines-speechmatics-eu-and-local-whisper.md) | STT module with Speechmatics EU and optional local Whisper only | §2.1, §3 |
| [0004](../adr/0004-transcript-pipeline-inside-stt-module.md) | Transcript processing inside the STT module | §3 |
| [0005](../adr/0005-agent-api-hermes-api-server.md) | Agent API via the Hermes API server, no in-process import | §2.2 |
| [0006](../adr/0006-surface-api-view-intents-user-actions.md) | Surface API: view intents out, user actions in | §2.3, §7 |
| [0007](../adr/0007-coupling-rule-blocks-surfaces.md) | Coupling rule between blocks and surfaces (open point) | §2.3 |
| [0008](../adr/0008-everything-that-works-is-a-block.md) | Building blocks with a small fixed API | §5 |
| [0009](../adr/0009-methods-are-templates.md) | Methods are templates; the core runs phases | §4, §6 |
| [0010](../adr/0010-reflect-first-template.md) | Reflect: name and first template, 12 blocks | §6, §8 |
| [0011](../adr/0011-inscribe-one-template-two-formats.md) | InScribe: one template, long and one-day format | §8 |
| [0012](../adr/0012-external-display-surface.md) | External Display as its own surface | §7 |
| [0013](../adr/0013-telegram-parked.md) | Telegram parked | §7 |
| [0014](../adr/0014-licence-agpl-cla-dco-modules-own-licence.md) | Licence: AGPL core, CLA and DCO; modules may carry their own licence | §1 |
| [0015](../adr/0015-public-repo-fresh-export-private-instances.md) | Public repository from a fresh export, private instance repositories | §1 |
| [0016](../adr/0016-public-repo-issues-and-prs.md) | Work in the public repository through issues and pull requests | — |
