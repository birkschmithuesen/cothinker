#!/usr/bin/env python3
# CoThinker — the level-1 decision interface: one call per block
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Ask the level-1 question set (gateway/questions.py) about one finished
speech block, in ONE backend call, and get numbers back.

    decide(state, questions=QUESTIONS, *, backend=None) -> Result

docs/entwurf-meeting-modi.md, "### Ebene 1 — der Fragensatz je Block".
Every question sees `vorher` (the last blocks) and `neu` (the current
block), never the future. All questions ride in the same request
("speculative fan-out"): an extra question costs tokens, not a round trip.

BACKENDS, chosen by `COTHINKER_DECIDER` (`backend_from_env()`):
  jev   OpenRouter's typed decisions endpoint, gateway/openrouter_jev.py.
        A US provider: selected ONLY by the env var somebody sets on
        purpose, imported lazily, and a failure RAISES -- there is no
        fallback to anything (AGENTS.md: no automatic switch to the US).
  fake  deterministic answers from a hash of block text + question id.
        No network. For tests and for wiring the consumer without cost.
  laya  a stub -- NotImplementedError pointing at the tracking card
        (`LAYA_CARD` below).
  script  deterministic answers from a JSON rule file -- tests and dry
        rehearsals, no network. The file is named by
        `COTHINKER_DECIDER_SCRIPT` (`ScriptBackend`).
  unset the decider is off: `backend_from_env()` returns None.

ANSWERS ARE NUMBERS, never text. `Result.answers` has exactly one entry
per question asked, in the set's order:
  noul    {"question_id", "p", "confidence", "ms"}
  choice  {"question_id", "choice", "confidence", "ms"}
  gap     {"question_id", "error", "ms"}   error: missing|no_noul|bad_choice
A gap is marked, never guessed: a 0.0 for an unanswered noul would be
read downstream as a firm "no". `confidence` is a float in 0..1 or None
when the backend did not send a usable one. `choice` is always one of the
question's own `choices`. `ms` per answer is the one round trip they all
shared.

TOKENS ARE MEASURED, AND BOUNDED BEFORE SENDING. `build_request()`
estimates the request deterministically (JSON length / CHARS_PER_TOKEN).
Over TARGET_TOKENS it drops the OLDEST previous blocks first, then trims
the TAIL of the current block (its head is what the questions are
about), not below MIN_NEU_CHARS; it records that as numbers only
(`truncated`, `dropped_blocks`, `trimmed_chars`). If the request is still
over MAX_TOKENS it raises `DecideError("over_limit")` -- never a silent
send. `Result.tokens_in` is the provider's `usage.input_tokens` when it
reports one, else the estimate; `tokens_est` is always the estimate, so
the two can be compared per call.

ERRORS carry a token (`DecideError.token`, 1-32 ASCII chars: `http_402`,
`timeout`, `no_key`, `over_limit`, ...) and never a response body, never
block text, never the key -- the same rule as openrouter_jev.JevError.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

from questions import QUESTIONS, QUESTIONS_VERSION, Question, questions_hash

DEFAULT_CONTEXT_BLOCKS = 5
TOPIC_TITLES = 3
TARGET_TOKENS = 3000
MAX_TOKENS = 32000
# Conservative for German text inside JSON (real tokenizers land nearer
# 3.5-4 chars per token): an estimate that over-counts truncates a little
# early; one that under-counts could let a request past the hard limit.
CHARS_PER_TOKEN = 3
# The current block is never trimmed below this: without its head the
# questions have nothing to be about. Same size as topic_detect's
# JEV_NEU_CHARS.
MIN_NEU_CHARS = 600
ENV_VAR = "COTHINKER_DECIDER"
SCRIPT_ENV_VAR = "COTHINKER_DECIDER_SCRIPT"
BACKENDS = ("jev", "laya", "fake", "script")
LAYA_CARD = "the laya backend tracking issue"

