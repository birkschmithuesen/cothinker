# Building blocks — inventory of what the code does today

Every Python source module of this repository maps to **exactly one**
target: a building block, a part of the STT module, a surface, the core
runtime behind the five APIs (see [target.md](target.md)), or `retire`.
The machine-checked mapping is the fenced `inventory` block at the end of
this file; the tables below are generated from it and add line counts.

**Used by:** `core` = part of the single code base this repository holds
today. `Reflect` = one of the 12 blocks confirmed by Birk for the Reflect
template (only 8 of those 12 have code in this repository today; the other
3 — condense, speaker queue, write text — are not
yet built here). `templates` = reserved for per-instance template files;
none exist in this repository yet. **Built** = how many separate source
files implement the target in this repository (always `1×` here — this
inventory counts one code base, not several).

## How to check it

From the repository root:

```bash
python3 docs/architecture/inventory_check.py
```

Expected output (exactly these two lines, exit code 0):

```
cothinker@HEAD: 89 source files, 89 mapped, 0 unmapped, 0 stale, 0 unknown targets
OK
```

"Source file" = every `*.py` in the scoped trees (`gateway stage shared stt
graph modules blocks templates surfaces`) that is not under a `tests/`
directory and is not `conftest.py`. `blocks/`, `templates/` and
`surfaces/` do not exist yet in this repository and so contribute 0 files
today; later waves of the target architecture create them and the
inventory grows to cover them. When code moves, the count changes; a new
module without a row prints `unmapped: <path>` and `FAIL` — this is what
`tests/test_architecture_inventory.py` enforces in the test suite.

`--table` prints the per-target file lists with line counts that the
tables below are built from.

## Blocks

| Block | Group | Source files (lines) | Used by | Built |
|---|---|---|---|---|
| segment (utterance batching) | Capture | `gateway/segmenter.py` 356, `shared/wake_word.py` 67 — 423 | core | 1× |
| topic change | Capture | `gateway/topic_detect.py` 571, `topic_hysteresis.py` 95, `topic_laya_worker.py` 138, `topic_title.py` 202 — 1006 | Reflect | 1× |
| decisions / tasks / open questions | Capture | `gateway/decide.py` 569, `decider.py` 424, `openrouter_jev.py` 269, `policy.py` 668, `policy_runner.py` 567, `proposals.py` 68, `questions.py` 191 — 2756 | Reflect | 1× |
| minutes document | Condense + write | `shared/minutes_format.py` 441 | Reflect | 1× |
| write text | Condense + write | `shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py` 84, `pick_questions.py` 37 — 121 | core | 1× |
| mirroring / coach question | Agent behaviour | `gateway/lane_context.py` 130, `lane_integration.py` 1067 — 1197 (scheduling in `lanes.py`/`agent_lane.py` belongs to the Agent API) | Reflect | 1× |
| knowledge link (vault) | Agent behaviour | `blocks/knowledge_link/wiki.py` 424, `shared/wiki_names.py` 49 (`graph/*.py`, the vault-note library, is a core library: `core:session`) | Reflect | 1× |
| status line | Show + share | `gateway/status_line.py` 164 | Reflect | 1× |
| graph (session / results / terms) | Show + share | `stage/session_graph.py` 389 | Reflect | 1× |
| speaking share (talk share + room talk time) | Show + share | `blocks/speaking_share/__init__.py` 86, `talk_share.py` 148, `room_talk_time.py` 108 — 342 | Reflect | 1× |
| timer (planned items, drift, projected end) | Time | `blocks/timer/clock.py` 59, `blocks/timer/__init__.py` 109 — 168 | Reflect | 1× |

**Reflect check:** 9 of the 12 blocks Birk confirmed for Reflect already
have code in this repository — topic change, decisions / tasks / open
questions, minutes document, mirroring / coach question, knowledge link,
status line, session graph, speaking share, timer. The other 3 confirmed
blocks (capture bullets, condense, speaker queue) have no code in this
repository yet.

## STT module, surfaces, core (not blocks)

