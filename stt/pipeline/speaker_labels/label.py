#!/usr/bin/env python3
# CoThinker — Speechmatics diarization labels attributed to real names
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Name a Speechmatics diarization label (S1, S2, ...)
from what is said in a casual intro round -- no "Robo, start" command, no
ml-server speaker id (dropped; Speechmatics diarization is the only
source of a label here).

Two stages, deliberately separate (operator, 2026-10-27, measured on the
26.10. per-speaker transcript):

  RULE stage (this module, `rule_attribute`): first-person patterns only
  ("ich bin X" and friends, via speaker_enroll.extract_name -- not
  duplicated here), accepted ONLY when `X` matches an optional
  participant list. Deterministic, no network call, no LLM. Measured
  precision on first-person patterns alone: 100% (2/2), recall low (2/8)
  -- by design the rule stage is precise and leaves most labels unnamed.

  LLM stage (stt/pipeline/speaker_labels/label_llm.py): async, off the hot path,
  for the labels the rule stage could not decide. `mentions_participant`
  and `should_fire_llm` below are the deterministic CANDIDATE detector
  that decides whether the (expensive, careful) LLM call is worth
  making at all -- the same role gateway/correction_prefilter.py plays
  for the correction stage. On the 26.10. transcript "any name mention"
  alone was 53 hits, 48 of them someone naming SOMEBODY ELSE
  (addressing, hand-over, third-person) -- `mentions_participant` is
  exactly that broad, low-precision detector, used here ONLY to decide
  whether to ask the model, never to assign a name by itself.

