# Gap analysis — today's code vs. the target architecture

Compares the code at `fe3351d4` with [target.md](target.md), one section per
layer. Every number below was measured with the command shown next to it
(run from the repository root). A figure that was **not** measured is
marked `ASSUMPTION:` and says what to check. Line counts are `wc -l`
newline counts; "refs" are `grep -c` line matches, not call graphs.

The file-level mapping is in [blocks-inventory.md](blocks-inventory.md).

## Summary

| Layer | Today | Target | Gap |
|---|---|---|---|
| STT API | two paths: SSE server `stt/` (meeting, Kollektivgedächtnis) and direct Speechmatics in `workshop/hub.py` (workshop) | one STT API, two transports, same engines | M |
| STT engines | 7 engines in the registry, defaults of the live paths are engines to drop | Speechmatics EU + optional local Whisper | S in code, gated in operation |
| Transcript pipeline | thought end, correction, speaker labels only on the meeting path, wired by hand in `gateway/consumer.py` | stages inside the STT module, switchable per instance | L |
| Agent API | `AgentPort` exists and runs the workshop and the meeting's coach lane on request; the meeting's main turn runs an in-process Hermes worker that edits files itself | every agent call over `AgentPort`, agent returns content, blocks write | L |
| Surface API | none: meeting writes `buehne.json` and `stage.py` renders it; the hub pushes 25 private message types to its own pages | blocks emit view intents, surfaces render, user actions come back | L |
| Block contract | none; module seam v1 (`shared/modules.py`, 169 lines) is loaded only in tests | small fixed block API, blocks in their own directories | M |
| Phase engine | three private ones (hub steps/cues, InScribe phases, Kollektivgedächtnis cycle) | one core phase engine with history | M |
| Templates | none; the meeting method is `gateway/consumer.py` `main()` (1559 lines) | Reflect, one-day workshop, InScribe long format, Kollektivgedächtnis as template files | M |
| Instance API | `shared/instance.py` (142 lines), modules list only | + template, block config, STT pipeline switches, surfaces | S |

## 1. STT API: two paths today

**Meeting path (SSE server).** The recording machine runs
`python -m fundusapps.stt_server <engine>` (`stt/`, 33 source files, 8046
lines, measured with
`python3 -c "…git ls-files 'stt/*.py' minus /tests/…"`). It serves `/events`
(server-sent events). `gateway/consumer.py` reads that stream itself:
`parse_sse` (L1141) and `sse_events` (L1194). Kollektivgedächtnis reads the
same wire with its own client, `kg/stt_client.py` (75 lines) + `kg/sse.py`
(27). So the SSE `/events` wire is already a de-facto STT API with two
independent clients.

**Workshop path (direct).** `workshop/hub.py` opens one Speechmatics
realtime stream per table, in-process:
`default_stream_factory` (`workshop/hub.py:669`) returns
`SpeechmaticsStream(cfg.stt_key, language, speakers, …)` (`:674`), imported
from `workshop/stt_speechmatics.py:46-51`, which re-exports
`shared/speechmatics.py` (323 lines, `class SpeechmaticsStream` at `:181`).
Browser audio arrives over the phone socket (`Table.on_binary`,
`workshop/hub.py:4811`). The STT server is not involved:
`git grep -n -E "correction|thought_end|smart_turn|fundusapps" -- workshop/hub.py`
prints nothing (measured 2026-11-20 at `dd8637c6`).

**What is already shared.** One Speechmatics client:
`stt/fundusapps/stt_server/backends/speechmatics_rt_backend.py:66` imports
`SpeechmaticsStream` from `shared.speechmatics` (repo-split task 4.2). The
engine code is shared; the API in front of it is not.

