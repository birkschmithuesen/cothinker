# Building blocks — inventory of what the code does today

Every Python source module of today's four applications maps to **exactly
one** target: a building block, a part of the STT module, the core phase
engine, a surface, the core runtime behind the five APIs (see
[target.md](target.md)), or `retire`. The machine-checked mapping is the
fenced `inventory` blocks at the end of this file; the tables below are
generated from it and add line counts and users.

Diagram: [blocks-inventory.png](blocks-inventory.png) (source
[blocks-inventory.html](blocks-inventory.html)), version 2, corrected
against the code (see "Corrections against the draft").

**Measured on:** cothinker `fe3351d4` (this repository), interview-theater
(the InScribe application) at tag `example-production-2026-final`, kollektivgedaechtnis at
`HEAD` of 2026-11-20. Line counts are newline counts of the blob at that ref
(`git show <ref>:<path> | wc -l`), produced by `inventory_check.py --table`.

**Users:** M = meeting (today's `gateway/`, `stage/`, `graph/`, becomes the
Reflect template), W = one-day workshop (`workshop/`, an example one-day-workshop instance),
I = InScribe long format (interview-theater, an example production instance),
K = Kollektivgedächtnis (kollektivgedaechtnis). **Built** = in how many of
the four code bases the function exists as separate code (2× / 3×).
**Reflect** = part of the Reflect template (12 blocks, confirmed by Birk).

## How to check it

From the repository root:

```bash
python3 docs/architecture/inventory_check.py
```

Expected output (exactly these two lines, exit code 0):

```
cothinker@HEAD: 187 source files, 187 mapped, 0 unmapped, 0 stale, 0 unknown targets
OK
```

With the two private application repositories available as local clones
(read-only; the script only runs `git ls-tree` and `git show` against the
pinned ref, never touches a working tree):

```bash
python3 docs/architecture/inventory_check.py \
  --repo interview-theater=<clone-of-interview-theater> \
  --repo kollektivgedaechtnis=<clone-of-kollektivgedaechtnis>
```

Expected output:

```
cothinker@HEAD: 187 source files, 187 mapped, 0 unmapped, 0 stale, 0 unknown targets
interview-theater@example-production-2026-final: 103 source files, 103 mapped, 0 unmapped, 0 stale, 0 unknown targets
kollektivgedaechtnis@HEAD: 41 source files, 41 mapped, 0 unmapped, 0 stale, 0 unknown targets
OK
```

"Source file" = every `*.py` in the scoped trees (`gateway stage workshop
shared stt graph modules` here; `interview_theater` there; `kg kg2 mirror
stt_backends` in Kollektivgedächtnis) that is not under a `tests/`
directory and is not `conftest.py`. When code moves during the migration,
the count changes; a new module without a row prints `unmapped: <path>`
and `FAIL`. Plan task A.3 puts this check into the test suite.

`--table` prints the per-target file lists with line counts that the two
tables below are built from.

## Blocks

