#!/usr/bin/env python3
# CoThinker — the OpenRouter Decisions client (TypeSafe Jev)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""One POST to OpenRouter's typed decisions endpoint, over urllib.

    POST <base>/api/alpha/decisions
    {"model": "typesafe/jev-1.13", "state": {...}, "questions": {...}}

OPT-IN ONLY. Jev is served by a US provider through a US router. The
bench uses it when a flag or `COTHINKER_DECIDER=jev` names it; the live
path reaches it only through the level-1 decider, and only when
`COTHINKER_DECIDER=jev` is set -- then block text, heading, topic titles
and the open question go to OpenRouter. Nothing may start using it
without such a deliberate decision: AGENTS.md forbids any automatic
switch to a US service. gateway/decide.py imports it LAZILY, only for
the jev backend, and a failed call there raises; it never falls back to
anything else.

`decide()` RETURNS A TYPED `Decision`, not the raw response dict. The
raw answer can carry anything the provider chooses to send; the typed
one carries numbers and the question/criterion keys WE sent, and nothing
else. That is what makes it safe to cache in the vault and to hand to a
bench that prints: a field this module does not know is dropped here,
not passed on.

ERROR MESSAGES CARRY NO RESPONSE BODY. An error answer is
`{"error": {"code": int, "message": str}}`, and the message can quote
the request -- which is speech of third parties. `JevError` therefore
carries the HTTP status and the numeric `code` at most, and a short
TOKEN (`http_402`, `timeout`, `URLError`, `bad_json`, ...) that a
detector puts on its `Score` unchanged. The same rule as
gateway/infomaniak_embed.py's `EmbedError`.

THE KEY NEVER LEAVES THIS OBJECT. It comes from the environment (the
caller reads `OPENROUTER_API_KEY`), it is not a constructor default and
not a CLI argument -- an argument stands in every process list on this
machine, and this repo reads process lists
(bench/session.read_processes()) -- and `__repr__` hides it.
"""
from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = "https://openrouter.ai"
DEFAULT_MODEL = "typesafe/jev-1.13"
DECISIONS_PATH = "/api/alpha/decisions"
# Answer fields kept per answer type. Everything else an answer carries is
# dropped by `clean_answer()` -- see the module docstring.
_NUMBER_FIELDS = ("noul", "confidence")
_TOKEN_MAX = 64


class JevError(Exception):
    """The endpoint was not reachable, or did not answer usably.

    `token` is what a detector writes into `Score.error`: 1-32 ASCII
    characters, a status or a type name, never text from the answer.
    """

    def __init__(self, message: str, token: str) -> None:
        super().__init__(message)
        self.token = token[:32]


@dataclass
class Decision:
    """One answered request. Numbers and our own keys only."""
    answers: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    ms: float = 0.0
    model: str = ""

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _number(wert):
    """A float for an int/float that is not a bool, else None."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return float(wert)


def _token(wert, extra: str = "") -> str:
    """A short key-like string, or "" for anything else."""
    if not isinstance(wert, str) or not wert or len(wert) > _TOKEN_MAX:
        return ""
    erlaubt = "_-." + extra
    return wert if all((c.isascii() and c.isalnum()) or c in erlaubt
                       for c in wert) else ""


def clean_answer(antwort) -> dict:
    """One answer reduced to the fields a detector reads.

    Kept: `type`, the numeric `noul` and `confidence`, `choice` and the
    `probabilities` map -- but a `choice` or a probability key only if it
    is a short key-like token, which the criteria keys we send are. A
    free-text field the provider might add later is dropped.
    """
    # JEV-SITE: an answer that is not an object carries nothing usable --
    # it becomes {} and the detector reports the gap (no_noul).
    if not isinstance(antwort, dict):
        return {}
    out: dict = {}
    art = _token(antwort.get("type"))
    if art:
        out["type"] = art
    # JEV-SITE: `noul` and `confidence` are kept only as real numbers; a
    # string or a bool is dropped, never coerced into a probability.
    for name in _NUMBER_FIELDS:
        zahl = _number(antwort.get(name))
        if zahl is not None:
            out[name] = zahl
    # JEV-SITE: a `choice` survives only as a short key-like token -- a
    # free-text choice could carry speech and is dropped here.
    wahl = _token(antwort.get("choice"))
    if wahl:
        out["choice"] = wahl
    wahrsch = antwort.get("probabilities")
    # JEV-SITE: probabilities keep key-like keys with numeric values only.
    if isinstance(wahrsch, dict):
        sauber = {}
        for k, v in wahrsch.items():
            zahl = _number(v)
            if _token(k) and zahl is not None:
                sauber[k] = zahl
        out["probabilities"] = sauber
    return out