# Criteria texts for choice questions, keyed by question id then by the
# choice value. German: they go into the request (AGENTS.md naming rule).
# A choice value without an entry here is sent with its own name as text.
CHOICE_CRITERIA = {
    "phase": {
        "einstieg": "Der Termin beginnt: Begruessung, Anlass, Agenda, wer "
                    "ist da.",
        "diskussion": "Ein Thema wird besprochen, Argumente und Ideen "
                      "werden ausgetauscht.",
        "entscheidung": "Es wird auf einen Beschluss oder eine Zusage "
                        "zugesteuert oder sie wird gefasst.",
        "abschluss": "Der Termin endet: Zusammenfassung, naechste "
                     "Schritte, Verabschiedung.",
    },
    "termin_art": {
        "sitzung": "Ein Thema nach dem anderen, Beschluesse, Zusagen.",
        "arbeit": "Was waere wenn, viele neue Begriffe, keine Beschluesse.",
        "folge": "Es wird an einen frueheren Termin angeknuepft, Begriffe "
                 "von dort tauchen frueh wieder auf.",
    },
}


class DecideError(Exception):
    """A call could not be made or did not come back usably.

    `token` is safe for the text-free event trace (gateway/events.py):
    1-32 ASCII letters, digits, `_` or `-`. The message is built from the
    token and numbers only.
    """

    def __init__(self, message: str, token: str) -> None:
        super().__init__(message)
        self.token = _safe_token(token)


def _safe_token(wert) -> str:
    if isinstance(wert, str) and 0 < len(wert) <= 32 and all(
            (c.isascii() and c.isalnum()) or c in "_-" for c in wert):
        return wert
    return "error"


def _error_token(fehler) -> str:
    """A client's own clean token, else the exception's type name."""
    token = getattr(fehler, "token", "")
    return token if _safe_token(token) == token else \
        _safe_token(type(fehler).__name__)


# --------------------------------------------------------------- the state

@dataclass
class State:
    """What the questions are asked about. Held by the caller, one per
    session; `push()` moves on to the next finished block.

    `previous` may hold more than `context_blocks` entries -- only the
    newest `context_blocks` are ever sent. `topics` likewise: only the
    last TOPIC_TITLES titles are sent.
    """
    block: str = ""
    previous: list = field(default_factory=list)
    heading: str = ""
    topics: list = field(default_factory=list)
    hints: list = field(default_factory=list)
    mode: str = ""
    context_blocks: int = DEFAULT_CONTEXT_BLOCKS

    def push(self, text: str) -> None:
        """`text` becomes the current block; the old one joins `previous`,
        which is kept at `context_blocks` entries."""
        if self.block:
            self.previous.append(self.block)
        self.block = text
        n = max(0, self.context_blocks)
        del self.previous[:max(0, len(self.previous) - n)]

    def window(self) -> list:
        n = max(0, self.context_blocks)
        return list(self.previous[-n:]) if n else []


# ------------------------------------------------------- the request builder

@dataclass
class Request:
    """One built request: what is sent, its estimate, what was cut."""
    state: dict
    questions: dict
    tokens_est: int
    dropped_blocks: int = 0
    trimmed_chars: int = 0

    @property
    def truncated(self) -> bool:
        return bool(self.dropped_blocks or self.trimmed_chars)


def questions_payload(questions: Sequence[Question]) -> dict:
    """The `questions` object of the decisions API, in set order."""
    out: dict = {}
    for f in questions:
        if f.kind == "choice":
            texte = CHOICE_CRITERIA.get(f.id, {})
            out[f.id] = {"type": "choice", "instructions": f.text,
                         "criteria": {c: texte.get(c, c) for c in f.choices}}
        else:
            out[f.id] = {"type": "noul", "instructions": f.text}
    return out


def _state_payload(state: State, window: list, neu: str) -> dict:
    """German keys: they are read by the model next to the question texts,
    which speak of `vorher` and `neu`. Optional fields only when set."""
    out = {"neu": neu, "vorher": "\n\n".join(t for t in window if t)}
    if state.heading:
        out["ueberschrift"] = state.heading
    themen = list(state.topics[-TOPIC_TITLES:])
    if themen:
        out["letzte_themen"] = themen
    if state.hints:
        out["offene_fragen"] = list(state.hints)
    if state.mode:
        out["modus"] = state.mode
    return out


def estimate_tokens(state: dict, questions: dict) -> int:
    """Deterministic pre-send estimate of a request's input tokens."""
    roh = json.dumps({"state": state, "questions": questions},
                     ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(roh) / CHARS_PER_TOKEN)


