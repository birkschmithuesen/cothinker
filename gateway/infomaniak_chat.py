#!/usr/bin/env python3
# CoThinker — a minimal Infomaniak chat client
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""One POST to {base}/chat/completions, over urllib. Nothing else.

JSON_SCHEMA ONLY. `response_format: {"type": "json_object"}` answers with
HTTP 400 on this endpoint (infomaniak-modelle.md 6.2), so the strict
`json_schema` form is the only one this client can send.

NO TEMPERATURE. The correction does not need sampling control, and every
field that is not needed is a field that can be rejected.

NEVER reasoning_effort. gemma and Ministral have no reasoning mode;
sending it is a 400 waiting to happen (infomaniak-modelle.md 6.2).

ERROR MESSAGES CARRY NO RESPONSE BODY AND NO KEY. A 4xx body can quote
the request, and the request carries a spoken utterance. The status code,
the exception type and `finish_reason` are enough to act on -- everything
else belongs in a reproduction by hand, not in a log that a meeting
scrolls past.
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# The OpenAI-compatible endpoint of the product this repo is allowed to
# use (CH, no retention, no training -- spec section 2).
DEFAULT_BASE = "https://api.infomaniak.com/2/ai/110416/openai/v1"

# What is worth trying again on the SAME model. Everything else (400,
# 401, 404) is a request or a key problem: a second identical attempt
# would fail identically and cost a meeting another second.
RETRY_STATUS = (429, 500, 502, 503)


class ChatError(Exception):
    """The model was not reachable, or did not answer usably.

    `status` is the HTTP code when there was one, `kind` is one of
    "http", "network", "format", "length". Callers branch on those two
    fields instead of on the message text.
    """

    def __init__(self, message: str, *, status=None, kind: str = "error"):
        super().__init__(message)
        self.status = status
        self.kind = kind


@dataclass
class ChatReply:
    text: str
    usage: dict = field(default_factory=dict)
    # None unless `chat()` set it -- the calling hub times a call once here
    # rather than re-timing it at every call site.
    latency_s: Optional[float] = None
    model: str = ""


def base_url() -> str:
    """The endpoint, overridable for tests and for a local stand-in."""
    return (os.environ.get("COTHINKER_INFOMANIAK_BASE") or DEFAULT_BASE).rstrip("/")


def api_key() -> str:
    """The key, or "" -- the same variable name the stt backend defaults to."""
    return os.environ.get("INFOMANIAK_API_KEY") or ""


def parse_first_json(content) -> dict:
    """The JSON object out of an answer that may carry prose in front.

    The rule is quoted from infomaniak-modelle.md 4.4a: try the whole
    text, then every position from which the REST parses. Measured
    against the alternative of cutting at the first `{` and the last
    `}` -- that one breaks on an answer containing a second object.
    """
    text = (content or "").strip()
    obj = None
    try:
        obj = json.loads(text)
    except ValueError:
        for i, ch in enumerate(text):
            if ch not in "{[":
                continue
            try:
                obj = json.loads(text[i:].strip())
            except ValueError:
                continue
            break
    if not isinstance(obj, dict):
        raise ChatError(
            f"no JSON object in an answer of {len(text)} characters",
            kind="format")
    return obj


class Client:
    """A single-endpoint client. `calls` counts every attempted request."""

    def __init__(self, base=None, key=None, timeout_s: float = 20.0) -> None:
        self.base = (base or base_url()).rstrip("/")
        self.key = api_key() if key is None else key
        self.timeout_s = timeout_s
        self.calls = 0

    def chat(self, model: str, system: str, user: str, schema: dict, *,
             schema_name: str = "korrektur",
             max_tokens: int = 1024,
             request_timeout_s: Optional[float] = None) -> ChatReply:
        timeout = self.timeout_s if request_timeout_s is None \
            else request_timeout_s
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True,
                                "schema": schema}},
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=data, method="POST",
            headers={"content-type": "application/json",
                     "authorization": "Bearer " + self.key})
        t0 = time.monotonic()
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise ChatError(f"HTTP {e.code} from the model endpoint",
                            status=e.code, kind="http") from None
        except (urllib.error.URLError, OSError,
                http.client.HTTPException) as e:
            # HTTPException (IncompleteRead, BadStatusLine, ...) is not
            # an OSError: a body cut short against its
            # Content-Length would escape ChatError and end the run.
            raise ChatError(f"no answer from the model endpoint "
                            f"({type(e).__name__})", kind="network") from None
        latenz = time.monotonic() - t0
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ChatError(f"answer is not JSON ({len(raw)} bytes)",
                            kind="format") from None
        if not isinstance(obj, dict):
            raise ChatError(f"answer is not a JSON object "
                            f"({type(obj).__name__})", kind="format")
        auswahl = obj.get("choices") or []
        if not auswahl or not isinstance(auswahl[0], dict):
            raise ChatError("answer carries no choice", kind="format")
        if auswahl[0].get("finish_reason") == "length":
            # NOT a format error: the answer was cut off, which means the
            # token budget was too small, not that the model cannot keep
            # the schema. The caller retries it like any other failure.
            raise ChatError("answer stopped on finish_reason length",
                            kind="length")
        nachricht = auswahl[0].get("message") or {}
        text = str(nachricht.get("content") or "")
        if not text.strip():
            raise ChatError("answer carries no content", kind="format")
        return ChatReply(text=text, usage=obj.get("usage") or {},
                         latency_s=round(latenz, 3),
                         model=str(obj.get("model") or model))