**Gap.** No Python-level STT interface. The hub depends on the
`SpeechmaticsStream` surface (`start`, `send_pcm`, `events`, `close` — named
in `stt/fundusapps/stt_server/tests/test_speechmatics_rt_backend.py:85`),
the consumer on raw SSE dicts, Kollektivgedächtnis on its own
`TranscriptionEvent`. Target: `shared/stt.py` with one event type that *is*
the SSE wire (additive only) and two transports (in-process for per-table
browser audio, SSE for a remote recorder).

## 2. Engines to drop (decision 25)

Registry: `stt/fundusapps/stt_server/backends/__init__.py` (58 lines)
knows `vosk` (eager import, L4), `whisper`, `whisper-streaming`,
`elevenlabs-scribe`, `infomaniak-whisper`, `speechmatics-rt` (L47-58).

| Drop | Files | Lines |
|---|---|---|
| Vosk | `backends/vosk_backend.py`, `stt_server/test_vosk.py` | 78 + 88 |
| whisper-streaming | `backends/whisper_streaming_backend.py`, `backends/_vendored_whisper_streaming/` (2 files) | 303 + 980 |
| ElevenLabs Scribe (US) | `backends/elevenlabs_scribe_backend.py` | 683 |
| Infomaniak Whisper | `backends/infomaniak_whisper_backend.py` | 475 |
| workshop fallback (Infomaniak) | `workshop/stt_fallback.py` (imported at `workshop/hub.py:110`) | 223 |
| **total** | | **2830** |

Keep: `backends/speechmatics_rt_backend.py` (277), `backends/whisper_backend.py`
(178, local faster-whisper).

**Operational catch (measured).** The engines to drop are the *defaults* of
two live paths:

- online meetings: `scripts/stt-vserver.sh:237` sets
  `BACKEND=infomaniak-whisper`; `--speechmatics` (`:247`) is opt-in.
  `docs/operations.md:101` documents Infomaniak as the backend.
- meeting room: `docs/operations.md:36` starts
  `elevenlabs-scribe`. ASSUMPTION: that is what the recording machine runs
  today — check on the machine with Birk; agents do not touch it.

So "drop engines" is two steps: Birk switches the defaults to
`speechmatics-rt` (and redeploys the recording machine himself, following
`docs/stt.md`), *then* the code goes. Deleting first would break the next
start of an online meeting. Tests that pin the dropped engines:
`stt/fundusapps/stt_server/tests/test_event_fields.py:13`,
`test_backend_registry.py`, `test_whisper_streaming_vendored_import.py`,
`tests/test_workshop_fallback.py`.

## 3. Transcript pipeline: meeting path only

| Stage | Code | Lines | Meeting | Workshop |
|---|---|---|---|---|
| thought end (audio, smart-turn) | `stt/fundusapps/stt_server/thought_end.py` | 514 | yes (server flag `--thought-end`) | no |
| thought end (text, Laya) | `gateway/thought_end.py`, `thought_end_live.py`, `thought_end_laya_worker.py`, `smart_turn_worker.py` | 1080 | yes (`consumer.py:93` `ThoughtEndLane`) | no |
| correction | `gateway/correction.py`, `correction_prefilter.py` | 858 | yes (`consumer.py:58-60`) | no |
| speaker labels | `stt/pipeline/speaker_labels/enroll.py`, `split.py`, `label.py`, `label_llm.py` | 1566 | yes (lazy imports `consumer.py:3569`, `:3592`, `:3622`, `:3631`) | own copy |
| speaker labels (workshop copy) | `workshop/role_enroll.py`, `text_anchor.py`, `question_readout.py`, `enrollment.py`, `label_filter.py` | 1167 | no | yes (138 matching lines in `workshop/hub.py`) |

`grep -c "correction\|thought_end\|smart_turn" workshop/hub.py` → **0**.
`grep -c "correction\|thought_end\|speaker_label\|speaker_split\|speaker_enroll" gateway/consumer.py` → **69**.

The consumer wires every stage by hand. The correction's safety property
holds today and must survive the move: its output goes only to its own file
(`transkript-korrigiert.md`) and never back into the agent's input
(prompt-injection safety; `gateway/correction.py` module docstring).

