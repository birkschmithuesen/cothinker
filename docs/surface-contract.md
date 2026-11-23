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
`reject`, `edit`, `tap`, `voice`). A surface never invents its own field or
event; it renders what the schema says, or drops what it does not render
(ADR 0006) and logs the drop.

## Run the conformance test

Once `tests/test_surface_conformance.py` exists (its own card, issue #132's
"Out" list), run it against your surface before calling it done: it loads
the surface headless against every fixture scenario and checks it renders
or explicitly drops every intent kind, requests nothing outside the
contract, and passes the skin rules and the design gate screenshots.

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
