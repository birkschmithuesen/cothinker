#!/usr/bin/env python3
# CoThinker - level-1 decider, run beside the consumer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Asks the level-1 question set about every finished block -- in the
background -- and writes the answer vector, as numbers, to
`decisions-<YYYY-MM-DD>.jsonl`.

A MEASUREMENT, NOT A SURFACE. Nothing in this process reads the file
back; it is the basis for level 2 (docs/entwurf-meeting-modi.md) and for
the reference check in the bench. With `--policy` the consumer hangs level
2 (gateway/policy_runner.py) onto `on_result`, which gets the answer in
memory -- the file itself is still never read back. So the rules are
those of the other traces (gateway/events.py, gateway/timing.py):

  * OFF unless `COTHINKER_DECIDER` is set. No surprise network call in a
    running meeting; without it this module creates no thread and no file
    and logs nothing.
  * THE PROTOCOL PATH NEVER WAITS. `submit()` is called from
    `block_done()`, which runs in the SSE reader (or the ticker) under
    BlockBuilder's lock. It does no I/O, takes no stage lock, never blocks
    and never raises. A full queue drops the block and counts it; a
    dropped block still gets its line (`"error": "dropped"`), written by
    the worker thread, so a reader can rely on one line per block.
  * A FAILING DECIDER COSTS NOTHING. Every exception out of `decide()` is
    caught in the worker thread and becomes an error TOKEN in the line.
    The raw transcript is written before the block exists at all.
  * TEXT-FREE. The line holds block index, timestamp, cut reason, numbers,
    question ids, `p` / `choice` (one of the question's own choices),
    tokens and cost. Every value is filtered by shape before it is
    written -- a string that is not a token (1-32 chars `[A-Za-z0-9_-]`)
    is dropped, the same promise gateway/events.py makes -- so neither the
    block, nor the heading, nor an exception message can reach the file.
    Log lines carry type names, tokens and numbers only.

ONE WRITER. Only the worker thread appends to the file.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import decide as d
from events import is_number, is_token
from questions import QUESTIONS, QUESTIONS_HASH, QUESTIONS_VERSION

# Version of the LINE layout below, not of the question set. Task 4 (the
# bench) reads it; bump it when a key changes meaning.
LINE_VERSION = 1
# Blocks waiting for the backend. A block arrives every few seconds to a
# minute; a jev call takes about one to a few seconds. Eight waiting means
# the backend is far behind, and waiting longer would only make the
# answers belong to a conversation that has moved on.
QUEUE_MAX = 8
# Dropped-block markers kept for the worker to write. Only index, stamp,
# reason and size -- a few bytes each; past this the drop is only counted.
DROPPED_MAX = 10_000
# How long shutdown waits for the backlog. A jev call times out after
# 30 s; the meeting's closing note and final commit must not wait that
# long for a measurement.
FINISH_TIMEOUT_S = 10.0

# `at` is the only string in a line that is not a token: an ISO 8601
# timestamp with offset, produced here from a datetime -- never a
# caller's string.
_AT_RE = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?\Z")


def _num(value, default=0):
    """A number, or `default`. A bool is NOT a number here: `True` in a
    token or cost field would read as 1 token / 1 USD."""
    if isinstance(value, bool):
        return default
    return value if is_number(value) else default


_CHOICES = {q.id: tuple(q.choices) for q in QUESTIONS if q.kind == "choice"}


def _token(value) -> Optional[str]:
    return value if is_token(value) else None


def _error_token(exc: BaseException) -> str:
    """Token for an exception -- its own token or its TYPE name, never
    its message (which can carry the block)."""
    tok = getattr(exc, "token", None)
    if is_token(tok):
        return tok
    name = type(exc).__name__
    return name if is_token(name) else "exception"


def _stamp(at) -> Optional[str]:
    if not isinstance(at, datetime):
        return None
    try:
        s = at.isoformat(timespec="seconds")
    except Exception:                     # noqa: BLE001
        return None
    return s if _AT_RE.match(s) else None


def _answer(entry) -> Optional[dict]:
    """One answer entry, shape-filtered. Unknown keys are dropped.

    JEV-SITE: this is where a jev answer (via decide.decide()) becomes a
    line entry -- `p` as the model gave it, `choice` only if it is one of
    the question's own choices (gateway/questions.py). decide() already
    checks that; this is the second safeguard, so a token-shaped word the
    model invented can never reach the file."""
    if not isinstance(entry, dict):
        return None
    qid = _token(entry.get("question_id"))
    if qid is None:
        return None
    out: dict = {"question_id": qid}
    p = entry.get("p")
    if "p" in entry and is_number(p) and not isinstance(p, bool):
        out["p"] = p
    ch = entry.get("choice")
    if ("choice" in entry and _token(ch) is not None
            and ch in _CHOICES.get(qid, ())):
        out["choice"] = ch
    if "confidence" in entry:
        c = entry.get("confidence")
        out["confidence"] = (c if is_number(c) and not isinstance(c, bool)
                             else None)
    err = _token(entry.get("error"))
    if err is not None:
        out["error"] = err
    out["ms"] = _num(entry.get("ms"), 0)
    return out


def decision_line(*, block: int, at, reason, chars, result=None,
                  error: Optional[str] = None, wait_ms: float = 0.0,
                  drops: int = 0, backend: str = "") -> dict:
    """The one line per block. See the module docstring for the rules and
    docs/bench.md ("The `decisions-<datum>.jsonl` line") for the keys."""
    zeile: dict = {"v": LINE_VERSION, "block": int(block)}
    stempel = _stamp(at)
    if stempel is not None:
        zeile["at"] = stempel
    grund = _token(reason)
    if grund is not None:
        zeile["reason"] = grund
    zeile["chars"] = _num(chars, 0) if not isinstance(chars, bool) else 0
    zeile["backend"] = _token(getattr(result, "backend", None)) or \
        _token(backend) or "unknown"
    zeile["questions_version"] = _num(
        getattr(result, "questions_version", QUESTIONS_VERSION),
        QUESTIONS_VERSION)
    zeile["questions_hash"] = _token(
        getattr(result, "questions_hash", None)) or QUESTIONS_HASH
    antworten = []
    if result is not None:
        for e in getattr(result, "answers", None) or ():
            a = _answer(e)
            if a is not None:
                antworten.append(a)
    zeile["answers"] = antworten
    zeile["errors"] = int(_num(getattr(result, "errors", 0), 0))
    zeile["tokens_in"] = _num(getattr(result, "tokens_in", 0), 0)
    zeile["tokens_out"] = _num(getattr(result, "tokens_out", 0), 0)
    zeile["tokens_est"] = _num(getattr(result, "tokens_est", 0), 0)
    zeile["truncated"] = bool(getattr(result, "truncated", False))
    zeile["dropped_blocks"] = _num(getattr(result, "dropped_blocks", 0), 0)
    zeile["trimmed_chars"] = _num(getattr(result, "trimmed_chars", 0), 0)
    zeile["cost_usd"] = _num(getattr(result, "cost_usd", 0.0), 0.0)
    zeile["ms"] = _num(getattr(result, "ms", 0.0), 0.0)
    zeile["wait_ms"] = _num(wait_ms, 0.0)
    zeile["drops"] = int(_num(drops, 0))
    if error is not None:
        zeile["error"] = _token(error) or "exception"
    return zeile


def _texts(value) -> list:
    """A list of strings out of whatever the caller handed in."""
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple)):
        return []
    return [v for v in value if isinstance(v, str) and v]