**Gap.** Stages live in `gateway/`, are not pluggable, have no common
interface and are not available to the workshop or to Kollektivgedächtnis.
Speaker labelling exists twice (1566 vs. 1167 lines).

## 4. Agent API

`shared/agent.py` (387 lines) has `AgentPort` (`:91`: `open_session`,
`send_turn`, `stop`, `close`), `FakeAgentPort` (`:102`) and
`HermesApiAgentPort` (`:236`, Hermes API server over HTTP).

Users of `AgentPort` today (`git grep -n "AgentPort\|HermesApiAgentPort"`):
`workshop/hub.py:6716`, `workshop/poster.py:1009`,
`workshop/sort_themes.py:457`, `workshop/setting_check.py:275`,
`workshop/poster_agent.py:87`, `workshop/hooks.py:156`, and on the meeting
side `gateway/agent_lane.py:241` — only when `COTHINKER_AGENT=api`
(`gateway/consumer.py:2470`), and then only the coach lane; the call lane
needs `COTHINKER_AGENT_CALL=api` too (`:2481`).

The meeting's **main turn** always runs `gateway/worker.py` (576 lines) as
a child process (`WorkerClient`, `gateway/consumer.py:3800-3808`), which
imports Hermes in-process (`from run_agent import AIAgent`,
`gateway/worker.py:427-429`) and calls `agent.run_conversation()` (`:554`).

**The real gap is not the transport.** The main turn's prompt
(`gateway/consumer.py` ~L285-390, German) tells the agent to *edit*
`protokoll.md` itself (append `## Verlauf` bullets, fill
`## Entscheidungen` / `## Aufgaben` / `## Offene Fragen`) and to append
JSON lines to `vorschlag.jsonl`. It needs file tools on the machine that
holds the vault folder. `gateway/consumer.py:2467-2469` says so for the
call lane: "the remote agent cannot see this machine's project folder (no
file tools here)". Moving the main turn onto `AgentPort` therefore first
needs the turn to **return** its content (structured output) and a block to
write it. That is the same cut the block contract needs (capture bullets,
decisions / tasks / open questions, minutes document) — which is why the
plan does the block cut before the transport switch.

Must survive: the output-text check `FAILURE_MARKERS` in `gateway/worker.py`
(AGENTS.md pitfall: `hermes -z` exit 0 on HTTP 404). On the `AgentPort` path
the same check has to run on the returned text.

## 5. Surface API

There is none. What exists:

- **Meeting → stage.** The consumer is the only writer of `buehne.json`
  (`StageWriter`, `gateway/consumer.py:2517-3190`, 674 lines). The agent
  proposes stage changes as 11 action types in `vorschlag.jsonl` (prompt
  `gateway/consumer.py:318-337`: mirror, headline, decision, notice,
  question, resolve, main, topic, graph, links, none). `stage/stage.py`
  (2953 lines) only reads (`BUEHNE_JSON`, `:96`) and serves `do_GET`
  (`:2624`); there is **no write endpoint** (`grep -n "do_POST" stage/stage.py`
  → nothing). Stage state model: `gateway/stage_state.py` (1618 lines).
- **Workshop → own pages.** `workshop/hub.py` pushes JSON messages over
  websockets to its own pages (`workshop/static/*.html|js`): 102 emit call
  sites (`grep -c "\.emit(\|\.emit_to(\|\.emit_threadsafe(" workshop/hub.py`),
  25 distinct message types in `.emit("<type>"` form
  (`grep -ohE '\.emit\("[a-z_]+"' workshop/hub.py | sort -u`), e.g. `step`,
  `seats`, `turn`, `words`, `status`, `speak_queue`, `talk_share`,
  `room_mode`. User actions come back as socket text (`Table.on_text`,
  `:3320`) and desk controls (`_on_desk_control`, `:3460`). Page code:
  `table.js` 4151, `wall.js` 5027, `floor.js` 1100, `desk.js` 328 lines.