| Target | Lines | Files | Used by |
|---|---|---|---|
| STT — server, wire, registry | 1231 | `stt/fundusapps/__init__.py`, `common/__init__.py`, `common/turnid.py`, `stt_server/__init__.py`, `__main__.py`, `app.py`, `backends/__init__.py`, `backends/base.py`, `botland_callbacks.py`, `events.py`, `settings.py` | core |
| STT — audio stage | 2148 | `stt/fundusapps/bbb_tap/__init__.py`, `feed_wav.py`, `pcm.py`; `stt_server/audio_clock.py`, `interview_signal.py`, `pcm_source.py`, `sr.py`, `vad.py`, `vad_detectors.py` | core |
| STT — engine Speechmatics EU | 775 | `shared/speechmatics.py` 323, `shared/utterance.py` 175, `stt_server/backends/speechmatics_rt_backend.py` 277 | core |
| STT — engine local Whisper | 178 | `stt_server/backends/whisper_backend.py` 178 | core |
| STT — thought end | 1594 | `stt/fundusapps/stt_server/thought_end.py` 514; `gateway/thought_end.py` 260, `thought_end_live.py` 402, `thought_end_laya_worker.py` 148, `smart_turn_worker.py` 270 | core |
| STT — speaker labels | 1566 | `gateway/speaker_enroll.py` 606, `speaker_split.py` 423, `speaker_label.py` 278, `speaker_label_llm.py` 259 | core |
| STT — correction | 858 | `gateway/correction.py` 733, `correction_prefilter.py` 125 | core |
| core — session runtime | 5588 | `gateway/consumer.py` 4817, `shared/events.py`, `sync_safe.py`, `timing.py`, `conflict_triage.py`; `shared/__init__.py` | core |
| core — Agent API | 1660 | `shared/agent.py` 387, `gateway/worker.py` 576, `lanes.py` 447, `agent_lane.py` 250 | core |
| core — model clients | 614 | `gateway/infomaniak_chat.py` 178, `infomaniak_embed.py` 124, `infomaniak_limiter.py` 312 | core |
| core — Instance API | 211 | `shared/instance.py` 128, `shared/profiles.py` 83 | core |
| core — Module API | 213 | `shared/modules.py` 169, `modules/example_echo/module.py` 44 | core |
| surface — stage | 4732 | `stage/stage.py` 2953, `gateway/stage_state.py` 1618, `shared/text.py` 54, `stage_image.py` 107 | core |
| retire | 1924 | STT engines dropped by decision 25: `stt_server/backends/infomaniak_whisper_backend.py` 475, `vosk_backend.py` 78, `whisper_streaming_backend.py` 303, `_vendored_whisper_streaming/` 980, `test_vosk.py` 88 | — |

## Targets

Every target id used by the file mapping below. `block:*` are building
blocks, `stt:*` parts of the STT module, `surface:*` surfaces, `core:*` the
core runtime behind the five APIs, and `retire` code the target drops.

```targets
stt:server                  STT module — server process, wire API (SSE /events), CLI, settings, engine registry
stt:audio                   STT module — audio stage: sources, audio clock, VAD, mic gate, PCM recording
stt:engine-speechmatics     STT module — engine adapter Speechmatics EU (realtime, diarization)
stt:engine-whisper-local    STT module — engine adapter local Whisper (optional, offline)
stt:thought-end              STT module — text/audio stage: thought end (smart-turn, Laya)
stt:speaker-labels           STT module — text stage: speaker labels, enrollment, name matching, who-read-what
stt:correction               STT module — text stage: correction (display only, never fed back)
core:session                 core session runtime: session host, state store, event bus, traces, sync safety
core:agent                   core Agent API (AgentPort and its adapters)
core:llm                     core model clients (chat, embeddings, rate limit, reply-language guard)
core:instance                core Instance API (instance.toml, content, profiles, languages)
core:module                  core Module API (module/block loader, example module)
core:surface-api              core Surface API (view intents out, user actions in; in-memory intent board)
surface:stage                surface — meeting web view (stage)
block:segment                block — utterance batching / turn boundaries
block:topics                 block — topic change
block:decisions-tasks-questions  block — decisions / tasks / open questions
block:minutes-document       block — minutes document
block:mirroring-coach        block — mirroring / coach question
block:knowledge-link         block — knowledge link (vault notes)
block:status-line            block — status line
block:graph                  block — graph (session / results / term graph)
block:write-text             block — write text (prologue, scene, script)
block:speaking-share         block — speaking share (talk share + room talk time)
block:timer                  block — timer (planned items, drift, projected end)
retire                       dropped by the target (decision 25 engines)
```

## File mapping — cothinker (this repository)

Scope: `gateway/ stage/ shared/ stt/ graph/ modules/ blocks/ templates/
surfaces/`, every `*.py` outside a `tests/` directory, at `HEAD`.