def stage_context(state) -> dict:
    """Heading, last topic titles and the open question, as the stage
    holds them right now. Cheap attribute reads, no lock taken (this runs
    under BlockBuilder's lock, and the consumer's lock order must stay as
    documented); a torn read costs one block a slightly stale context.
    Never raises -- anything unexpected is simply left out."""
    out: dict = {}
    try:
        h = getattr(state, "headline", "")
        if isinstance(h, str) and h:
            out["heading"] = h
    except Exception:                     # noqa: BLE001
        pass
    try:
        verlauf = list(getattr(state, "history", None) or ())
        titel = [getattr(m, "title", "") for m in verlauf]
        titel = [t for t in titel if isinstance(t, str) and t]
        if titel:
            out["topics"] = titel[-d.TOPIC_TITLES:]
    except Exception:                     # noqa: BLE001
        pass
    try:
        frage = getattr(state, "question", None)
        if frage is not None and getattr(frage, "state", "") == "open":
            t = getattr(frage, "text", "")
            if isinstance(t, str) and t:
                out["hints"] = [t]
    except Exception:                     # noqa: BLE001
        pass
    return out


class DeciderWorker:
    """One daemon thread, one bounded queue, one output file."""

    def __init__(self, backend, path, *, events=None,
                 log: Callable = print,
                 context_blocks: int = d.DEFAULT_CONTEXT_BLOCKS,
                 queue_max: int = QUEUE_MAX,
                 decide_fn: Optional[Callable] = None,
                 on_result: Optional[Callable] = None) -> None:
        self.backend = backend
        self.path = Path(path)
        self.events = events
        self.log = log
        self.decide_fn = decide_fn or d.decide
        # callable(result, snap, reason), run in this worker thread after
        # a block's line is written. None: the measurement alone.
        self.on_result = on_result
        self.written = 0
        self.failed = 0
        self.drops = 0
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, queue_max))
        self._dropped: list = []
        self._lock = threading.Lock()
        self._history = d.State(context_blocks=context_blocks)
        self._counter = 0
        self._closed = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.backend_name = _token(getattr(backend, "name", None)) or "unknown"

    def __repr__(self) -> str:
        return f"DeciderWorker(backend={self.backend_name}, path={self.path.name})"

    # ------------------------------------------------------------ in
    def start(self) -> None:
        if self._thread is not None:
            return
        self._trace("decider_started", backend=self.backend_name,
                    questions_hash=QUESTIONS_HASH,
                    questions_version=QUESTIONS_VERSION,
                    count=self._history.context_blocks)
        self._thread = threading.Thread(target=self._work, daemon=True,
                                        name="decider")
        self._thread.start()

    def submit(self, text, *, reason="", chars=None, heading="", topics=(),
               hints=(), mode="", at=None) -> bool:
        """Hand one finished block over. Never blocks, never raises.

        True when the block is queued; False when it was dropped (queue
        full, or the worker already finished)."""
        try:
            with self._lock:
                if self._closed:
                    self.drops += 1
                    return False
                text = text if isinstance(text, str) else ""
                self._counter += 1
                # The window moves on even for a block that is dropped
                # below: the next block's "vorher" is what was really said.
                self._history.push(text)
                snap = d.State(
                    block=self._history.block,
                    previous=list(self._history.window()),
                    heading=heading if isinstance(heading, str) else "",
                    topics=_texts(topics),
                    hints=_texts(hints),
                    mode=mode if isinstance(mode, str) else "",
                    context_blocks=self._history.context_blocks)
                if not isinstance(at, datetime):
                    at = datetime.now().astimezone()
                kopf = {"block": self._counter, "at": at, "reason": reason,
                        "chars": (chars if is_number(chars)
                                  and not isinstance(chars, bool)
                                  else len(text))}
                try:
                    self._queue.put_nowait((kopf, snap, time.monotonic()))
                except queue.Full:
                    self.drops += 1
                    if len(self._dropped) < DROPPED_MAX:
                        self._dropped.append(kopf)
                    return False
                return True
        except Exception as e:                # noqa: BLE001 -- see docstring
            self._say("decider submit skipped:", _error_token(e))
            return False

    # -------------------------------------------------------- the thread
    def _work(self) -> None:
        while True:
            try:
                self._flush_dropped()
                try:
                    kopf, snap, t_in = self._queue.get(timeout=0.2)
                except queue.Empty:
                    if self._stop.is_set():
                        self._flush_dropped()
                        return
                    continue
                self._one(kopf, snap, t_in)
            except Exception as e:            # noqa: BLE001 -- never die
                self._say("decider error (continuing):", _error_token(e))

    def _one(self, kopf: dict, snap, t_in: float) -> None:
        wait_ms = round((time.monotonic() - t_in) * 1000.0, 3)
        result = None
        fehler = None
        try:
            # JEV-SITE: the live backend call. With COTHINKER_DECIDER=jev
            # this sends the block, `vorher`, heading, topic titles and the
            # open question to OpenRouter (USA) -- only by deliberate choice.
            result = self.decide_fn(snap, backend=self.backend)
        except Exception as e:                # noqa: BLE001 -- never propagate
            fehler = _error_token(e)
            self.failed += 1
            self._say("decider call failed:", fehler, f"block={kopf['block']}")
        self._write(decision_line(result=result, error=fehler, wait_ms=wait_ms,
                                  drops=self.drops, backend=self.backend_name,
                                  **kopf))
        if self.on_result is not None and result is not None:
            try:
                # Level 2 (gateway/policy_runner.py) runs here, in this
                # thread, AFTER the measurement line is safely written.
                self.on_result(result, snap, kopf.get("reason", ""))
            except Exception as e:            # noqa: BLE001 -- never propagate
                self._say("policy hook failed:", _error_token(e))

    def _flush_dropped(self) -> None:
        with self._lock:
            weg, self._dropped = self._dropped, []
        for kopf in weg:
            self._write(decision_line(error="dropped", drops=self.drops,
                                      backend=self.backend_name, **kopf))

    def _write(self, zeile: dict) -> None:
        try:
            line = json.dumps(zeile, ensure_ascii=False, allow_nan=False) + "\n"
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            self.written += 1
        except Exception as e:                # noqa: BLE001
            self._say("decisions not written:", _error_token(e))

    # ------------------------------------------------------------ out
    def finish(self, timeout: float = FINISH_TIMEOUT_S) -> None:
        """Stop taking blocks, give the backlog `timeout` seconds, record
        the totals. Never raises (a second Ctrl-C is the caller's)."""
        try:
            with self._lock:
                self._closed = True
            self._stop.set()
            if self._thread is not None:
                self._thread.join(max(0.0, float(timeout)))
                if self._thread.is_alive():
                    self._say("decider: backlog not finished in time,",
                              f"{self._queue.qsize()} blocks unwritten")
            self._trace("decider_finished", count=self.written,
                        discarded=self.drops, backend=self.backend_name)
        except Exception as e:                # noqa: BLE001
            self._say("decider finish:", _error_token(e))

    # ---------------------------------------------------------- helpers
    def _trace(self, event: str, **fields) -> None:
        if self.events is None:
            return
        try:
            self.events.record(event, **fields)
        except Exception:                     # noqa: BLE001
            pass

    def _say(self, *parts) -> None:
        try:
            self.log(*parts)
        except Exception:                     # noqa: BLE001
            pass


def from_env(path, *, events=None, log: Callable = print, env=None,
             **kw) -> Optional[DeciderWorker]:
    """The worker `COTHINKER_DECIDER` asks for, or None.

    Unset -> None, silently: zero behaviour change. A backend that cannot
    be built (jev without key, laya not built yet, an unknown name) ->
    None and ONE log line with the type name / token. Never raises: a
    measurement must not keep a meeting from starting."""
    try:
        backend = d.backend_from_env(env)
    except Exception as e:                    # noqa: BLE001
        try:
            log(f"decider off: {d.ENV_VAR} set but unusable ({_error_token(e)})")
        except Exception:                     # noqa: BLE001
            pass
        return None
    if backend is None:
        return None
    return DeciderWorker(backend, path, events=events, log=log, **kw)
