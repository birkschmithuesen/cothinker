#!/usr/bin/env python3
# CoThinker — short model-made titles for confirmed topic changes
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Real topic titles, behind `titles = true` of the `topics` block
in templates/reflect/template.toml (needs `--lanes`), default off.

Until now a confirmed topic change (gateway/call_lane.py) took
its history title from the first line of the triggering block -- live
2026-10-20 that was half a spoken sentence. With this switch the
provisional title still goes up at once (nothing waits), and ONE short
model call asks for a 2-5 word German noun phrase; when it answers
within TIMEOUT_S the history entry's title and the headline are
replaced in place. On timeout, error or an unusable answer the
provisional title simply stays.

Model route: the same local anthropic_plan proxy the worker and the
bench use (bench/llm.py, `/v1/messages`) -- no new provider. Off the hot
path: one daemon worker thread, newest request wins if they pile up.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

DEFAULT_BASE = "http://127.0.0.1:28764"
DEFAULT_MODEL = "claude-haiku-4-5"
TIMEOUT_S = 4.0
MAX_INPUT_CHARS = 2400
MAX_TITLE_CHARS = 60

SYSTEM = (
    "Du benennst Themen in einem laufenden Gespraech. Antworte mit GENAU "
    "einem kurzen deutschen Titel: 2 bis 5 Woerter, eine Nominalphrase, "
    "keine Anfuehrungszeichen, kein Satzzeichen am Ende, keine Erklaerung.")


class TitleError(Exception):
    """The model gave no usable title (token-style message, no text)."""


def clean_title(raw: str) -> str:
    """The first line, quotes and trailing punctuation stripped, 1-6 words.

    Raises TitleError for anything that is not a short title.
    """
    if not isinstance(raw, str):
        raise TitleError("no_text")
    line = next((l for l in raw.strip().splitlines() if l.strip()), "")
    line = re.sub(r"^(?:titel|thema)\s*:\s*", "", line.strip(), flags=re.I)
    quotes = "\"'„“”‚‘’«»*_`#"
    line = line.strip().strip(quotes).strip()
    line = re.sub(r"[.!?:;,]+$", "", line).strip().strip(quotes).strip()
    line = re.sub(r"\s+", " ", line)
    words = line.split(" ")
    if not line or len(words) > 6 or len(line) > MAX_TITLE_CHARS:
        raise TitleError("bad_shape")
    return line


def proxy_model_call(system: str, user: str, *, model: str = DEFAULT_MODEL,
                     base_url: str = DEFAULT_BASE,
                     timeout_s: float = TIMEOUT_S) -> str:
    """One POST /v1/messages to the plan proxy; the reply text.

    Errors carry no response body (it can quote the request, and the
    request carries the transcript) -- same rule as bench/llm.py.
    """
    body = {"model": model, "max_tokens": 30, "system": system,
            "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/messages",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json", "x-api-key": "dummy",
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise TitleError(f"http_{e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        name = type(e).__name__
        if "timeout" in name.lower() or "timed out" in str(e).lower():
            raise TitleError("timeout") from None
        raise TitleError(f"no_answer_{name}") from None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise TitleError("not_json") from None
    return "".join(b.get("text", "") for b in (obj.get("content") or [])
                   if isinstance(b, dict) and b.get("type") == "text")


def build_user_prompt(block: str, previous: str = "") -> str:
    """The triggering block (head first), plus the block before it."""
    neu = (block or "").strip()[:MAX_INPUT_CHARS]
    parts = []
    if previous:
        parts.append("Davor:\n" + previous.strip()[-600:])
    parts.append("Neues Thema beginnt hier:\n" + neu)
    parts.append("Titel des neuen Themas:")
    return "\n\n".join(parts)


class TopicTitler:
    """One worker thread; `request()` never blocks and never raises.

    `on_title(entry_id, old, new, ms)` runs on the worker thread when a
    usable title arrived in time; `on_fail(entry_id, token, ms)` otherwise.
    `call(system, user, timeout_s) -> str` is injectable (tests: a fake).
    """

    def __init__(self, on_title: Callable, *, on_fail: Optional[Callable] = None,
                 call: Optional[Callable] = None, timeout_s: float = TIMEOUT_S,
                 model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE,
                 log: Callable[..., None] = print) -> None:
        self.on_title = on_title
        self.on_fail = on_fail
        self.timeout_s = timeout_s
        self.log = log
        self._call = call or (lambda system, user, timeout_s: proxy_model_call(
            system, user, model=model, base_url=base_url, timeout_s=timeout_s))
        self._q: "queue.Queue" = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="topic-titles")
        self._thread.start()
        self.calls = 0
        self.ok = 0
        self.failed = 0

    def request(self, entry_id: str, provisional: str, block: str,
                previous: str = "") -> bool:
        try:
            self._q.put_nowait((entry_id, provisional, block, previous))
            return True
        except queue.Full:
            return False

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._one(*item)
            except Exception as e:            # noqa: BLE001 — never die
                self.log("topic: title worker error:", type(e).__name__)

    def _one(self, entry_id, provisional, block, previous) -> None:
        t0 = time.monotonic()
        self.calls += 1
        result: dict = {}

        def _target():
            try:
                result["text"] = self._call(SYSTEM,
                                            build_user_prompt(block, previous),
                                            self.timeout_s)
            except Exception as e:            # noqa: BLE001
                result["err"] = (str(e) if isinstance(e, TitleError)
                                 else type(e).__name__)

        # The hard budget is enforced here, not only by the socket timeout:
        # a proxy that trickles bytes would otherwise outlive it.
        th = threading.Thread(target=_target, daemon=True)
        th.start()
        th.join(self.timeout_s)
        ms = int((time.monotonic() - t0) * 1000)
        token = None
        title = ""
        if th.is_alive():
            token = "timeout"
        elif "err" in result:
            token = result["err"]
        else:
            try:
                title = clean_title(result.get("text", ""))
            except TitleError as e:
                token = str(e)
        if token is not None:
            self.failed += 1
            self.log(f"topic: title {provisional!r} kept ({token}) {ms}ms")
            if self.on_fail:
                self.on_fail(entry_id, token, ms)
            return
        self.ok += 1
        self.on_title(entry_id, provisional, title, ms)