| Block | Group | Source files (lines) | Users | Built | Reflect |
|---|---|---|---|---|---|
| capture bullets | Capture | M: no own file — the main agent turn appends `## Verlauf` bullets (prompt in `gateway/consumer.py` ~L300–312, run by `gateway/worker.py`); W: `workshop/bullets.py` 956, `bullets_mishear.py` 160, `bullets_pairing.py` 154, `bullets_sweep.py` 348, `bullets_sweep_score.py` 316, `echo.py` 323 (+ prompts `workshop/prompts/notes_live*.md`, `notes_final*.md`, `stage2*.md`, `echo*.md`) — 2257 | M W | 2× | yes |
| topic change | Capture | `gateway/topic_detect.py` 571, `topic_hysteresis.py` 95, `topic_laya_worker.py` 138, `topic_title.py` 202 — 1006 (+ the agent's `topic` proposal) | M | 1× | yes |
| decisions / tasks / open questions | Capture | M: `gateway/decide.py` 569, `decider.py` 424, `openrouter_jev.py` 269, `policy.py` 668, `policy_runner.py` 567, `proposals.py` 68, `questions.py` 191 (+ the main turn's `## Entscheidungen/Aufgaben/Offene Fragen`); I: `journal.py` 316, `vorschlag.py` 538, `vorschlagssperre.py` 175 — 3785 | M I | 2× | yes |
| capture terms | Capture | W: `workshop/terms.py` 844; I: `begriffe.py` 154, `begriffsboard.py` 865, `begriffsboard_analyse.py` 218; K: `kg/extraction.py` 178 — 2259 | W I K | 3× | — |
| merge terms | Capture | W: `workshop/term_merge.py` 389; K: `kg/embeddings.py` 227, `kg/merging.py` 262 — 878 | W K | 2× | — |
| segment (utterance batching) | Capture | M: `gateway/blocks.py` 402; W: `workshop/discussion_turn_queue.py` 76, `discussion_turns.py` 82 — 560 | M W | 2× | (inside capture) |
| condense / summary | Condense + write | W: `workshop/discussion_eval_live.py` 113, `discussion_eval_sweep.py` 602, `discussion_sweep_score.py` 117, `feedback_points.py` 178; I: `diskussion.py` 261, `phasen_summary.py` 385, `verdichter.py` 260; K: `kg2/condense.py` 734, `kg2/weighting.py` 592 — 3242 | W I K | 3× | yes (new for M) |
| minutes document | Condense + write | `gateway/protocol_doc.py` 441 + `Protocol` class in `gateway/consumer.py` (L1249–1689, 441) | M | 1× | yes |
| contrast pairs | Condense + write | `workshop/dossier.py` 123, `sort.py` 292, `sort_themes.py` 592, `wall_themes.py` 393 — 1400 | W | 1× | — |
| write text | Condense + write | W: `workshop/prologue_fast.py` 559, `prologue_input.py` 450, `prologue_orchestrator.py` 642, `prologue_premise.py` 358, `prologue_run.py` 558, `prologue_run_profiles.py` 155, `prologue_seam.py` 293, `prologue_time.py` 60, `scene.py` 734, `shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py` 84, `pick_questions.py` 37; I: `entwurf.py` 422, `kuerzung.py` 230, `kurzgeschichte.py` 620, `laengen.py` 611, `nachpass.py` 407, `sprachpass.py` 389, `stagescript.py` 1023, `szene.py` 2932, `szene_claude.py` 701, `ueberarbeitung.py` 830 — 12095 | W I | 2× | — |
| dramaturgy checks | Condense + write | W: `workshop/prologue_check.py` 473, `prologue_editor.py` 811; I: `dramaturgie/__init__.py` 27, `beleg.py` 182, `bilanz.py` 201, `fanout.py` 1610, `mechanik.py` 1149, `schleife.py` 437, `kartentreue.py` 130, `prueflauf.py` 668, `stueckpruefung.py` 447 — 6135 | W I | 2× | — |
| poster | Condense + write | `workshop/poster.py` 1621, `poster_agent.py` 128 — 1749 | W | 1× | — |
| image generation | Condense + write | W: `workshop/poster_backend.py` 306; K: `kg2/imagegen.py` 739 — 1045 | W K | 2× | — |
| export | Condense + write | `vorspann.py` 321, `web_pdf.py` 142, `web_skript.py` 434 — 897 | I | 1× | — |
| mirroring / coach question | Agent behaviour | `gateway/lane_context.py` 130, `lane_integration.py` 1067 — 1197 (scheduling in `lanes.py`/`agent_lane.py` belongs to the Agent API) | M | 1× | yes |
| knowledge link (vault) | Agent behaviour | `gateway/wiki.py` 424, `graph/__init__.py` 5, `graph_build.py` 89, `neighbours.py` 90, `ring_select.py` 110, `seed_note.py` 84, `vault_notes.py` 239 — 1041 | M | 1× | yes |
| interview questions (own / AI) | Agent behaviour | W: `workshop/own_question.py` 312, `own_question_match.py` 106, `own_question_sweep.py` 162, `own_question_sweep2.py` 97, `own_question_sweep2_score.py` 126, `own_question_sweep_score.py` 111; I: `auswahl.py` 242, `fragen_auswertung.py` 100, `fragen_ki.py` 313, `knoepfe/fragen.py` 2015, `leitfaden.py` 318 — 3902 | W I | 2× | — |
| conversation agent | Agent behaviour | `ablauf.py` 2215, `erkenner.py` 4153, `kontext.py` 2388 — 8756 | I | 1× | — |
| research | Agent behaviour | `recherche.py` 293 | I | 1× | — |
| translation | Agent behaviour | `skript_uebersetzung.py` 195, `uebersetzung.py` 265 — 460 | I | 1× | — |
| timer | Rounds + time | no own file: cue clock inside `workshop/hub.py` (L467–595 cue files, L7193–7302 cue clock/drift; 52 lines match `timer`) + `workshop/static/timer.js` 65, `wall-hint-timer.js` 157 | W | 1× | yes (new for M) |
| speaker queue | Rounds + time | `workshop/speak_queue.py` 83 (+ `workshop/static/speak-queue.js` 200) | W | 1× | yes (new for M) |
| speaking share | Rounds + time | `workshop/room_talk_time.py` 108, `talk_share.py` 148 — 256 | W | 1× | yes (new for M) |
| intro round | Rounds + time | `workshop/stt_check.py` 110, `vorstellen.py` 162 — 272 | W | 1× | — |
| interview round | Rounds + time | W: `workshop/flow.py` 733, `start_seats.py` 130; I: `knoepfe/interviews.py` 517; K: `kg/session.py` 207 — 1587 | W I K | 3× | — |
| setting round | Rounds + time | `workshop/setting.py` 720, `setting_answer.py` 204, `setting_check.py` 388 — 1312 | W | 1× | — |
| feedback round | Rounds + time | `workshop/feedback_rec.py` 256 | W | 1× | — |
| status line | Show + share | M: `gateway/status_line.py` 164; I: `cothinker_status.py` 69 — 233 | M I | 2× | yes |
| graph (session / results / terms) | Show + share | M: `stage/session_graph.py` 389; W: `workshop/results_graph.py` 431; K: `kg/export.py` 144, `kg2/graph_client.py` 52 — 1016 | M W K | 3× | yes |
| release to guests | Show + share | W: `workshop/release.py` 216; K: `mirror/uploader.py` 332 — 548 | W K | 2× | — |
| choice list | Show + share | no code; planned for InScribe (generic tap list: accept / reject / edit) | (I) | 0× | — |
| voice command | Show + share | `kg/segmentation.py` 260, `kg/stop_intent.py` 161 — 421 (the meeting's wake word lives in `gateway/blocks.py`, mapped to segment) | K | 1× | — |
| portrait | Show + share | `kg/photos.py` 313 | K | 1× | — |
| piece model | InScribe | `erkenner_werkzeuge.py` 156, `hintergrund.py` 155, `karten_nachzug.py` 203, `stueck.py` 635, `stueck_sicht.py` 82, `szenenfolge.py` 1464, `szenenkarte.py` 1182, `szenenkern.py` 323 — 4200 | I | 1× | — |
| characters | InScribe | `knoepfe/figuren.py` 688, `sprachprofil.py` 248, `sprachstil.py` 243, `sprecher.py` 279, `sprechweise.py` 225 — 1683 | I | 1× | — |
| forms and styles | InScribe | `formberater.py` 904, `stile.py` 219 — 1123 | I | 1× | — |
| quote anchoring | InScribe | `kernzitate.py` 327, `schaerfung.py` 1165, `zitat.py` 34 — 1526 | I | 1× | — |
| brainstorm stage card | InScribe | `brainstorm.py` 128, `buehnenkarte.py` 166 — 294 | I | 1× | — |

InScribe paths are relative to `interview_theater/`; Kollektivgedächtnis
paths are given with their top directory.

**Reflect check:** the yellow cells are exactly the 12 blocks Birk
confirmed for Reflect — capture bullets, topic change, decisions / tasks /
open questions, condense, minutes document, mirroring / coach question,
knowledge link, timer, speaker queue, speaking share, status line, session
graph. Four of them have no meeting code today: condense, timer, speaker
queue, speaking share.

## STT module, phase engine, surfaces, core (not blocks)

| Target | Lines | Main files (all files: `--table`) | Users |
|---|---|---|---|
| STT — server, wire, registry | 1532 | `stt/fundusapps/stt_server/{app,args,settings,events,__main__}.py`, `backends/{__init__,base}.py` | M K |
| STT — audio stage | 5791 | `stt/fundusapps/stt_server/sr.py` 1149, `bbb_tap/__main__.py` 617, `vad*.py`, `audio_clock.py`; `workshop/table_audio.py` 146; I `aufnahme.py` 2707 | M W I |
| STT — engine Speechmatics EU | 1255 | `shared/speechmatics.py` 323, `shared/utterance.py` 175, `backends/speechmatics_rt_backend.py` 277, `workshop/stt_session.py` 273, `workshop/stt_speechmatics.py` 207 | M W |
| STT — engine local Whisper | 178 | `backends/whisper_backend.py` 178 | M |
| STT — thought end | 1594 | `stt/…/thought_end.py` 514, `gateway/thought_end.py` 260, `thought_end_live.py` 402, `thought_end_laya_worker.py` 148, `smart_turn_worker.py` 270 | M |
| STT — speaker labels | 2545 | `gateway/speaker_enroll.py` 606, `speaker_split.py` 423, `speaker_label.py` 278, `speaker_label_llm.py` 259; `workshop/role_enroll.py` 667, `text_anchor.py` 193, `question_readout.py` 62, `enrollment.py` 57 | M W |
| STT — correction | 858 | `gateway/correction.py` 733, `correction_prefilter.py` 125 | M |
| STT — text filter | 188 | `workshop/label_filter.py` 188 | W |
| STT — API client | 102 | `kg/stt_client.py` 75, `kg/sse.py` 27 (the meeting's client lives inside `gateway/consumer.py`, `parse_sse`/`sse_events`) | K (M) |
| phase engine | 5787 | `workshop/mic_off.py` 112 (+ step/cue machinery inside `workshop/hub.py`); I `phasen.py` 617, `phasentexte.py` 593, `roadmap.py` 781, `fehlstellen.py` 387, `knoepfe/szenen.py` 2116, `knoepfe/stationen.py` 546; K `kg/pipeline.py` 105, `kg2/cycle.py` 216, `trigger.py` 196, `watcher.py` 118 | W I K |
| core — session runtime | 17549 | `gateway/consumer.py` 4817, `events.py`, `sync_safe.py`, `timing.py`, `conflict_triage.py`; `workshop/hooks.py`, `latency.py`, `table_files.py`, `trace.py`; I `repo.py` 6232, `db.py` 1951, `bot.py` 785; K `kg/store.py`, `kg2/store.py` … | M W I K |
| core — Agent API | 1660 | `shared/agent.py` 387, `gateway/worker.py` 576, `lanes.py` 447, `agent_lane.py` 250 | M W |
| core — model clients | 2257 | `gateway/infomaniak_{chat,embed,limiter}.py`, `workshop/language_guard.py`; I `llm.py` 732, `kosten.py` 338; K `kg/llm.py` 276 | M W I K |
| core — Instance API | 3319 | `shared/instance.py` 142, `shared/profiles.py` 83, `workshop/languages.py`, `textfiles.py`, `deploy/sync_profile.py`; I `workshop.py` 1334 … | all |
| core — Module API | 213 | `shared/modules.py` 169, `modules/example_echo/module.py` 44 | — |
| surface — stage | 4678 | `stage/stage.py` 2953, `gateway/stage_state.py` 1618, `stage_image.py` 107 | M |
| surface — workshop web app | 11708 | `workshop/hub.py` 8038 (whole file; split by concern in [gap-analysis.md](gap-analysis.md) §7), `ops_state.py` 696, `hub_config.py` 545, `ops_metrics.py` 409, … | W |
| surface — InScribe web app | 27908 | `web.py` 6543, `web_chat.py` 5674, `web_vereint.py` 2979, `web_daten.py` 2826, `web_gestalt.py` 2286, `knoepfe/wirkung.py` 2124, … | I |
| surface — External Display | 5973 | `workshop/wall.py` 3633, `wall_layout.py` 635, `wall_feedback_layout.py` 501, `room_look.py` 369, …; K `kg/server.py` 308, `kg2/server.py` 257 | W K |
| surface — guest view | 1503 | `workshop/conference_app.py` 1091; K `mirror/receiver.py` 400 | W K |
| surface — Telegram (parked) | 2399 | I `befehle.py` 1676, `telegram.py` 605; K `kg/telegram_bot.py` 118 | I K |
| retire | 3620 | STT engines dropped by decision 25: `backends/elevenlabs_scribe_backend.py` 683, `infomaniak_whisper_backend.py` 475, `whisper_streaming_backend.py` 303, `_vendored_whisper_streaming/` 980, `vosk_backend.py` 78, `test_vosk.py` 88; `workshop/stt_fallback.py` 223 (Infomaniak fallback); re-export shims `workshop/agent_api.py` 20, `agent_port.py` 20, `utterance.py` 15; I `stt.py` 293; K `stt_backends/infomaniak_whisper_backend.py` 442 | — |

`workshop/hub.py` is mapped as a whole to the workshop web app because the
checker maps files, not functions. It also holds the timer (cue clock), the
phase machinery and parts of eight blocks; the line-range breakdown is in
[gap-analysis.md](gap-analysis.md) §7.

## Corrections against the draft (diagram v1)

| Draft v1 said | The code says | Fixed in v2 |
|---|---|---|
| capture bullets, meeting side: `gateway/blocks.py` + `decide.py` | `gateway/blocks.py` bundles STT finals into agent-sized blocks (pause / wake word / length cut, docstring L8–25) → **segment**. `gateway/decide.py` asks the level-1 question set per block → **decisions / tasks / open questions**. The meeting's bullets are appended to `## Verlauf` by the main agent turn itself (prompt `gateway/consumer.py` L303–305) — no own file. | capture bullets row names the agent turn; `blocks.py` → segment |
| mirroring / coach: `gateway/lanes.py`, `agent_lane.py`, `worker.py` · `workshop/echo.py` (M+W) | `lanes.py` is a generic lane scheduler ("knows nothing about prompts", L21–23), `agent_lane.py` the AgentPort transport, `worker.py` the in-process Hermes worker → **Agent API**. The coach logic is `lane_integration.py` + `lane_context.py`. `workshop/echo.py` is a rolling bullet extractor for the sort step ("Eure Ergänzungen", L2) that imports `workshop.bullets` → **capture bullets**. | mirroring is M only, 1×; echo → capture bullets |
| condense / summary: W only, "notes prompts" | `notes_live`/`notes_final` are the capture-bullets prompts (`workshop/bullets.py` L95–96). Condensation also exists in InScribe (`verdichter.py`, `phasen_summary.py`, `diskussion.py`) and Kollektivgedächtnis (`kg2/condense.py`, `weighting.py`). | condense W+I+K, 3× |
| terms: W only | Also InScribe (`begriffe.py`, `begriffsboard*.py`) and Kollektivgedächtnis (`kg/extraction.py`; merge in `kg/merging.py` + `embeddings.py`). | capture terms 3×, merge terms 2× |
| session / results graph: M+W | Kollektivgedächtnis builds the term graph too (`kg/export.py` → graph.json, `kg2/graph_client.py`). | graph 3× |
| status line, decisions: M only | InScribe has its own (`cothinker_status.py`; `journal.py`, `vorschlag*.py`). | both 2× |
| interview round, write text, dramaturgy, own question: W only | InScribe has its own versions of each; interview round also in Kollektivgedächtnis (`kg/session.py`). | 2× / 3× markers |
| speech row: four cards, no engines | Engines are part of the STT module: one Speechmatics client (`shared/speechmatics.py`, used by both the STT server backend and the workshop) and the local Whisper backend; six engine files retire (decision 25). | engines card added |
| release to guests: `release.py`, `conference_app.py` | `conference_app.py` is a page server (guest view surface), not block logic. Kollektivgedächtnis has its own release (`mirror/uploader.py`). | release W+K 2×; `conference_app.py` → surface |
| — | Not in the draft at all: research, translation, conversation agent, characters, forms and styles, quote anchoring, brainstorm card, export (InScribe); voice command, portrait (Kollektivgedächtnis); segment; image generation. | added |

## Targets

Every target id used by the file mapping below. `block:*` are building
blocks, `stt:*` parts of the STT module, `phase` the core phase engine,
`surface:*` surfaces, `core:*` the core runtime behind the five APIs, and
`retire` code the target drops.

```targets
stt:server                  STT module — server process, wire API (SSE /events), CLI, settings, engine registry
stt:audio                   STT module — audio stage: sources, audio clock, VAD, mic gate, PCM recording
stt:engine-speechmatics     STT module — engine adapter Speechmatics EU (realtime, diarization)
stt:engine-whisper-local    STT module — engine adapter local Whisper (optional, offline)
stt:thought-end             STT module — text/audio stage: thought end (smart-turn, Laya)
stt:speaker-labels          STT module — text stage: speaker labels, enrollment, name matching, who-read-what
stt:correction              STT module — text stage: correction (display only, never fed back)
stt:text-filter             STT module — text stage: content filter (read-out labels, volume)
stt:api-client              STT API client side (consumers of the event stream)
phase                       core phase engine (phases, transitions, per-phase rules, run-to-run orchestration)
core:session                core session runtime: session host, state store, event bus, traces, sync safety
core:agent                  core Agent API (AgentPort and its adapters)
core:llm                    core model clients (chat, embeddings, rate limit, reply-language guard)
core:instance               core Instance API (instance.toml, content, profiles, languages)
core:module                 core Module API (module/block loader, example module)
surface:stage               surface — meeting web view (stage)
surface:workshop-app        surface — workshop web app (phones, operator desk), one-day format
surface:inscribe-app        surface — InScribe web app, long format
surface:external-display    surface — External Display (wall, floor, projection, screen)
surface:guest-view          surface — read-only view for guests / public mirror
surface:telegram            surface — Telegram (parked, decision 3)
block:segment               block — utterance batching / turn boundaries
block:capture-bullets       block — capture bullets
block:topics                block — topics
block:decisions-tasks-questions  block — decisions / tasks / open questions
block:capture-terms         block — capture terms
block:merge-terms           block — merge terms
block:condense              block — condense / summary
block:contrast-pairs        block — contrast pairs (sort step)
block:write-text            block — write text (prologue, scene, script)
block:dramaturgy-checks     block — dramaturgy checks
block:poster                block — poster
block:image-generation      block — image generation
block:minutes-document      block — minutes document
block:mirroring-coach       block — mirroring / coach question
block:interview-questions   block — interview questions (own vs. AI question)
block:knowledge-link        block — knowledge link (vault notes)
block:timer                 block — timer
block:speaker-queue         block — speaker queue
block:speaking-share        block — speaking share
block:intro-round           block — introduction round
block:interview-round       block — interview round (who asks / answers)
block:setting-round         block — setting round
block:feedback-round        block — feedback round
block:status-line           block — status line
block:graph                 block — graph (session / results / term graph)
block:release               block — release to guests / public
block:voice-command         block — voice command (wake word, spoken stop)
block:piece-model           block — piece model (incl. scene cards)
block:characters            block — characters
block:forms-styles          block — forms and styles
block:quote-anchoring       block — quote anchoring
block:translation           block — translation
block:research              block — research
block:brainstorm-card       block — brainstorm stage card
block:conversation-agent    block — conversation agent (chat turn, intent recognizer)
block:export                block — export (script, PDF, preface)
block:portrait              block — portrait
block:choice-list           block — choice list (tap accept / reject / edit); planned, no code yet
retire                      dropped by the target (decision 25 engines, re-export shims)
```

## File mapping — cothinker (this repository)

Scope: `gateway/ stage/ workshop/ shared/ stt/ graph/ modules/`, every
`*.py` outside a `tests/` directory, at `HEAD`.

```inventory cothinker
gateway/agent_lane.py                     core:agent
gateway/blocks.py                         block:segment
gateway/conflict_triage.py                core:session
gateway/consumer.py                       core:session
gateway/correction.py                     stt:correction
gateway/correction_prefilter.py           stt:correction
gateway/decide.py                         block:decisions-tasks-questions
gateway/decider.py                        block:decisions-tasks-questions
gateway/events.py                         core:session
gateway/infomaniak_chat.py                core:llm
gateway/infomaniak_embed.py               core:llm
gateway/infomaniak_limiter.py             core:llm
gateway/lane_context.py                   block:mirroring-coach
gateway/lane_integration.py               block:mirroring-coach
gateway/lanes.py                          core:agent
gateway/openrouter_jev.py                 block:decisions-tasks-questions
gateway/policy.py                         block:decisions-tasks-questions
gateway/policy_runner.py                  block:decisions-tasks-questions
gateway/proposals.py                      block:decisions-tasks-questions
gateway/protocol_doc.py                   block:minutes-document
gateway/questions.py                      block:decisions-tasks-questions
gateway/smart_turn_worker.py              stt:thought-end
gateway/speaker_enroll.py                 stt:speaker-labels
gateway/speaker_label.py                  stt:speaker-labels
gateway/speaker_label_llm.py              stt:speaker-labels
gateway/speaker_split.py                  stt:speaker-labels
gateway/stage_image.py                    surface:stage
gateway/stage_state.py                    surface:stage
gateway/status_line.py                    block:status-line
gateway/sync_safe.py                      core:session
gateway/thought_end.py                    stt:thought-end
gateway/thought_end_laya_worker.py        stt:thought-end
gateway/thought_end_live.py               stt:thought-end
gateway/timing.py                         core:session
gateway/topic_detect.py                   block:topics
gateway/topic_hysteresis.py               block:topics
gateway/topic_laya_worker.py              block:topics
gateway/topic_title.py                    block:topics
gateway/wiki.py                           block:knowledge-link
gateway/worker.py                         core:agent
graph/__init__.py                         block:knowledge-link
graph/graph_build.py                      block:knowledge-link
graph/neighbours.py                       block:knowledge-link
graph/ring_select.py                      block:knowledge-link
graph/seed_note.py                        block:knowledge-link
graph/vault_notes.py                      block:knowledge-link
modules/example_echo/module.py            core:module
shared/__init__.py                        core:session
shared/agent.py                           core:agent
shared/instance.py                        core:instance
shared/modules.py                         core:module
shared/profiles.py                        core:instance
shared/profiles/skel/main/skills/prolog/scripts/collect_answers.py  block:write-text
shared/profiles/skel/main/skills/prolog/scripts/pick_questions.py   block:write-text
shared/speechmatics.py                    stt:engine-speechmatics
shared/utterance.py                       stt:engine-speechmatics
stage/session_graph.py                    block:graph
stage/stage.py                            surface:stage
stt/fundusapps/__init__.py                stt:server
stt/fundusapps/bbb_tap/__init__.py        stt:audio
stt/fundusapps/bbb_tap/__main__.py        stt:audio
stt/fundusapps/bbb_tap/cdp.py             stt:audio
stt/fundusapps/bbb_tap/feed_wav.py        stt:audio
stt/fundusapps/bbb_tap/pcm.py             stt:audio
stt/fundusapps/common/__init__.py         stt:server
stt/fundusapps/common/turnid.py           stt:server
stt/fundusapps/stt_server/__init__.py     stt:server
stt/fundusapps/stt_server/__main__.py     stt:server
stt/fundusapps/stt_server/app.py          stt:server
stt/fundusapps/stt_server/args.py         stt:server
stt/fundusapps/stt_server/audio_clock.py  stt:audio
stt/fundusapps/stt_server/backends/__init__.py  stt:server
stt/fundusapps/stt_server/backends/_vendored_whisper_streaming/__init__.py        retire
stt/fundusapps/stt_server/backends/_vendored_whisper_streaming/whisper_online.py  retire
stt/fundusapps/stt_server/backends/base.py                         stt:server
stt/fundusapps/stt_server/backends/elevenlabs_scribe_backend.py    retire
stt/fundusapps/stt_server/backends/infomaniak_whisper_backend.py   retire
stt/fundusapps/stt_server/backends/speechmatics_rt_backend.py      stt:engine-speechmatics
stt/fundusapps/stt_server/backends/vosk_backend.py                 retire
stt/fundusapps/stt_server/backends/whisper_backend.py              stt:engine-whisper-local
stt/fundusapps/stt_server/backends/whisper_streaming_backend.py    retire
stt/fundusapps/stt_server/botland_callbacks.py  stt:server
stt/fundusapps/stt_server/events.py       stt:server
stt/fundusapps/stt_server/interview_signal.py  stt:audio
stt/fundusapps/stt_server/pcm_source.py   stt:audio
stt/fundusapps/stt_server/settings.py     stt:server
stt/fundusapps/stt_server/sr.py           stt:audio
stt/fundusapps/stt_server/test_vosk.py    retire
stt/fundusapps/stt_server/thought_end.py  stt:thought-end
stt/fundusapps/stt_server/vad.py          stt:audio
stt/fundusapps/stt_server/vad_detectors.py  stt:audio
workshop/__init__.py                      core:session
workshop/agent_api.py                     retire
workshop/agent_port.py                    retire
workshop/bullets.py                       block:capture-bullets
workshop/bullets_mishear.py               block:capture-bullets
workshop/bullets_pairing.py               block:capture-bullets
workshop/bullets_sweep.py                 block:capture-bullets
workshop/bullets_sweep_score.py           block:capture-bullets
workshop/client_log.py                    surface:workshop-app
workshop/conference_app.py                surface:guest-view
workshop/deploy/sync_profile.py           core:instance
workshop/discussion_eval_live.py          block:condense
workshop/discussion_eval_sweep.py         block:condense
workshop/discussion_sweep_score.py        block:condense
workshop/discussion_turn_queue.py         block:segment
workshop/discussion_turns.py              block:segment
workshop/dossier.py                       block:contrast-pairs
workshop/echo.py                          block:capture-bullets
workshop/enrollment.py                    stt:speaker-labels
workshop/feedback_points.py               block:condense
workshop/feedback_rec.py                  block:feedback-round
workshop/flow.py                          block:interview-round
workshop/hooks.py                         core:session
workshop/hub.py                           surface:workshop-app
workshop/hub_config.py                    surface:workshop-app
workshop/js_object_literal.py             surface:workshop-app
workshop/label_filter.py                  stt:text-filter
workshop/language_guard.py                core:llm
workshop/languages.py                     core:instance
workshop/latency.py                       core:session
workshop/latency_page.py                  surface:workshop-app
workshop/login_gate.py                    surface:workshop-app
workshop/mic_off.py                       phase
workshop/object_icons.py                  surface:workshop-app
workshop/ops_metrics.py                   surface:workshop-app
workshop/ops_state.py                     surface:workshop-app
workshop/own_question.py                  block:interview-questions
workshop/own_question_match.py            block:interview-questions
workshop/own_question_sweep.py            block:interview-questions
workshop/own_question_sweep2.py           block:interview-questions
workshop/own_question_sweep2_score.py     block:interview-questions
workshop/own_question_sweep_score.py      block:interview-questions
workshop/persons.py                       core:instance
workshop/poster.py                        block:poster
workshop/poster_agent.py                  block:poster
workshop/poster_backend.py                block:image-generation
workshop/probe_demo.py                    surface:workshop-app
workshop/prologue_check.py                block:dramaturgy-checks
workshop/prologue_editor.py               block:dramaturgy-checks
workshop/prologue_fast.py                 block:write-text
workshop/prologue_input.py                block:write-text
workshop/prologue_orchestrator.py         block:write-text
workshop/prologue_premise.py              block:write-text
workshop/prologue_run.py                  block:write-text
workshop/prologue_run_profiles.py         block:write-text
workshop/prologue_seam.py                 block:write-text
workshop/prologue_time.py                 block:write-text
workshop/pseudonym.py                     surface:workshop-app
workshop/question_readout.py              stt:speaker-labels
workshop/release.py                       block:release
workshop/results_graph.py                 block:graph
workshop/role_enroll.py                   stt:speaker-labels
workshop/room_look.py                     surface:external-display
workshop/room_talk_time.py                block:speaking-share
workshop/scene.py                         block:write-text
workshop/setting.py                       block:setting-round
workshop/setting_answer.py                block:setting-round
workshop/setting_check.py                 block:setting-round
workshop/skin.py                          surface:workshop-app
workshop/sort.py                          block:contrast-pairs
workshop/sort_themes.py                   block:contrast-pairs
workshop/speak_queue.py                   block:speaker-queue
workshop/start_seats.py                   block:interview-round
workshop/strings_file.py                  surface:workshop-app
workshop/stt_check.py                     block:intro-round
workshop/stt_fallback.py                  retire
workshop/stt_session.py                   stt:engine-speechmatics
workshop/stt_speechmatics.py              stt:engine-speechmatics
workshop/table_audio.py                   stt:audio
workshop/table_files.py                   core:session
workshop/table_views.py                   surface:workshop-app
workshop/talk_share.py                    block:speaking-share
workshop/term_merge.py                    block:merge-terms
workshop/terms.py                         block:capture-terms
workshop/test_pages.py                    surface:workshop-app
workshop/text_anchor.py                   stt:speaker-labels
workshop/textfiles.py                     core:instance
workshop/trace.py                         core:session
workshop/utterance.py                     retire
workshop/vorstellen.py                    block:intro-round
workshop/wall.py                          surface:external-display
workshop/wall_feedback_layout.py          surface:external-display
workshop/wall_hub_terms.py                surface:external-display
workshop/wall_layout.py                   surface:external-display
workshop/wall_theme_layout.py             surface:external-display
workshop/wall_themes.py                   block:contrast-pairs
```

## File mapping — InScribe (interview-theater, tag `example-production-2026-final`)

Scope: `interview_theater/`. Operator scripts (`scripts/`, 68 files) and
the simulator (`simulation/`, 38 files) are tooling, not runtime, and are
not mapped.

```inventory interview-theater
interview_theater/__init__.py             core:session
interview_theater/ablauf.py               block:conversation-agent
interview_theater/anweisungen.py          core:instance
interview_theater/arbeitszeilen.py        surface:inscribe-app
interview_theater/aufnahme.py             stt:audio
interview_theater/auswahl.py              block:interview-questions
interview_theater/befehle.py              surface:telegram
interview_theater/begriffe.py             block:capture-terms
interview_theater/begriffsboard.py        block:capture-terms
interview_theater/begriffsboard_analyse.py  block:capture-terms
interview_theater/bot.py                  core:session
interview_theater/brainstorm.py           block:brainstorm-card
interview_theater/buehnenkarte.py         block:brainstorm-card
interview_theater/cothinker_status.py     block:status-line
interview_theater/db.py                   core:session
interview_theater/diskussion.py           block:condense
interview_theater/dramaturgie/__init__.py   block:dramaturgy-checks
interview_theater/dramaturgie/beleg.py      block:dramaturgy-checks
interview_theater/dramaturgie/bilanz.py     block:dramaturgy-checks
interview_theater/dramaturgie/fanout.py     block:dramaturgy-checks
interview_theater/dramaturgie/mechanik.py   block:dramaturgy-checks
interview_theater/dramaturgie/schleife.py   block:dramaturgy-checks
interview_theater/einstellungen.py        core:instance
interview_theater/entwurf.py              block:write-text
interview_theater/erkenner.py             block:conversation-agent
interview_theater/erkenner_werkzeuge.py   block:piece-model
interview_theater/fehlstellen.py          phase
interview_theater/formberater.py          block:forms-styles
interview_theater/fragen_auswertung.py    block:interview-questions
interview_theater/fragen_ki.py            block:interview-questions
interview_theater/handykarten.py          surface:inscribe-app
interview_theater/hintergrund.py          block:piece-model
interview_theater/journal.py              block:decisions-tasks-questions
interview_theater/karten_nachzug.py       block:piece-model
interview_theater/kartentreue.py          block:dramaturgy-checks
interview_theater/kernzitate.py           block:quote-anchoring
interview_theater/knoepfe/__init__.py     surface:inscribe-app
interview_theater/knoepfe/basis.py        surface:inscribe-app
interview_theater/knoepfe/figuren.py      block:characters
interview_theater/knoepfe/fragen.py       block:interview-questions
interview_theater/knoepfe/interviews.py   block:interview-round
interview_theater/knoepfe/stationen.py    phase
interview_theater/knoepfe/szenen.py       phase
interview_theater/knoepfe/texte.py        surface:inscribe-app
interview_theater/knoepfe/wirkung.py      surface:inscribe-app
interview_theater/kontext.py              block:conversation-agent
interview_theater/kosten.py               core:llm
interview_theater/kuerzung.py             block:write-text
interview_theater/kurzgeschichte.py       block:write-text
interview_theater/laengen.py              block:write-text
interview_theater/leitfaden.py            block:interview-questions
interview_theater/llm.py                  core:llm
interview_theater/modellwahl.py           core:llm
interview_theater/nachpass.py             block:write-text
interview_theater/nachspeichern.py        surface:inscribe-app
interview_theater/phasen.py               phase
interview_theater/phasen_summary.py       block:condense
interview_theater/phasentexte.py          phase
interview_theater/prueflauf.py            block:dramaturgy-checks
interview_theater/recherche.py            block:research
interview_theater/repo.py                 core:session
interview_theater/roadmap.py              phase
interview_theater/ruecknahme.py           core:session
interview_theater/schaerfung.py           block:quote-anchoring
interview_theater/skript_uebersetzung.py  block:translation
interview_theater/sprache.py              core:instance
interview_theater/sprachmessung.py        core:llm
interview_theater/sprachpass.py           block:write-text
interview_theater/sprachprofil.py         block:characters
interview_theater/sprachstil.py           block:characters
interview_theater/sprecher.py             block:characters
interview_theater/sprechweise.py          block:characters
interview_theater/stagescript.py          block:write-text
interview_theater/stile.py                block:forms-styles
interview_theater/strom.py                surface:inscribe-app
interview_theater/stt.py                  retire
interview_theater/stueck.py               block:piece-model
interview_theater/stueck_sicht.py         block:piece-model
interview_theater/stueckpruefung.py       block:dramaturgy-checks
interview_theater/szene.py                block:write-text
interview_theater/szene_claude.py         block:write-text
interview_theater/szenenfolge.py          block:piece-model
interview_theater/szenenkarte.py          block:piece-model
interview_theater/szenenkern.py           block:piece-model
interview_theater/telegram.py             surface:telegram
interview_theater/ueberarbeitung.py       block:write-text
interview_theater/uebersetzung.py         block:translation
interview_theater/verdichter.py           block:condense
interview_theater/vorschlag.py            block:decisions-tasks-questions
interview_theater/vorschlagssperre.py     block:decisions-tasks-questions
interview_theater/vorspann.py             block:export
interview_theater/web.py                  surface:inscribe-app
interview_theater/web_chat.py             surface:inscribe-app
interview_theater/web_daten.py            surface:inscribe-app
interview_theater/web_gestalt.py          surface:inscribe-app
interview_theater/web_grenze.py           surface:inscribe-app
interview_theater/web_kanal.py            surface:inscribe-app
interview_theater/web_pdf.py              block:export
interview_theater/web_schreiben.py        surface:inscribe-app
interview_theater/web_skript.py           block:export
interview_theater/web_vereint.py          surface:inscribe-app
interview_theater/workshop.py             core:instance
interview_theater/zitat.py                block:quote-anchoring
```

## File mapping — Kollektivgedächtnis (kollektivgedaechtnis, `HEAD`)

Scope: `kg/ kg2/ mirror/ stt_backends/`. The simulator (`sim/`, 25 files)
and `scripts/` (1 file) are tooling and are not mapped.

```inventory kollektivgedaechtnis
kg/__init__.py                            core:session
kg/__main__.py                            core:session
kg/bus.py                                 surface:external-display
kg/config.py                              core:instance
kg/core.py                                core:session
kg/db.py                                  core:session
kg/embeddings.py                          block:merge-terms
kg/export.py                              block:graph
kg/extraction.py                          block:capture-terms
kg/llm.py                                 core:llm
kg/merging.py                             block:merge-terms
kg/models.py                              core:session
kg/photos.py                              block:portrait
kg/pipeline.py                            phase
kg/segmentation.py                        block:voice-command
kg/server.py                              surface:external-display
kg/session.py                             block:interview-round
kg/sse.py                                 stt:api-client
kg/stop_intent.py                         block:voice-command
kg/store.py                               core:session
kg/stt_client.py                          stt:api-client
kg/telegram_bot.py                        surface:telegram
kg/transcript.py                          core:session
kg2/__init__.py                           core:session
kg2/__main__.py                           core:session
kg2/condense.py                           block:condense
kg2/config.py                             core:instance
kg2/cycle.py                              phase
kg2/db.py                                 core:session
kg2/graph_client.py                       block:graph
kg2/imagegen.py                           block:image-generation
kg2/models.py                             core:session
kg2/server.py                             surface:external-display
kg2/store.py                              core:session
kg2/trigger.py                            phase
kg2/watcher.py                            phase
kg2/weighting.py                          block:condense
mirror/__init__.py                        surface:guest-view
mirror/receiver.py                        surface:guest-view
mirror/uploader.py                        block:release
stt_backends/infomaniak_whisper_backend.py  retire
```