def build_request(state: State, questions: Sequence[Question] = QUESTIONS, *,
                  target_tokens: int = TARGET_TOKENS,
                  max_tokens: int = MAX_TOKENS) -> Request:
    """Build the request, truncating toward `target_tokens`; raise
    `DecideError("over_limit")` if it still exceeds `max_tokens`."""
    fragen = questions_payload(questions)
    fenster = state.window()
    neu = state.block or ""
    gedroppt = 0
    zustand = _state_payload(state, fenster, neu)
    schaetzung = estimate_tokens(zustand, fragen)
    # 1. the oldest previous blocks go first
    while schaetzung > target_tokens and fenster:
        fenster = fenster[1:]
        gedroppt += 1
        zustand = _state_payload(state, fenster, neu)
        schaetzung = estimate_tokens(zustand, fragen)
    # 2. then the tail of the current block, never below MIN_NEU_CHARS
    while schaetzung > target_tokens and len(neu) > MIN_NEU_CHARS:
        zuviel = (schaetzung - target_tokens) * CHARS_PER_TOKEN
        neu = neu[:max(MIN_NEU_CHARS, len(neu) - max(1, zuviel))]
        zustand = _state_payload(state, fenster, neu)
        schaetzung = estimate_tokens(zustand, fragen)
    if schaetzung > max_tokens:
        raise DecideError(f"request needs ~{schaetzung} tokens even when "
                          f"truncated, limit {max_tokens}", "over_limit")
    return Request(state=zustand, questions=fragen, tokens_est=schaetzung,
                   dropped_blocks=gedroppt,
                   trimmed_chars=len(state.block or "") - len(neu))


# ----------------------------------------------------------------- backends

@dataclass
class Reply:
    """What a backend hands back: answers keyed by question id in the
    decisions API's shape (`noul`, `choice`, `confidence`), plus usage.
    A missing id means "not answered"."""
    answers: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    ms: float = 0.0
    model: str = ""


class Backend:
    """`ask(state, questions) -> Reply` for one built request."""
    name = ""

    def ask(self, state: dict, questions: dict) -> Reply:
        raise NotImplementedError