- **InScribe** has its own web stack (27908 lines mapped to the InScribe web
  app surface), **Kollektivgedächtnis** its own servers (`kg/server.py`,
  `kg2/server.py`).

**Gap.** Blocks and page code are coupled through private message types.
Intent kinds the target needs that have no equivalent on the stage today:
timer, speaker queue, speaking share, choice list. The stage cannot receive
user actions at all, so Reflect's speaker queue needs another input
(voice command first; see gate list in the plan).

## 6. Block contract, Module API, phase engine, templates

`shared/modules.py` (169 lines, `CORE_API_VERSION = 1`) defines `ToolSpec`,
`Phase(id, titles)`, `CoreEvent(kind, payload)`, `StateStore` (read/write
only), `ModuleContext(instance, state, agent)` and the `Module` protocol
(`tools()`, `phases()`, `profile_dir()`, `on_event()`). The only module is
`modules/example_echo/module.py` (44 lines), loaded only by tests (InScribe
draft gap G3). The InScribe port draft lists 15 seam gaps G1-G15 against this
file; the plan maps each to the task that closes it.

Phase logic exists three times, privately:

- workshop: step and cue machinery inside `workshop/hub.py`
  (`enter_step` `:2329`, `_desk_next_step` `:3624`, cue pointer / actuals
  files `:467-595`, `cue_advance` `:7266`, `cue_back` `:7342`, `cue_reset`
  `:7389`) + `workshop/mic_off.py` (112);
- InScribe: `phasen.py` 617, `phasentexte.py` 593, `roadmap.py` 781,
  `fehlstellen.py` 387, `knoepfe/szenen.py` 2116, `knoepfe/stationen.py` 546;
- Kollektivgedächtnis: `kg/pipeline.py` 105, `kg2/cycle.py` 216,
  `trigger.py` 196, `watcher.py` 118.

The meeting has no phases; its agenda (`AgendaController`,
`gateway/consumer.py:1037`) ticks agenda items.

**Templates.** The meeting "method" is `gateway/consumer.py` `main()`
(`:3259` to end, 1559 lines), which imports 23 local modules at the top
(`gateway/consumer.py:57-105`). Reflect as a template means this wiring
becomes data (`templates/reflect/template.toml`) plus a block host.

## 7. `workshop/hub.py` by concern (8038 lines)

Measured from the `def`/`class` line numbers
(`grep -n "^class \|^def \|^    def \|^    async def " workshop/hub.py`,
318 methods). A range runs from one `def` line to the next, so comments
between methods count to the preceding concern.

