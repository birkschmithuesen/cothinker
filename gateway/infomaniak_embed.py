#!/usr/bin/env python3
# CoThinker — the Infomaniak embeddings client
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""One POST to the OpenAI-shaped embeddings endpoint, over urllib.

ERROR MESSAGES CARRY NO RESPONSE BODY. The same rule bench/llm.py
follows: a 4xx or 5xx body can quote the request, and the request
carries speech of third parties. A status code and an exception type are
enough to act on.

THE KEY NEVER LEAVES THIS OBJECT. It comes from the environment (the
caller reads `INFOMANIAK_API_KEY`), it is not a constructor default, it
is not a CLI argument -- an argument stands in every process list on
this machine, and this very repo reads process lists
(bench/session.read_processes()) -- and `__repr__` hides it, because a
repr ends up in tracebacks and log lines.

Switzerland, no retention, no training (spec section 2). One text per
call: the per-decision latency and the per-decision token count are what
the bench reports, and a batched call would make both of them a guess.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = "https://api.infomaniak.com"
# The AI-tools product number. Already a committed default in
# stt/fundusapps/stt_server/backends/infomaniak_whisper_backend.py
# (`product_id: str = "110416"`), so it is not new information here.
DEFAULT_PRODUCT_ID = "110416"
DEFAULT_MODEL = "bge_multilingual_gemma2"


class EmbedError(Exception):
    """The endpoint was not reachable, or did not answer usably."""


@dataclass
class Embedding:
    vector: list = field(default_factory=list)
    tokens: int = 0
    ms: float = 0.0
    model: str = ""


class Client:
    """A single-endpoint client. `calls` counts every attempted request."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *,
                 base_url: str = DEFAULT_BASE,
                 product_id: str = DEFAULT_PRODUCT_ID,
                 timeout_s: float = 60.0) -> None:
        if not api_key:
            raise EmbedError("no key -- set INFOMANIAK_API_KEY in the "
                             "environment")
        self._key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.product_id = product_id
        self.timeout_s = timeout_s
        self.calls = 0

    def __repr__(self) -> str:
        return (f"Client(model={self.model!r}, url={self.url()!r}, "
                f"calls={self.calls})")

    def url(self) -> str:
        return f"{self.base_url}/2/ai/{self.product_id}/openai/v1/embeddings"

    def embed(self, text: str) -> Embedding:
        roh_anfrage = json.dumps({"model": self.model, "input": [text]},
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
            raise EmbedError(
                f"HTTP {e.code} from the embeddings endpoint") from None
        except (urllib.error.URLError, OSError) as e:
            raise EmbedError(f"no answer from the embeddings endpoint "
                             f"({type(e).__name__})") from None
        ms = (time.monotonic() - t0) * 1000.0
        try:
            obj = json.loads(roh.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise EmbedError(f"answer is not JSON ({len(roh)} bytes)") from None
        if not isinstance(obj, dict):
            raise EmbedError(
                f"answer is not a JSON object ({type(obj).__name__})")
        daten = obj.get("data")
        if not isinstance(daten, list) or not daten \
                or not isinstance(daten[0], dict):
            raise EmbedError("answer carries no usable data array")
        vektor = daten[0].get("embedding")
        if not isinstance(vektor, list) or not vektor:
            raise EmbedError("answer carries no embedding vector")
        try:
            zahlen = [float(x) for x in vektor]
        except (TypeError, ValueError):
            # The ENTRY COUNT, not an entry: a foreign answer's contents
            # are not ours to print.
            raise EmbedError(f"embedding vector is not numeric "
                             f"({len(vektor)} entries)") from None
        nutzung = obj.get("usage") or {}
        tokens = nutzung.get("prompt_tokens")
        return Embedding(
            vector=zahlen,
            tokens=int(tokens) if isinstance(tokens, (int, float)) else 0,
            ms=round(ms, 3),
            model=str(obj.get("model") or self.model))
