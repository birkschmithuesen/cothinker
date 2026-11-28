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
cothinker@HEAD: <N> source files, <N> mapped, 0 unmapped, 0 stale, 0 unknown targets
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
| topic change | Capture | `blocks/topics/detect.py` 571, `hysteresis.py` 95, `laya_worker.py` 138, `title.py` 202 — 1006 | Reflect | 1× |
| decisions / tasks / open questions | Capture | `blocks/decisions_tasks_questions/{decide,decider,openrouter_jev,policy,policy_runner,questions}.py` — 2688 (`gateway/proposals.py` 68 stays in the session host) | Reflect | 1× |
| capture bullets | Condense + write | `blocks/capture_bullets/__init__.py` 67, `parse.py` 108 | Reflect | 1× |
| minutes document | Condense + write | `shared/minutes_format.py` 454, `blocks/minutes_document/__init__.py` 130, `blocks/minutes_document/minutes.py` 235 (the session host's `gateway/raw_transcript.py` writes the file) | Reflect | 1× |
| write text | Condense + write | `shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py` 84, `pick_questions.py` 37 — 121 | core | 1× |
| mirroring / coach question | Agent behaviour | `blocks/mirroring_coach/context.py` 130, `coach.py` 588 (the call lane in `gateway/call_lane.py` and scheduling in `lanes.py`/`agent_lane.py` belong to the Agent API) — 718 | Reflect | 1× |
| knowledge link (vault) | Agent behaviour | `blocks/knowledge_link/__init__.py` 123, `blocks/knowledge_link/wiki.py` 408, `shared/wiki_names.py` 49 (`graph/*.py`, the vault-note library, is a core library: `core:session`) | Reflect | 1× |
| status line | Show + share | `blocks/status_line/__init__.py` 143, `group.py` 57, `line.py` 24 — 224 | Reflect | 1× |
| graph (session / results / terms) | Show + share | `blocks/graph/__init__.py` 143, `session_graph.py` 386 — 529 | Reflect | 1× |
| speaking share (talk share + room talk time) | Show + share | `blocks/speaking_share/__init__.py` 86, `talk_share.py` 148, `room_talk_time.py` 108 — 342 | Reflect | 1× |
| timer (planned items, drift, projected end) | Time | `blocks/timer/clock.py` 59, `blocks/timer/__init__.py` 164 — 223 | Reflect | 1× |
| speaker queue | Show + share | `blocks/speaker_queue/__init__.py` 149, `queue.py` 82, `round.py` 56 — 287 | Reflect | 1× |
| voice attribution (who is talking right now, by track/STT speaker label) | Show + share | `blocks/voice_attribution/__init__.py` 104, `voice.py` 73 — 177 | Reflect | 1× |
| call window (listen:room open/close by voice, tap, or timeout) | Capture | `blocks/call_window/__init__.py` 108 | Reflect | 1× |
| live transcript (side panel: last 10 finished lines + the in-progress partial) | Show + share | `blocks/live_transcript/__init__.py` 114, `transcript.py` 86 — 200 | Reflect | 1× |
| status line | Show + share | `blocks/status_line/__init__.py` 86, `line.py` 24 — 110 | Reflect | 1× |
| graph (session / results / terms) | Show + share | `stage/session_graph.py` 389 | Reflect | 1× |
| forms & styles (theatre/music form catalogue, scene forms, writing styles) | Agent behaviour | `blocks/forms_styles/__init__.py` 169, `catalogue.py` 175 — 344 | core | 1× |

**Reflect check:** all 12 of the blocks Birk confirmed for Reflect now have
code in this repository — topic change, capture bullets, condense,
decisions / tasks / open questions, minutes document, mirroring / coach
question, knowledge link, status line, session graph, speaking share, timer,
speaker queue. Voice attribution (issue #164) is a 13th block and live
transcript (issue #181) a 14th, both registered in
`gateway/block_host_wiring.py`'s `REGISTRY`; voice attribution was
deliberately left out of `templates/reflect/template.toml` at first, since
ADR 0010 fixes Reflect at exactly 12 blocks and wiring a 13th into the live
template was a template/ADR decision for the maintainer, not one this
inventory entry could make on its own. Issue #178 (opened by the maintainer,
naming `voice`'s missing live fill as part of its own acceptance criterion)
made that decision: `template.toml` listed `voice-attribution` too, 13
blocks against ADR 0010's fixed 12. Issue #181 (the stage's live transcript
side panel) widens the same, already-named conflict to 14 by adding
`live-transcript` the same way -- not silently papered over either time;
the ADR's own text is unchanged (it is only ever superseded or amended,
never rewritten), and a status-line amendment is the maintainer's call, not
this pull request's.

## STT module, surfaces, core (not blocks)

| Target | Lines | Files | Used by |
|---|---|---|---|
| STT — server, wire, registry | 1580 | `stt/fundusapps/__init__.py`, `common/__init__.py`, `common/turnid.py`, `stt_server/__init__.py`, `__main__.py`, `app.py`, `args.py`, `backends/__init__.py`, `backends/base.py`, `botland_callbacks.py`, `events.py`, `instance_vocab.py`, `settings.py` | core |
| STT — audio stage | 2148 | `stt/fundusapps/bbb_tap/__init__.py`, `feed_wav.py`, `pcm.py`; `stt_server/audio_clock.py`, `interview_signal.py`, `pcm_source.py`, `sr.py`, `vad.py`, `vad_detectors.py` | core |
| STT — engine Speechmatics EU | 775 | `shared/speechmatics.py` 323, `shared/utterance.py` 175, `stt_server/backends/speechmatics_rt_backend.py` 277 | core |
| STT — engine local Whisper | 178 | `stt_server/backends/whisper_backend.py` 178 | core |
| STT — thought end | 1594 | `stt/fundusapps/stt_server/thought_end.py` 514; `gateway/thought_end.py` 260, `thought_end_live.py` 402, `thought_end_laya_worker.py` 148, `smart_turn_worker.py` 270 | core |
| STT — speaker labels | 1566 | `stt/pipeline/speaker_labels/enroll.py` 606, `split.py` 423, `label.py` 278, `label_llm.py` 259 | core |
| STT — correction | 858 | `gateway/correction.py` 733, `correction_prefilter.py` 125 | core |
| core — session runtime | 5888 | `gateway/consumer.py` 4653, `shared/events.py` 179, `gateway/raw_transcript.py` 172, `sync_safe.py` 204, `timing.py` 207, `conflict_triage.py` 186, `block_host_wiring.py` 197; `shared/phases.py` 82, `shared/__init__.py` 1, `blocks/__init__.py` 9 | core |
| core — Agent API | 1660 | `shared/agent.py` 387, `gateway/worker.py` 576, `lanes.py` 447, `agent_lane.py` 250 | core |
| core — model clients | 614 | `gateway/infomaniak_chat.py` 178, `infomaniak_embed.py` 124, `infomaniak_limiter.py` 312 | core |
| core — Instance API | 211 | `shared/instance.py` 128, `shared/profiles.py` 83 | core |
| core — Module API | 213 | `shared/modules.py` 169, `modules/example_echo/module.py` 44 | core |
| surface — stage | 5197 | `stage/stage.py` 2953, `gateway/stage_state.py` 1618, `gateway/stage_surface.py` 311, `shared/text.py` 54, `stage_image.py` 107, `gateway/stage_texts.py` 154 | core |
| retire | 1924 | STT engines dropped by decision 25: `stt_server/backends/infomaniak_whisper_backend.py` 475, `vosk_backend.py` 78, `whisper_streaming_backend.py` 303, `_vendored_whisper_streaming/` 980, `test_vosk.py` 88 | — |

## Targets

Every target id used by the file mapping below. `block:*` are building
blocks, `stt:*` parts of the STT module, `surface:*` surfaces, `core:*` the
core runtime behind the five APIs, and `retire` code the target drops.

```targets
stt:server                  STT module — server process, wire API (SSE /events), CLI, settings, engine registry
stt:api-client               STT API (ADR 0002/0003/0004): client side of the /events wire — event type, SSE transport
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
block:condense               block — condense (per-topic text, verbatim quotes)
block:capture-bullets        block — capture bullets (main-turn reply parser)
block:minutes-document       block — minutes document
block:mirroring-coach        block — mirroring / coach question
block:knowledge-link         block — knowledge link (vault notes)
block:status-line            block — status line
block:graph                  block — graph (session / results / term graph)
block:write-text             block — write text (prologue, scene, script)
block:speaking-share         block — speaking share (talk share + room talk time)
block:timer                  block — timer (planned items, drift, projected end)
block:speaker-queue          block — speaker queue (tap to request/leave the floor)
block:voice-attribution      block — voice attribution (who is talking right now, by track/STT speaker label)
block:call-window            block — call window (listen:room open/close by voice, tap, or timeout)
block:live-transcript        block — live transcript (side panel: last 10 finished lines + the in-progress partial)
block:forms-styles           block — forms & styles catalogue (theatre/music forms, scene forms, writing styles)
retire                       dropped by the target (decision 25 engines)
```

## File mapping — cothinker (this repository)

Scope: `gateway/ stage/ shared/ stt/ graph/ modules/ blocks/ templates/
surfaces/`, every `*.py` outside a `tests/` directory, at `HEAD`.

```inventory cothinker
blocks/__init__.py                                                                core:session
blocks/condense/__init__.py                                                       block:condense
blocks/condense/coverage.py                                                       block:condense
blocks/condense/quotes.py                                                         block:condense
blocks/capture_bullets/__init__.py                                                block:capture-bullets
blocks/capture_bullets/parse.py                                                   block:capture-bullets
blocks/minutes_document/__init__.py                                               block:minutes-document
blocks/minutes_document/minutes.py                                                block:minutes-document
blocks/speaking_share/__init__.py                                                 block:speaking-share
blocks/speaking_share/room_talk_time.py                                           block:speaking-share
blocks/speaking_share/talk_share.py                                               block:speaking-share
blocks/status_line/__init__.py                                                   block:status-line
blocks/status_line/group.py                                                      block:status-line
blocks/status_line/line.py                                                       block:status-line
blocks/timer/__init__.py                                                         block:timer
blocks/timer/clock.py                                                            block:timer
blocks/decisions_tasks_questions/__init__.py                                      block:decisions-tasks-questions
blocks/speaker_queue/__init__.py                                                  block:speaker-queue
blocks/speaker_queue/queue.py                                                     block:speaker-queue
blocks/speaker_queue/round.py                                                     block:speaker-queue
blocks/voice_attribution/__init__.py                                              block:voice-attribution
blocks/voice_attribution/voice.py                                                 block:voice-attribution
blocks/call_window/__init__.py                                                    block:call-window
blocks/live_transcript/__init__.py                                                block:live-transcript
blocks/live_transcript/transcript.py                                              block:live-transcript
blocks/topics/__init__.py                                                         block:topics
blocks/topics/detect.py                                                           block:topics
blocks/topics/hysteresis.py                                                       block:topics
blocks/topics/laya_worker.py                                                      block:topics
blocks/topics/title.py                                                            block:topics
gateway/actions_reader.py                                                         core:session
blocks/graph/__init__.py                                                          block:graph
blocks/graph/session_graph.py                                                     block:graph
gateway/agent_lane.py                                                             core:agent
gateway/block_host_wiring.py                                                      core:session
gateway/conflict_triage.py                                                        core:session
gateway/consumer.py                                                               core:session
gateway/correction.py                                                             stt:correction
gateway/correction_prefilter.py                                                   stt:correction
blocks/decisions_tasks_questions/decide.py                                        block:decisions-tasks-questions
blocks/decisions_tasks_questions/decider.py                                       block:decisions-tasks-questions
shared/events.py                                                                  core:session
gateway/infomaniak_chat.py                                                        core:llm
gateway/infomaniak_embed.py                                                       core:llm
gateway/infomaniak_limiter.py                                                     core:llm
gateway/call_lane.py                                                              core:agent
gateway/lanes.py                                                                  core:agent
blocks/decisions_tasks_questions/openrouter_jev.py                                block:decisions-tasks-questions
blocks/decisions_tasks_questions/policy.py                                        block:decisions-tasks-questions
blocks/decisions_tasks_questions/policy_runner.py                                 block:decisions-tasks-questions
gateway/proposals.py                                                              core:session
gateway/raw_transcript.py                                                         core:session
shared/minutes_format.py                                                          block:minutes-document
blocks/decisions_tasks_questions/questions.py                                     block:decisions-tasks-questions
gateway/segmenter.py                                                              block:segment
gateway/smart_turn_worker.py                                                      stt:thought-end
gateway/stage_image.py                                                            surface:stage
gateway/stage_state.py                                                            surface:stage
gateway/stage_surface.py                                                          surface:stage
gateway/stage_texts.py                                                            surface:stage
gateway/sync_safe.py                                                              core:session
gateway/thought_end.py                                                            stt:thought-end
gateway/thought_end_laya_worker.py                                                stt:thought-end
gateway/thought_end_live.py                                                       stt:thought-end
gateway/timing.py                                                                 core:session
blocks/mirroring_coach/__init__.py                                                block:mirroring-coach
blocks/mirroring_coach/coach.py                                                   block:mirroring-coach
blocks/mirroring_coach/context.py                                                 block:mirroring-coach
blocks/knowledge_link/__init__.py                                                 block:knowledge-link
blocks/knowledge_link/wiki.py                                                     block:knowledge-link
blocks/forms_styles/__init__.py                                                   block:forms-styles
blocks/forms_styles/catalogue.py                                                  block:forms-styles
gateway/worker.py                                                                 core:agent
graph/__init__.py                                                                 core:session
graph/graph_build.py                                                              core:session
graph/neighbours.py                                                               core:session
graph/ring_select.py                                                              core:session
graph/seed_note.py                                                                core:session
graph/vault_notes.py                                                              core:session
modules/example_echo/module.py                                                   core:module
shared/__init__.py                                                               core:session
shared/actions_spool.py                                                           core:surface-api
shared/agent.py                                                                   core:agent
shared/blocks.py                                                                  core:session
shared/instance.py                                                                core:instance
shared/lane_port.py                                                               core:agent
shared/language_guard.py                                                          core:llm   
shared/llm.py                                                                     core:llm   
shared/modules.py                                                                 core:module
shared/phases.py                                                                  core:session
shared/profiles.py                                                                core:instance
shared/templates.py                                                               core:instance
shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py               block:write-text
shared/profiles/skel/main/skills/prolog/scripts/pick_questions.py                block:write-text
shared/speechmatics.py                                                            stt:engine-speechmatics
shared/surface.py                                                                  core:surface-api
shared/text.py                                                                    surface:stage
shared/stt.py                                                                     stt:api-client
shared/stt_inprocess.py                                                           stt:engine-speechmatics
shared/utterance.py                                                              stt:engine-speechmatics
shared/wake_word.py                                                               block:segment
shared/wiki_names.py                                                              block:knowledge-link
stage/stage.py                                                                   surface:stage
surfaces/__init__.py                                                             core:surface-api
surfaces/contract/__init__.py                                                    core:surface-api
surfaces/contract/__main__.py                                                    core:surface-api
surfaces/contract/app.py                                                         core:surface-api
surfaces/contract/check.py                                                       core:surface-api
surfaces/contract/fixtures/__init__.py                                           core:surface-api
surfaces/contract/fixtures/meeting/__init__.py                                   core:surface-api
surfaces/contract/generate_spec.py                                               core:surface-api
surfaces/contract/live_player.py                                                 core:surface-api
surfaces/contract/models.py                                                      core:surface-api
surfaces/contract/player.py                                                      core:surface-api
surfaces/design/__init__.py                                                      core:surface-api
surfaces/design/build.py                                                         core:surface-api
surfaces/operator/__init__.py                                                    core:surface-api
surfaces/operator/__main__.py                                                    core:surface-api
surfaces/operator/app.py                                                         core:surface-api
surfaces/operator/auth.py                                                        core:surface-api
surfaces/operator/backend.py                                                     core:surface-api
surfaces/operator/check.py                                                       core:surface-api
surfaces/operator/demo_backend.py                                                core:surface-api
surfaces/operator/fixtures.py                                                    core:surface-api
surfaces/operator/generate_spec.py                                               core:surface-api
surfaces/operator/models.py                                                      core:surface-api
stt/fundusapps/__init__.py                                                       stt:server
stt/fundusapps/bbb_tap/__init__.py                                               stt:audio
stt/fundusapps/bbb_tap/__main__.py                                               stt:audio
stt/fundusapps/bbb_tap/cdp.py                                                    stt:audio
stt/fundusapps/bbb_tap/feed_wav.py                                               stt:audio
stt/fundusapps/bbb_tap/pcm.py                                                    stt:audio
stt/fundusapps/bbb_tap/reattribute.py                                            stt:audio
stt/fundusapps/bbb_tap/speaker_attribution.py                                    stt:audio
stt/fundusapps/common/__init__.py                                                stt:server
stt/fundusapps/common/turnid.py                                                  stt:server
stt/fundusapps/stt_server/__init__.py                                            stt:server
stt/fundusapps/stt_server/__main__.py                                            stt:server
stt/fundusapps/stt_server/app.py                                                 stt:server
stt/fundusapps/stt_server/args.py                                                stt:server
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
stt/fundusapps/stt_server/instance_vocab.py                                      stt:server
stt/fundusapps/stt_server/interview_signal.py                                    stt:audio
stt/fundusapps/stt_server/pcm_source.py                                          stt:audio
stt/fundusapps/stt_server/settings.py                                            stt:server
stt/fundusapps/stt_server/track_speaker_resolver.py                             stt:audio
stt/fundusapps/stt_server/sr.py                                                  stt:audio
stt/fundusapps/stt_server/test_vosk.py                                           retire
stt/fundusapps/stt_server/thought_end.py                                         stt:thought-end
stt/fundusapps/stt_server/vad.py                                                 stt:audio
stt/fundusapps/stt_server/vad_detectors.py                                       stt:audio
stt/pipeline/__init__.py                                                          stt:server
stt/pipeline/speaker_labels/__init__.py                                           stt:speaker-labels
stt/pipeline/speaker_labels/enroll.py                                             stt:speaker-labels
stt/pipeline/speaker_labels/label.py                                              stt:speaker-labels
stt/pipeline/speaker_labels/label_llm.py                                          stt:speaker-labels
stt/pipeline/speaker_labels/split.py                                              stt:speaker-labels
```
