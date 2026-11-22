#!/usr/bin/env python3
# CoThinker — the level-1 question set, asked once per finished block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The fixed question set of docs/entwurf-meeting-modi.md, "### Ebene 1 —
der Fragensatz je Block": ten noul (yes/no-probability) questions plus one
choice question (the meeting phase), asked of an LLM backend in ONE call
per finished speech block ("speculative fan-out" -- the extra questions
cost tokens, not extra round trips).

DATA ONLY. This module does not call anything and does not decide
anything -- `gateway/decide.py` (a later card) sends `QUESTIONS` to a
backend and turns the answers into a vector; `gateway/topic_detect.py`'s
`JevDetector` is the precedent for that call shape. Keeping the set here,
on its own, lets both the decider and the bench (`bench/run.py
--detector decide:...`, another later card) import the SAME questions
instead of each defining a slightly different one.

WHY A HASH. `QUESTIONS_VERSION` alone tells you a human bumped it; the
hash (`questions_hash()`) also catches an edit nobody remembered to bump
-- a question's wording changed and the version did not, which would
otherwise mix two different question sets under one version number in
the event trace. Both go into `stage-events.jsonl` (text-free, per
shared/events.py) as the measurement basis for later cards: which
questions actually separate anything (bench) is only comparable across
sessions if the trace also says which exact wording was asked.

IDS ARE GERMAN, deliberately: they ride along in the JSON object sent to
the LLM backend as literal question keys (see `gateway/topic_detect.py`,
`JEV_NOUL_ID = "neues_thema"`), and AGENTS.md's naming convention puts
anything that "goes into a prompt" in German, not English. They are also
ASCII-only tokens (`shared/events.py`'s `is_token()`): no umlaut, so a
question id can be written into the text-free event trace unchanged.

Question texts phrase `vorher` (the preceding blocks) and `neu` (the
current block) the way the design doc's table does, so the wording a
human reads in this file is the wording a reviewer of the design doc
would recognise.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence

# Bumped by a human when a question is added, removed or reworded on
# purpose. `questions_hash()` below is the machine-checked companion:
# it changes on ANY edit, bumped or not, so a silent wording change is
# still visible in the event trace even if nobody touched this constant.
QUESTIONS_VERSION = 1


@dataclass(frozen=True)
class Question:
    """One question of the level-1 set. Immutable -- the set is data,
    never mutated by a caller that only meant to read it.

    `choices` is empty for a `noul` question and the ordered tuple of
    values for a `choice` question. `purpose` is a short English note
    for readers of this file (which tag/action a `True`-ish answer
    triggers downstream) -- it is NOT part of the question's identity
    and deliberately excluded from `questions_hash()`: renaming a code
    comment must not look like a changed question to the event trace.
    """
    id: str
    kind: str                      # "noul" | "choice"
    text: str
    choices: tuple = ()
    purpose: str = ""


# The four phases a meeting moves through (design doc, choice question).
# `docs/entwurf-meeting-modi.md` names them in this order (einstieg is
# where a meeting starts), so the tuple order is not alphabetical.
PHASES = ("einstieg", "diskussion", "entscheidung", "abschluss")


QUESTIONS: tuple = (
    Question(
        id="neues_thema", kind="noul",
        text="Eroeffnet `neu` gegenueber `vorher` ein anderes Thema?",
        purpose="open a new topic block (today: topic, Bench C)"),
    Question(
        id="rueckkehr_thema", kind="noul",
        text="Kehrt `neu` zu einem Thema zurueck, ueber das frueher schon "
             "gesprochen wurde, statt zum Thema von `vorher`?",
        purpose="continue an earlier block instead of opening a new one"),
    Question(
        id="beschluss", kind="noul",
        text="Ist in `neu` etwas beschlossen oder zugesagt worden?",
        purpose="tag: decision"),
    Question(
        id="aufgabe", kind="noul",
        text="Wird in `neu` eine Aufgabe an jemanden vergeben?",
        purpose="tag: task"),
    Question(
        id="offene_frage", kind="noul",
        text="Bleibt nach `neu` eine Frage offen?",
        purpose="tag: open question"),
    Question(
        id="idee", kind="noul",
        text="Wird in `neu` eine Idee entwickelt, ohne dass sie "
             "beschlossen wird?",
        purpose="tag: idea (work-meeting mode)"),
    Question(
        id="recherche_kandidat", kind="noul",
        text="Wird in `neu` etwas genannt, das nachgeschlagen werden "
             "koennte (Begriff, Projekt, Person, Zahl)?",
        purpose="research candidate"),
    Question(
        id="dokument_kandidat", kind="noul",
        text="Verdient das Thema von `neu` ein eigenes Dokument?",
        purpose="document candidate"),
    Question(
        id="zuruf_robo", kind="noul",
        text="Ist `neu` ein Zuruf an Robo?",
        purpose="augments the wake word (bench: prefix false alarms)"),
    Question(
        id="zahl_frist_betrag", kind="noul",
        text="Wird in `neu` eine Zahl, eine Frist oder ein Betrag genannt?",
        purpose="verbatim into the protocol; (unbestaetigt) on STT doubt"),
    Question(
        id="phase", kind="choice",
        text="In welcher Phase ist der Termin gerade: einstieg, "
             "diskussion, entscheidung oder abschluss?",
        choices=PHASES,
        purpose="agenda position; triggers the closing summary"),
)


def questions_hash(questions: Sequence[Question] = QUESTIONS) -> str:
    """A short, stable, ASCII digest of the question set's IDENTITY.

    Identity is id + kind + text + choices, in the given order -- not
    `purpose` (a code comment) and not a resorted/deduplicated view: a
    reordering is itself a change worth seeing, since the backend call
    sends the questions as an ordered structure.

    Truncated to 16 hex characters: short enough for a compact event
    trace line, long enough that a collision between two DIFFERENT
    question sets is not a realistic worry for a handful of sessions.
    16 lowercase hex characters also satisfy `shared/events.py`'s
    `is_token()`, so this value can be written into the text-free trace
    unchanged, the same way `EventLog.hash_quote()` already does for a
    call-out quote.
    """
    canonical = [
        {"id": f.id, "kind": f.kind, "text": f.text,
         "choices": list(f.choices)}
        for f in questions
    ]
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# Precomputed for call sites that just want "the current hash" without
# caring about a hypothetical other question set (event trace, docs).
QUESTIONS_HASH = questions_hash()


# --- level 2 (MM-B): extra sets, asked in their OWN decide() call ---------
# Separate on purpose: QUESTIONS and QUESTIONS_HASH above stay exactly as
# they are, so every decisions-*.jsonl line keeps one comparable question
# set. These are asked only while level 2 needs them -- a question open on
# the stage, or the first minutes of the meeting -- by
# gateway/policy_runner.py through decide.decide().
MEETING_MODES = ("sitzung", "arbeit", "folge")   # == shared.minutes_format.MODES
CONFIRM_ID = "rueckfrage_bejaht"
MODE_ID = "termin_art"

CONFIRM_QUESTIONS: tuple = (
    Question(
        id=CONFIRM_ID, kind="noul",
        text="Bejaht `neu` die offene Rueckfrage an Robo (siehe "
             "`offene_fragen`)?",
        purpose="level 2: a yes to the open stage question runs the asked "
                "action"),
)

MODE_QUESTIONS: tuple = (
    Question(
        id=MODE_ID, kind="choice",
        text="Welche Art Termin ist das: sitzung, arbeit oder folge?",
        choices=MEETING_MODES,
        purpose="level 0 counter-check of --mode over the first minutes"),
)