| Lines | Concern | Count | Target |
|---|---|---|---|
| 1-201 | imports, constants | 201 | — |
| 202-596 | phase/cue/room state files (ablauf, cue pointer, actuals, room mode, seat QR gate) | 395 | phase engine + timer |
| 597-739 | HTTP helpers, STT stream / enroll / fallback factories | 143 | surface + STT API |
| 740-1173 | `Table.__init__` | 434 | split with its users |
| 1174-2240 | seats, roles, turn order, turn start / done | 1067 | block interview round |
| 2241-2318 | emit plumbing | 78 | Surface API |
| 2319-2539 | step machine (enter / restore / leave role finding) | 221 | phase engine |
| 2540-2695 | setting round (block hand-off, setting answer) | 156 | block setting round |
| 2696-3027 | discussion evaluation, terms routing | 332 | blocks condense + capture terms |
| 3028-3113 | contrast pairs, echo | 86 | blocks contrast pairs + capture bullets |
| 3114-3181 | setting check | 68 | block setting round |
| 3182-3792 | sockets, `on_text`, desk controls, step navigation | 611 | surface workshop web app + phase engine |
| 3793-4039 | intro round, STT check | 247 | block intro round |
| 4040-4118 | interview reset / rewind | 79 | block interview round |
| 4119-4241 | probe demo | 123 | surface (operator tooling) |
| 4242-4523 | desk operations (reset, retry poster, roles, pass turn, start wall) | 282 | surface workshop web app |
| 4524-4718 | buttons, apply effects, desk state | 195 | surface workshop web app |
| 4719-4810 | speak queue and mic gate | 92 | block speaker queue |
| 4811-5255 | audio intake, enrollment, voiceprints, role binding | 445 | STT module (audio + speaker labels) |
| 5256-5788 | STT events, utterances, finalize | 533 | STT API client + capture bullets dispatch |
| 5789-5837 | feedback round | 49 | block feedback round |
| 5838-6273 | mic holder / speaker resolution | 436 | STT speaker labels |
| 6274-6393 | STT fallback (Infomaniak) | 120 | retire (decision 25) |
| 6394-6500 | tick, close, lifecycle | 107 | core session |
| 6501-6642 | speak taps | 142 | block speaker queue |
| 6643-6772 | `TableHub.__init__`, lazy embed client | 130 | split with its users |
| 6773-6986 | room seats, speaker list, room talk time | 214 | blocks speaker queue + speaking share |
| 6987-7123 | room mode, go, steps, seat QR gate | 137 | phase engine |
| 7124-7192 | room look, probe mode | 69 | External Display |
| 7193-7302 | cue clock, drift, projected end | 110 | block timer |
| 7303-7498 | cue back / reset, scene reset | 196 | phase engine |
| 7499-7868 | start / stop, HTTP routes, sockets, tick loop | 370 | surface + core session |
| 7869-8038 | `main()` | 170 | core session |

Rough totals: STT-related 1677 (incl. 120 retiring), phase engine ~1170,
surface ~1900, blocks ~2300, core/plumbing ~1000.

## 8. `gateway/consumer.py` by concern (4817 lines)

| Lines | Concern | Target |
|---|---|---|
| 1-787 | imports, German prompt templates, protocol header | template Reflect (prompts as block config) |
| 788-1036 | project / session paths, flat-file rule | core session |
| 1037-1140 | `AgendaController` | phase engine / timer config |
| 1141-1207 | SSE client | STT API client |
| 1249-1689 | `Protocol` (append-only protocol writer) | block minutes document |
| 1690-2073 | turn building (`build_prompt`, policy turns, call-out pages) | blocks capture bullets, decisions, mirroring |
| 2074-2213 | `RunQueue` — **not touched** (AGENTS.md) | stays in core session |
| 2214-2449 | stage helpers, call-out settlement | surface stage |
| 2450-2516 | `build_lanes` | Agent API + block mirroring |
| 2517-3190 | `StageWriter` (sole writer of `buehne.json`) | surface stage (stays the sole writer) |
| 3191-4817 | `restore_stage`, `main()` | core session + template host |

## 9. Built more than once

From [blocks-inventory.md](blocks-inventory.md):

| Function | Copies | Code |
|---|---|---|
| capture bullets | 2 | meeting main turn (`## Verlauf`, no own file) · `workshop/bullets*.py` + `echo.py` (2257 lines) + prompts `notes_live|final` |
| capture terms | 3 | `workshop/terms.py` 844 · InScribe `begriffe*.py`/`begriffsboard*.py` 1237 · `kg/extraction.py` 178 |
| merge terms | 2 | `workshop/term_merge.py` 389 · `kg/merging.py` 262 + `kg/embeddings.py` 227 |
| condense | 3 | workshop 1010 · InScribe 906 · Kollektivgedächtnis 1326 |
| graph | 3 | `stage/session_graph.py` 389 · `workshop/results_graph.py` 431 · `kg/export.py` + `kg2/graph_client.py` 196 |
| speaker labels | 2 | gateway 1566 · workshop 1167 |
| interview round | 3 | workshop 863 · InScribe 517 · `kg/session.py` 207 |
| image generation, release, write text, dramaturgy, interview questions, status line, decisions | 2 each | see inventory |