`LabelAttribution` is the per-label store, with the conflict rule of
design decision point 3: a rule-stage assignment is final for its label
unless the rule itself changes it (re-introduction, same semantics as
speaker_enroll.EnrollmentSession's re-enrollment); an LLM verdict never
overrides an existing assignment (rule- or LLM-sourced) that names someone
else -- it is queued in `.pending` for the stage to show, never applied
silently.

Nothing in this module does I/O, touches the network, or raises on bad
input: it runs on the SSE loop's hot path exactly like
speaker_enroll.py's own functions, and the same rule holds here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .enroll import extract_name

_WORD_CHARS = r"[A-Za-zÄÖÜäöüß\-]+"


def _norm(s) -> str:
    return str(s or "").strip()


def match_participant(name: str, participants) -> str | None:
    """The participant list entry `name` refers to, or None.

    Matches the full entry ("Max Mueller") or just its first word
    ("Max") case-insensitively -- a spoken intro almost never carries a
    surname. Returns the CANONICAL participant string (as given in the
    list), not the spoken fragment, so the attributed name matches
    whatever the room already knows the person as (Jitsi display name,
    table-seat name).
    """
    key = _norm(name).casefold()
    if not key:
        return None
    for entry in participants or []:
        entry = _norm(entry)
        if not entry:
            continue
        if entry.casefold() == key:
            return entry
        first = entry.split(" ", 1)[0]
        if first.casefold() == key:
            return entry
    return None


def rule_attribute(text: str, participants=None) -> str | None:
    """The first-person name in `text`, restricted to `participants`.

    `participants` is OPTIONAL (design decision point 1: "accepted ONLY
    if it matches an optional participant list"): given a list (possibly
    empty), a name must match it or nothing is returned; given None (no
    list configured at all), any extracted name is accepted -- the same
    unrestricted behaviour speaker_enroll.extract_name has always had,
    which the voice-embedding enrollment path still relies on.
    """
    name = extract_name(text)
    if not name:
        return None
    if participants is None:
        return name
    return match_participant(name, participants)


_NAME_TOKEN = re.compile(_WORD_CHARS)


def mentions_participant(text: str, participants) -> list[str]:
    """Every participant mentioned ANYWHERE in `text`, in order of
    appearance -- first person, third person, addressing, hand-over, all
    of it. Deliberately broad and low-precision (measured 48/53 false
    positives on the 26.10. transcript): used ONLY to decide whether the
    LLM candidate stage is worth asking, never to assign a name.

    A participant matches on its first word, word-boundary only ("Klaus"
    inside "unglaublich kompliziert" does not match).
    """
    text = str(text or "")
    tokens_lower = {m.group(0).casefold(): m.start()
                    for m in _NAME_TOKEN.finditer(text)}
    hits = []
    for entry in participants or []:
        entry = _norm(entry)
        if not entry:
            continue
        first = entry.split(" ", 1)[0].casefold()
        if first in tokens_lower:
            hits.append((tokens_lower[first], entry))
    hits.sort(key=lambda t: t[0])
    return [name for _pos, name in hits]


@dataclass
class Assignment:
    name: str
    source: str              # "rule" | "llm"
    confidence: str = ""     # "" for a rule hit, else the LLM's own label


@dataclass
class PendingVerdict:
    """One LLM verdict that was NOT auto-applied -- surfaced for a human
    to decide (design decision point 2 and 3), never resolved here."""
    label: str
    proposed_name: str
    confidence: str
    kind: str                # "conflict" | "suggestion"
    existing_name: str = ""
    evidence: str = ""


class LabelAttribution:
    """label -> name, built from the rule stage and accepted LLM verdicts.

    `assign_rule()` always wins: it is the deterministic stage, and a
    second rule hit for the same label (a re-introduction) is allowed to
    change it, same as EnrollmentSession's re-enrollment semantics.
    `apply_llm_verdict()` never overrides an existing assignment with a
    different name -- it only ever ADDS to `.pending`, which the caller
    (gateway/consumer.py) turns into a stage notice; nothing here decides
    a conflict on its own.
    """

    def __init__(self) -> None:
        self._by_label: dict[str, Assignment] = {}
        self.pending: list[PendingVerdict] = []

    def name_for(self, label) -> str | None:
        a = self._by_label.get(_norm(label))
        return a.name if a else None

    def assign_rule(self, label: str, name: str) -> None:
        label, name = _norm(label), _norm(name)
        if not label or not name:
            return
        self._by_label[label] = Assignment(name=name, source="rule")

    def apply_llm_verdict(self, label: str, name: str, confidence: str,
                          evidence: str = "") -> str:
        """Returns "assigned", "confirmed", "conflict" or "suggestion".

        Never raises, never silently overwrites: see the class docstring.
        """
        label, name = _norm(label), _norm(name)
        if not label or not name:
            return "ignored"
        existing = self._by_label.get(label)
        if existing is not None and existing.name != name:
            self.pending.append(PendingVerdict(
                label=label, proposed_name=name, confidence=confidence,
                kind="conflict", existing_name=existing.name,
                evidence=evidence))
            return "conflict"
        if confidence != "high":
            if existing is None:
                self.pending.append(PendingVerdict(
                    label=label, proposed_name=name, confidence=confidence,
                    kind="suggestion", evidence=evidence))
            return "suggestion"
        if existing is not None:
            return "confirmed"
        self._by_label[label] = Assignment(name=name, source="llm",
                                           confidence=confidence)
        return "assigned"


def notice_text(verdict: PendingVerdict) -> str:
    """The German stage-notice line for one pending verdict (AGENTS.md:
    text that lands on the stage is German)."""
    if verdict.kind == "conflict":
        return (f"**{verdict.label}**: bereits **{verdict.existing_name}** "
                f"zugeordnet — Vorschlag **{verdict.proposed_name}** "
                f"({verdict.confidence}) steht dagegen, bitte bestätigen")
    return (f"**{verdict.label}**: Vorschlag **{verdict.proposed_name}** "
            f"({verdict.confidence}) — nicht automatisch zugeordnet")


class AttributionWindow:
    """The last `max_len` (label, text) finals, oldest first -- the
    context the LLM candidate stage reads (design decision point 2:
    "~6 surrounding finals"). Pure ring buffer, no I/O."""

    def __init__(self, max_len: int = 6) -> None:
        self.max_len = max_len
        self._entries: list[dict] = []
        self._next_index = 0

    def push(self, label: str, text: str) -> None:
        self._entries.append({"index": self._next_index,
                              "label": _norm(label), "text": str(text or "")})
        self._next_index += 1
        if len(self._entries) > self.max_len:
            self._entries = self._entries[-self.max_len:]

    def snapshot(self) -> list[dict]:
        return list(self._entries)

    def unnamed_labels(self, attribution: LabelAttribution) -> list[str]:
        """Labels in the window that `attribution` has no name for yet,
        in first-seen order, each listed once."""
        seen, out = set(), []
        for entry in self._entries:
            label = entry["label"]
            if label and label not in seen and attribution.name_for(label) is None:
                seen.add(label)
                out.append(label)
        return out


def should_fire_llm(window: AttributionWindow, participants,
                    attribution: LabelAttribution) -> bool:
    """Deterministic prefilter: worth an LLM candidate call right now?

    Called AFTER the current final has been pushed onto `window` -- the
    window handed to the model has to include the triggering utterance
    itself (the hand-over, the third-person mention), not just what came
    before it.

    Fires only when (a) the broad detector finds at least one
    participant mentioned in the MOST RECENT final who is not already
    assigned to some label, AND (b) at least one label in the window
    (the current utterance's own label counts) is still unnamed -- there
    has to be both a name on the table and a blank to put it in. Neither
    condition alone is enough: a mention of an already-named person
    resolves nothing, and an unnamed label with nobody mentioned gives
    the model nothing to go on.
    """
    entries = window.snapshot()
    if not entries:
        return False
    current = entries[-1]
    mentioned = mentions_participant(current["text"], participants)
    if not mentioned:
        return False
    already_named = {attribution.name_for(e["label"]) for e in entries}
    already_named.discard(None)
    candidates = [n for n in mentioned if n not in already_named]
    if not candidates:
        return False
    return bool(window.unnamed_labels(attribution))
