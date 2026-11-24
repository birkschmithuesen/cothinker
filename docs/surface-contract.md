# The surface contract

A surface (the stage, the External Display, or a new one) is designed and
built against `surfaces/contract/` — never against a self-made simulation
(ADR 0019). This document assumes no prior familiarity with the repository.

## Start fixture mode

```
pip install -r surfaces/contract/requirements.txt
python -m surfaces.contract --fixtures meeting
```

This starts the real server (the same FastAPI app, the same models a live
stage would use) and replays the invented "meeting" scenario from
`surfaces/contract/fixtures/meeting/` over it, in time-lapse: agenda items
advance, utterances carry a speaker, a decision and an open question land,
the speaker queue turns over, the meeting pauses and tasks are named at the
close — a few seconds apart, not real meeting time.

## Start live mode

```
pip install -r surfaces/contract/requirements.txt
python -m surfaces.contract --live <session dir>
```

Same URL paths as fixture mode (`/docs`, `/snapshot`, `/events`,
`/actions`), only the start flag differs. Instead of replaying a fixture
scenario, this reads the given session directory's `buehne.json` -- the
file `gateway.consumer.StageWriter` writes for a running meeting -- and
maps its content onto the same `StageStateSnapshot`/`ViewIntent` models
fixture mode already validates against. It is read-only: live mode never
writes `buehne.json`, the consumer stays its single writer.

Live mode polls the file every 0.3 s (well under the 2 s acceptance
bound) and, on every content change, diffs the previous mapped snapshot
against the new one. A field with an obvious owning view-intent kind
(`queue`, `voice`, `share`, `session_graph`) is pushed as that kind's intent
(`queue:room`, `voice:room`, `share:room`, `graph:session`); every other
changed field (headline, decisions, notices, the question slot, ...) is
folded into one `status` intent naming the changed fields, since most of
the snapshot has no view-intent kind of its own. A field the live core does
not fill yet stays empty, exactly as in fixture mode (`"x-status":
"planned"`).

`queue` and `voice` answer different questions and must not be read as
interchangeable: `queue` (the `speaker-queue` block) is who holds the
floor by TAPPING to join the queue -- it stays empty all meeting in an
online session where nobody uses that UI -- while `voice` (the
`voice-attribution` block) is who is actually talking right now, derived
from the audio track / STT speaker label, independent of anyone using the
queue at all. A room display meant to answer "who is speaking" should read
`voice.speaking`, not `queue.speaking`.

## Cross-origin access

Same-origin by default: a surface fetching `/snapshot` or `/events` from
another origin (e.g. a local dev server against
`?api=http://127.0.0.1:8000/`) is rejected by the browser unless you opt
in. Pass `--cors-origin <origin>` (repeatable) to either mode to allow it:

```
python -m surfaces.contract --fixtures meeting --cors-origin http://localhost:5173
```

No flag, no header -- the default behaviour is unchanged.

## Read `/docs`

Open `http://127.0.0.1:8000/docs` for the interactive OpenAPI documentation:
every field of the stage-state snapshot and every user action, each with a
description and an example. `GET /snapshot` returns the current snapshot;
`GET /events` is the server-sent-events push channel for view intents —
read `surfaces/contract/asyncapi.yaml` for its message shapes, since OpenAPI
only weakly describes a push channel.

A field marked `"x-status": "planned"` in the schema (e.g. the agenda's
`items`, or the `transcript:recent` and `tasks` view intents) is not wired
up in the live core yet; the extra also carries `"x-issue"`, the GitHub
issue that will fill it. The fixture still populates it, so you can design
against its shape today.

## Build

Build the surface against this server exactly as you would against the
live one: poll or cache `GET /snapshot`, subscribe to `GET /events` for
view intents, and — once your surface needs to answer back — `POST
/actions` with a user action from the contract (`choose`, `accept`,
`reject`, `edit`, `tap`, `voice`, `message`). A surface never invents its own field or
event; it renders what the schema says, or drops what it does not render
(ADR 0006) and logs the drop.

## Run the conformance test

A surface is done when it passes the conformance check (ADR 0019 point 4).
Run it against a URL or a local `index.html` -- no knowledge of this
repository's code required:

```
pip install -r surfaces/contract/requirements.txt
playwright install chromium
python -m surfaces.contract.check --surface surfaces/contract/examples/minimal/index.html
```

It starts the contract server in fixture mode on a free port, opens the
surface headless (Playwright Chromium) with `?api=<that server>/`,
records every network request and plays the whole "meeting" scenario
(pass `--scenario` for another one). For every intent kind, it checks the
page either changed visibly or logged `dropped <kind>`; that the page
requested nothing outside the contract and its own static files; that a
second replay with every `x-status: planned` field stripped to its zero
value does not break the page; and that there were no console errors.
Each check prints as `PASS`/`FAIL` with a one-line reason; a JSON report
and a screenshot per intent (1920x1080) are written to `--out` (default
`conformance-out`). Exit code 0 only if every check passes.

Sample output, running the check against the reference surface above:

```
PASS: fetches /snapshot -- GET /snapshot was requested
PASS: subscribes to /events -- GET /events was requested
PASS: requests nothing outside the contract -- every request stayed within the contract paths and the surface's own static files
PASS: intent 'prompt' renders or is dropped -- DOM changed after the event
PASS: intent 'timer' renders or is dropped -- DOM changed after the event
PASS: intent 'transcript:recent' renders or is dropped -- DOM changed after the event
PASS: intent 'timer' renders or is dropped -- DOM changed after the event
PASS: intent 'transcript:recent' renders or is dropped -- DOM changed after the event
PASS: intent 'transcript:recent' renders or is dropped -- DOM changed after the event
PASS: intent 'status' renders or is dropped -- DOM changed after the event
PASS: intent 'tasks' renders or is dropped -- DOM changed after the event
PASS: no console errors -- no console errors were logged
PASS: planned fields sent empty do not break the page -- no console errors with planned fields stripped
PASS: planned fields sent empty do not blank the screen -- the page still rendered content with planned fields stripped

PASS: overall
```

`surfaces/contract/examples/minimal/index.html` is the reference surface
above: a tiny, dependency-free page using only `fetch('/snapshot')` and
`EventSource('/events')` that renders every intent kind the "meeting"
fixture sends, and logs the drop for any kind it does not render. Use it
as a starting point, or to see what a passing surface looks like.

## Missing data

If your surface needs a field the contract does not have, open an issue
against this repository first (ADR 0019 point 5) — do not invent the field.
The core adds it additively: schema, example, fixture. Only then does the
surface use it.

## Keeping the contract in sync

`surfaces/contract/openapi.json` and `surfaces/contract/asyncapi.yaml` are
generated from `surfaces/contract/models.py` by
`python3 -m surfaces.contract.generate_spec`. A CI test
(`tests/test_surface_contract_spec.py`) fails when a model changes and the
committed files are not regenerated; run the generator and commit the
result whenever you change a model.