## 10. Only in the workshop today (Reflect gains them)

- **Timer**: no file of its own. Server side is the cue clock inside
  `workshop/hub.py` (`load_cue_sheet_planned_minutes` `:572`,
  `planned_minutes_cumulative` `:6742`, `drift_s` `:7237`,
  `projected_end_ms` `:7255`); 52 lines of `hub.py` match `timer`
  (`grep -ci timer workshop/hub.py`). Pages: `workshop/static/timer.js` 65,
  `wall-hint-timer.js` 157, `cue-sheet-data.js` 224. `gateway/timing.py`
  (207) is a latency trace, not a timer: the meeting has no timer.
- **Speaker queue**: `workshop/speak_queue.py` 83 (pure, clock injected);
  hub wiring `:4719-4810`, `:6501-6642`, `:6773-6986`; page
  `workshop/static/speak-queue.js` 200.
- **Speaking share**: `workshop/talk_share.py` 148 (words per person from STT
  finals, no model) and `workshop/room_talk_time.py` 108 (holder time from the
  speaker queue, no STT). Both pure.

Files that use any of the three
(`git grep -l "talk_share\|room_talk_time\|speak_queue\|SpeakQueue\|TalkShareTracker" -- "*.py"`
in `workshop gateway stage shared`): `workshop/{talk_share,room_talk_time,speak_queue,hub,hub_config,ops_state,ops_metrics,vorstellen,wall}.py`
— nothing in `gateway/` or `stage/`.

All three are already pure and clock-injected, so moving them into blocks is
small; the work is the meeting-side input (who wants to speak, who is
speaking) and the stage rendering.

## 11. Instance API

`shared/instance.py` (142 lines): `InstanceConfig` with `content()`,
`profile()`, `acceptance()`, a `modules` name list, and `load()` /
`validate()` / `env_view()` / `getenv()`. Missing for the target: which
template an instance runs, per-block configuration (`[blocks.<id>]`), STT
engine and pipeline switches (`[stt]`), attached surfaces (External
Display on/off), per-session language (InScribe gap G13), per-module
configuration (G9).

## 12. Assumptions in this document

- ASSUMPTION: the recording machine in the meeting room runs
  `elevenlabs-scribe` (from `docs/operations.md:36`). Check with Birk; do not
  log in to check.
- ASSUMPTION: no caller outside `infomaniak-whisper` depends on the
  `--word-times` word timestamps (`scripts/stt-vserver.sh:287-291`, an
  infomaniak-only flag). Dropping the engine drops the feature. Check:
  `git grep -n "\"words\"\|\.words\b" -- gateway stage` before task F.4 of the
  migration plan.
- ASSUMPTION: repo-split tasks 6.5–6.7 (positive list, public docs, export)
  are not on `main` yet (`ls publish/` shows no `public-paths.txt` at
  `dd8637c6`); they may be running on other branches. Check:
  `git log --oneline origin/main -- publish/public-paths.txt`.

## 13. Wire detail the engine drop must respect

`TranscriptionEvent.backend` (`stt/fundusapps/stt_server/events.py:14-15`)
is a `Literal` that names all six engines. The wire is additive only
(AGENTS.md): dropping an engine removes its adapter, never the value from
the type — an older consumer may still parse old recordings that carry it.
`tests/known_red.json` is an empty list at `dd8637c6`
(`python3 -c "import json;print(len(json.load(open('tests/known_red.json'))))"`
→ `0`), so "0 new" in the plan means no failing test at all.
- ASSUMPTION: a Hermes API server running on the vault machine would have
  file tools there; the plan does not rely on it (blocks write, not the
  agent). Check: `shared/profiles/skel/main/config.yaml` `enabled_toolsets`
  and the server's profile.