def _error_code(roh: bytes):
    """The numeric `error.code` of an error body, or None. Nothing else."""
    try:
        obj = json.loads(roh.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    fehler = obj.get("error") if isinstance(obj, dict) else None
    code = fehler.get("code") if isinstance(fehler, dict) else None
    return int(code) if isinstance(code, int) and not isinstance(code, bool) \
        else None


def _is_timeout(e) -> bool:
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True
    grund = getattr(e, "reason", None)
    return isinstance(grund, (socket.timeout, TimeoutError))


class Client:
    """A single-endpoint client. `calls` counts every attempted request."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *,
                 base_url: str = DEFAULT_BASE,
                 timeout_s: float = 60.0) -> None:
        if not api_key:
            raise JevError("no key -- set OPENROUTER_API_KEY in the "
                           "environment", "no_key")
        self._key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.calls = 0

    def __repr__(self) -> str:
        return (f"Client(model={self.model!r}, url={self.url()!r}, "
                f"calls={self.calls})")

    def url(self) -> str:
        return f"{self.base_url}{DECISIONS_PATH}"

    def decide(self, state, questions: dict, *,
               require_all: bool = True) -> Decision:
        """Ask `questions` about `state`. A `Decision` or a `JevError`.

        Every question id sent must come back with an answer; a missing
        one is an error (`missing_answer`), because a detector that got
        half an answer would otherwise score a gap as a "no".

        `require_all=False` (gateway/decide.py, a whole question set per
        call) keeps the answered ids and simply leaves a missing one out
        of `answers`: the caller marks THAT question as a gap instead of
        losing the other ten answers of the same call.
        """
        roh_anfrage = json.dumps(
            {"model": self.model, "state": state, "questions": questions},
            ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url(), data=roh_anfrage, method="POST",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self._key}"})
        t0 = time.monotonic()
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                roh = resp.read()
        except urllib.error.HTTPError as e:
            # The body is read for its numeric code ONLY. Its message is
            # never looked at, never kept, never formatted.
            try:
                code = _error_code(e.read())
            except (OSError, ValueError):
                code = None
            zusatz = f", error code {code}" if code is not None else ""
            raise JevError(f"HTTP {e.code} from the decisions endpoint"
                           f"{zusatz}", f"http_{e.code}") from None
        except (urllib.error.URLError, OSError,
                http.client.HTTPException) as e:
            # HTTPException: a truncated body (IncompleteRead) or a broken
            # status line is not an OSError, and must not escape unwrapped.
            token = "timeout" if _is_timeout(e) else type(e).__name__
            raise JevError(f"no answer from the decisions endpoint "
                           f"({token})", token) from None
        ms = (time.monotonic() - t0) * 1000.0
        try:
            obj = json.loads(roh.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise JevError(f"answer is not JSON ({len(roh)} bytes)",
                           "bad_json") from None
        if not isinstance(obj, dict):
            raise JevError(f"answer is not a JSON object "
                           f"({type(obj).__name__})", "bad_reply")
        antworten = obj.get("answers")
        if not isinstance(antworten, dict):
            raise JevError("answer carries no answers object", "no_answers")
        fehlend = [k for k in questions if k not in antworten]
        # JEV-SITE: every question sent must be answered; half an answer
        # is an error, not a partial decision a detector could misread.
        if fehlend and require_all:
            # The COUNT, not the id list: ids are ours, but a count is all
            # a reader needs to act.
            raise JevError(f"answer lacks {len(fehlend)} of {len(questions)} "
                           f"questions", "missing_answer")
        # JEV-SITE: only the ids we asked are kept; an extra answer the
        # provider adds is dropped (and, with require_all=False, a missing
        # one stays absent -- never filled in).
        sauber = {k: clean_answer(antworten[k]) for k in questions
                  if k in antworten}
        # JEV-SITE: usage -- tokens and USD cost are read as numbers only;
        # a missing or non-numeric field counts as 0, never as an error,
        # because the decision itself is still valid.
        nutzung = obj.get("usage") if isinstance(obj.get("usage"), dict) \
            else {}
        ein = _number(nutzung.get("input_tokens"))
        aus = _number(nutzung.get("output_tokens"))
        kosten = _number(nutzung.get("cost"))
        return Decision(
            answers=sauber,
            input_tokens=int(ein) if ein is not None else 0,
            output_tokens=int(aus) if aus is not None else 0,
            cost_usd=kosten if kosten is not None else 0.0,
            ms=round(ms, 3),
            # The model the server ANSWERED with (a dated snapshot), so a
            # silently substituted model is visible in the scores file.
            model=_token(obj.get("model"), "/:") or self.model)