```inventory cothinker
blocks/__init__.py                                                                core:session
blocks/speaking_share/__init__.py                                                 block:speaking-share
blocks/speaking_share/room_talk_time.py                                           block:speaking-share
blocks/speaking_share/talk_share.py                                               block:speaking-share
blocks/timer/__init__.py                                                         block:timer
blocks/timer/clock.py                                                            block:timer
gateway/agent_lane.py                                                             core:agent
gateway/block_host_wiring.py                                                      core:session
gateway/conflict_triage.py                                                        core:session
gateway/consumer.py                                                               core:session
gateway/correction.py                                                             stt:correction
gateway/correction_prefilter.py                                                   stt:correction
gateway/decide.py                                                                 block:decisions-tasks-questions
gateway/decider.py                                                                block:decisions-tasks-questions
shared/events.py                                                                  core:session
gateway/infomaniak_chat.py                                                        core:llm
gateway/infomaniak_embed.py                                                       core:llm
gateway/infomaniak_limiter.py                                                     core:llm
gateway/lane_context.py                                                           block:mirroring-coach
gateway/lane_integration.py                                                       block:mirroring-coach
gateway/lanes.py                                                                  core:agent
gateway/legacy_stage_sink.py                                                      core:session
gateway/openrouter_jev.py                                                         block:decisions-tasks-questions
gateway/policy.py                                                                 block:decisions-tasks-questions
gateway/policy_runner.py                                                          block:decisions-tasks-questions
gateway/proposals.py                                                              block:decisions-tasks-questions
shared/minutes_format.py                                                          block:minutes-document
gateway/questions.py                                                              block:decisions-tasks-questions
gateway/segmenter.py                                                              block:segment
gateway/smart_turn_worker.py                                                      stt:thought-end
gateway/speaker_enroll.py                                                         stt:speaker-labels
gateway/speaker_label.py                                                          stt:speaker-labels
gateway/speaker_label_llm.py                                                      stt:speaker-labels
gateway/speaker_split.py                                                          stt:speaker-labels
gateway/stage_image.py                                                            surface:stage
gateway/stage_state.py                                                            surface:stage
gateway/status_line.py                                                            block:status-line
gateway/sync_safe.py                                                              core:session
gateway/thought_end.py                                                            stt:thought-end
gateway/thought_end_laya_worker.py                                                stt:thought-end
gateway/thought_end_live.py                                                       stt:thought-end
gateway/timing.py                                                                 core:session
gateway/topic_detect.py                                                           block:topics
gateway/topic_hysteresis.py                                                       block:topics
gateway/topic_laya_worker.py                                                      block:topics
gateway/topic_title.py                                                            block:topics
blocks/knowledge_link/__init__.py                                                                block:knowledge-link
blocks/knowledge_link/wiki.py                                                                block:knowledge-link
gateway/worker.py                                                                 core:agent
graph/__init__.py                                                                 core:session
graph/graph_build.py                                                              core:session
graph/neighbours.py                                                               core:session
graph/ring_select.py                                                              core:session
graph/seed_note.py                                                                core:session
graph/vault_notes.py                                                              core:session
modules/example_echo/module.py                                                   core:module
shared/__init__.py                                                               core:session
shared/agent.py                                                                   core:agent
shared/blocks.py                                                                  core:session
shared/instance.py                                                                core:instance
shared/modules.py                                                                 core:module
shared/phases.py                                                                  core:session
shared/profiles.py                                                                core:instance
shared/templates.py                                                               core:instance
shared/surface.py                                                                  core:surface-api
shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py               block:write-text
shared/profiles/skel/main/skills/prolog/scripts/pick_questions.py                block:write-text
shared/speechmatics.py                                                           stt:engine-speechmatics
shared/text.py                                                                    surface:stage
shared/utterance.py                                                              stt:engine-speechmatics
shared/wake_word.py                                                               block:segment
shared/wiki_names.py                                                              block:knowledge-link
stage/session_graph.py                                                           block:graph
stage/stage.py                                                                   surface:stage
stt/fundusapps/__init__.py                                                       stt:server
stt/fundusapps/bbb_tap/__init__.py                                               stt:audio
stt/fundusapps/bbb_tap/feed_wav.py                                               stt:audio
stt/fundusapps/bbb_tap/pcm.py                                                    stt:audio
stt/fundusapps/common/__init__.py                                                stt:server
stt/fundusapps/common/turnid.py                                                  stt:server
stt/fundusapps/stt_server/__init__.py                                            stt:server
stt/fundusapps/stt_server/__main__.py                                            stt:server
stt/fundusapps/stt_server/app.py                                                 stt:server
stt/fundusapps/stt_server/audio_clock.py                                         stt:audio
stt/fundusapps/stt_server/backends/__init__.py                                   stt:server
stt/fundusapps/stt_server/backends/_vendored_whisper_streaming/__init__.py       retire
stt/fundusapps/stt_server/backends/_vendored_whisper_streaming/whisper_online.py  retire
stt/fundusapps/stt_server/backends/base.py                                       stt:server
stt/fundusapps/stt_server/backends/infomaniak_whisper_backend.py                 retire
stt/fundusapps/stt_server/backends/speechmatics_rt_backend.py                    stt:engine-speechmatics
stt/fundusapps/stt_server/backends/vosk_backend.py                               retire
stt/fundusapps/stt_server/backends/whisper_backend.py                            stt:engine-whisper-local
stt/fundusapps/stt_server/backends/whisper_streaming_backend.py                  retire
stt/fundusapps/stt_server/botland_callbacks.py                                   stt:server
stt/fundusapps/stt_server/events.py                                              stt:server
stt/fundusapps/stt_server/interview_signal.py                                    stt:audio
stt/fundusapps/stt_server/pcm_source.py                                          stt:audio
stt/fundusapps/stt_server/settings.py                                            stt:server
stt/fundusapps/stt_server/sr.py                                                  stt:audio
stt/fundusapps/stt_server/test_vosk.py                                           retire
stt/fundusapps/stt_server/thought_end.py                                         stt:thought-end
stt/fundusapps/stt_server/vad.py                                                 stt:audio
stt/fundusapps/stt_server/vad_detectors.py                                       stt:audio
```