def _unit(text: str, salt: str) -> float:
    h = hashlib.sha256(f"{salt}\x00{text}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(2 ** 64)


class FakeBackend(Backend):
    """Deterministic answers from sha256(question id + block). No network,
    no cost; tokens_in falls back to the estimate."""
    name = "fake"

    def __repr__(self) -> str:
        return "FakeBackend()"

    def ask(self, state: dict, questions: dict) -> Reply:
        t0 = time.monotonic()
        neu = str(state.get("neu", ""))
        antworten: dict = {}
        for qid, frage in questions.items():
            konf = round(0.5 + 0.5 * _unit(neu, f"{qid}:c"), 4)
            if frage.get("type") == "choice":
                werte = list(frage.get("criteria") or {})
                if werte:
                    i = int(_unit(neu, qid) * len(werte)) % len(werte)
                    antworten[qid] = {"type": "choice", "choice": werte[i],
                                      "confidence": konf}
            else:
                antworten[qid] = {"type": "noul",
                                  "noul": round(_unit(neu, qid), 4),
                                  "confidence": konf}
        return Reply(answers=antworten,
                     ms=round((time.monotonic() - t0) * 1000.0, 3),
                     model="fake")


class ScriptBackend(Backend):
    """Deterministic answers from a JSON rule file. No network, no cost.

        {"default": 0.0,
         "rules": [{"match": "<substring of neu>",
                    "answers": {"<question id>": <p> | "<choice>"}}]}

    For each question asked, the FIRST rule whose `match` occurs in
    `neu` AND that names the question decides it: a number becomes the
    noul `p`, a string the choice -- kept only when it is one of the
    question's own criteria, else left out (a gap). A noul no rule
    decides gets `default`; a choice no rule decides is left out. The
    file is read on every `ask()`, so a test can change it mid-run.

    An unreadable or malformed file raises `DecideError("no_script")`;
    neither the message nor the token ever carries the file's content.
    """
    name = "script"

    def __init__(self, path) -> None:
        if not path:
            raise DecideError(f"{SCRIPT_ENV_VAR} is unset", "no_script")
        self.path = str(path)

    def __repr__(self) -> str:
        return "ScriptBackend()"

    __str__ = __repr__

    def _load(self) -> tuple:
        try:
            with open(self.path, encoding="utf-8") as fh:
                roh = json.load(fh)
        except (OSError, ValueError, UnicodeError):
            roh = None
        regeln = roh.get("rules", []) if isinstance(roh, dict) else None
        default = (roh.get("default", 0.0) if isinstance(roh, dict)
                   else None)
        if (not isinstance(regeln, list) or isinstance(default, bool)
                or not isinstance(default, (int, float))):
            raise DecideError("script unreadable", "no_script")
        return float(default), [r for r in regeln if isinstance(r, dict)
                                and isinstance(r.get("match"), str)
                                and isinstance(r.get("answers"), dict)]

    def ask(self, state: dict, questions: dict) -> Reply:
        t0 = time.monotonic()
        default, regeln = self._load()
        neu = str(state.get("neu", ""))
        treffer = [r["answers"] for r in regeln if r["match"] in neu]
        antworten: dict = {}
        for qid, frage in questions.items():
            wert = next((a[qid] for a in treffer if qid in a), None)
            if frage.get("type") == "choice":
                if isinstance(wert, str) and wert in (frage.get("criteria")
                                                      or {}):
                    antworten[qid] = {"type": "choice", "choice": wert,
                                      "confidence": 0.9}
            else:
                if isinstance(wert, bool) or not isinstance(
                        wert, (int, float)):
                    wert = default
                antworten[qid] = {"type": "noul", "noul": float(wert),
                                  "confidence": 0.9}
        return Reply(answers=antworten,
                     ms=round((time.monotonic() - t0) * 1000.0, 3),
                     model="script")


class LayaBackend(Backend):
    """Stub. The local laya worker as a decider is tracked by `LAYA_CARD`."""
    name = "laya"

    def __init__(self, *a, **kw) -> None:
        raise NotImplementedError(
            f"the laya decider backend is not built yet -- see card "
            f"{LAYA_CARD}; use COTHINKER_DECIDER=jev or fake")


class JevBackend(Backend):
    """Jev (TypeSafe) over OpenRouter's decisions endpoint.

    The key is handed to gateway/openrouter_jev.Client and kept only
    there; this object's repr names the model and URL, nothing else.
    """
    name = "jev"

    def __init__(self, api_key: str, *, model: str = "",
                 base_url: str = "", timeout_s: float = 30.0) -> None:
        # Lazy on purpose: the live path does not load the US client
        # unless somebody chose it by setting COTHINKER_DECIDER=jev.
        import openrouter_jev as boj
        self._boj = boj
        kw = {"timeout_s": timeout_s}
        if model:
            kw["model"] = model
        if base_url:
            kw["base_url"] = base_url
        try:
            self.client = boj.Client(api_key, **kw)
        except boj.JevError as e:
            raise DecideError(f"jev backend unusable ({e.token})",
                              e.token) from None

    def __repr__(self) -> str:
        return f"JevBackend({self.client!r})"

    __str__ = __repr__

    def ask(self, state: dict, questions: dict) -> Reply:
        # JEV-SITE: the ONE call per block -- all level-1 questions in a
        # single request; a missing answer is left out (require_all=False)
        # so decide() can mark that one question as a gap.
        try:
            antwort = self.client.decide(state, questions, require_all=False)
        except self._boj.JevError as e:
            raise DecideError(f"jev call failed ({e.token})",
                              e.token) from None
        return Reply(answers=dict(antwort.answers),
                     input_tokens=int(antwort.input_tokens),
                     output_tokens=int(antwort.output_tokens),
                     cost_usd=float(antwort.cost_usd), ms=float(antwort.ms),
                     model=antwort.model)


def backend_from_env(env=None) -> Optional[Backend]:
    """The backend `COTHINKER_DECIDER` names, or None when it is unset.

    jev reads `OPENROUTER_API_KEY` from the same environment -- never a
    default, never an argument (see openrouter_jev's docstring)."""
    env = os.environ if env is None else env
    wahl = (env.get(ENV_VAR) or "").strip().lower()
    if not wahl:
        return None
    if wahl == "fake":
        return FakeBackend()
    if wahl == "laya":
        return LayaBackend()
    if wahl == "jev":
        return JevBackend(env.get("OPENROUTER_API_KEY") or "")
    if wahl == "script":
        return ScriptBackend(env.get(SCRIPT_ENV_VAR) or "")
    raise ValueError(f"{ENV_VAR}={wahl[:20]!r} is not a decider backend; "
                     f"use one of {', '.join(BACKENDS)} or leave it unset")


# ------------------------------------------------------------ the decision

@dataclass
class Result:
    """One block's answers plus the numbers of the call. Text-free."""
    answers: list
    backend: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_est: int = 0
    cost_usd: float = 0.0
    ms: float = 0.0
    model: str = ""
    truncated: bool = False
    dropped_blocks: int = 0
    trimmed_chars: int = 0
    errors: int = 0
    questions_version: int = QUESTIONS_VERSION
    questions_hash: str = ""

    def by_id(self) -> dict:
        return {a["question_id"]: a for a in self.answers}


def _fraction(wert) -> Optional[float]:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    wert = float(wert)
    if not math.isfinite(wert):
        return None
    return min(1.0, max(0.0, wert))


def interpret(questions: Sequence[Question], answers: dict,
              ms: float) -> list:
    """Backend answers -> one structured entry per question, in order."""
    out = []
    for f in questions:
        roh = answers.get(f.id)
        eintrag: dict = {"question_id": f.id}
        # JEV-SITE: an absent or empty answer is a gap ("missing"), not a
        # "no" -- nothing is filled in for it.
        if not isinstance(roh, dict) or not roh:
            eintrag["error"] = "missing"
        elif f.kind == "choice":
            wahl = roh.get("choice")
            # JEV-SITE: only one of the question's own choices counts; any
            # other value (or text) becomes bad_choice and is not kept.
            if isinstance(wahl, str) and wahl in f.choices:
                eintrag["choice"] = wahl
                # JEV-SITE: confidence kept as a number in 0..1, else None.
                eintrag["confidence"] = _fraction(roh.get("confidence"))
            else:
                eintrag["error"] = "bad_choice"
        else:
            # JEV-SITE: `noul` IS the probability p, clamped into 0..1; a
            # string, bool or non-finite value is a gap (no_noul).
            p = _fraction(roh.get("noul"))
            if p is None:
                eintrag["error"] = "no_noul"
            else:
                eintrag["p"] = p
                # JEV-SITE: confidence kept as a number in 0..1, else None.
                eintrag["confidence"] = _fraction(roh.get("confidence"))
        eintrag["ms"] = ms
        out.append(eintrag)
    return out


def decide(state: State, questions: Sequence[Question] = QUESTIONS, *,
           backend: Optional[Backend] = None, env=None,
           target_tokens: int = TARGET_TOKENS,
           max_tokens: int = MAX_TOKENS) -> Result:
    """Ask `questions` about `state` in one backend call.

    `backend=None` selects one from `env` (default os.environ); if that is
    off too, `DecideError("decider_off")`. Call-level failures (transport,
    HTTP status, over_limit, no_key) raise DecideError with a token;
    per-question problems are marked in the entry instead.
    """
    t0 = time.monotonic()
    if backend is None:
        backend = backend_from_env(env)
    if backend is None:
        raise DecideError(f"no decider backend -- {ENV_VAR} is unset",
                          "decider_off")
    anfrage = build_request(state, questions, target_tokens=target_tokens,
                            max_tokens=max_tokens)
    try:
        antwort = backend.ask(anfrage.state, anfrage.questions)
    except (DecideError, NotImplementedError):
        raise
    except Exception as fehler:          # noqa: BLE001
        token = _error_token(fehler)
        raise DecideError(f"decider backend failed ({token})",
                          token) from None
    eintraege = interpret(questions, antwort.answers, float(antwort.ms))
    # JEV-SITE: usage -- measured input tokens when the provider reports
    # them, the pre-send estimate otherwise; cost is usage.cost in USD.
    return Result(
        answers=eintraege,
        backend=backend.name,
        tokens_in=antwort.input_tokens or anfrage.tokens_est,
        tokens_out=antwort.output_tokens,
        tokens_est=anfrage.tokens_est,
        cost_usd=antwort.cost_usd,
        ms=round((time.monotonic() - t0) * 1000.0, 3),
        model=antwort.model,
        truncated=anfrage.truncated,
        dropped_blocks=anfrage.dropped_blocks,
        trimmed_chars=anfrage.trimmed_chars,
        errors=sum(1 for e in eintraege if "error" in e),
        questions_version=QUESTIONS_VERSION,
        questions_hash=questions_hash(questions))
