#!/usr/bin/env python3
# CoThinker — thought end, live: the combo detector as a parallel lane
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The bench's `combo` in the live path -- written down, never acted on.

One row per utterance in `thought-end.jsonl`: smart-turn's p (computed on
the recording machine and delivered as a `thought_end` SSE event), Laya's
p (computed here, on text), their mean, and whether that mean would have
cut at the pinned threshold. Nothing from here reaches the block
builder, the agent, the protocol or the stage: this is a measurement
running next to the 2.5 s pause cut, not a replacement for it (plan
docs/superpowers/plans/2026-09-26-thought-end-parallelspur.md).

NO TEXT IN THE TRACE. Laya needs the utterance and the text before it;
both stay in memory and go to the worker's stdin only. What is written is
an allow-list of numbers and tokens (`ROW_KEYS`), and log lines carry
numbers and exception type names only -- same rule and same reason as
shared/events.py and gateway/correction.py.

THE SSE READER IS NEVER BLOCKED. `enqueue()` and `smart_turn()` take a
lock for a dict or queue operation and return; the Laya call, the wait
for smart-turn and the file write all happen on this lane's own thread,
which is also the only writer of the file.

HOW THE TWO HALVES MEET. The STT server publishes `thought_end` after the
final it belongs to, with the final's `recognizer_id`, `turn_id` and
`t_end`. A result is filed under (recognizer, turn_id) and under
(recognizer, t_end); the utterance looks for either. Laya is slower than
smart-turn (150-1600 ms against ~50 ms, docs/bench-thought-end.md), so
the answer is usually there already; otherwise the lane waits up to
`smart_turn_wait_s` -- but only for an utterance with a span, and only
once this run has seen at least one `thought_end` at all, so an older
STT server costs no waiting.

HOW THE ROW MEETS timing.jsonl. `at` is the SAME datetime the consumer
hands to `TimingLog.record_utterance()`, formatted the same way, so
bench/thought_end_live.py joins the two files on it.
"""
from __future__ import annotations

import math
import os
import queue
import sys
import threading
import time
import json
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from thought_end import TOKEN_RE, WorkerClient, WorkerError  # noqa: E402
from timing import valid_span  # noqa: E402

CANDIDATE = "combo"
# The leave-one-session-out threshold the bench chose for combo in two of
# its three folds (docs/bench-thought-end.md, held-out table). Pinned, not
# tuned: every p is written, so bench/thought_end_live.py can re-threshold.
DEFAULT_THRESHOLD = 0.44
# Same context length as bench/thought_end.KONTEXT_CHARS -- the question
# is asked the way it was measured.
KONTEXT_CHARS = 600
SMART_TURN_WAIT_S = 2.0
QUEUE_MAX = 64
# More utterances waiting than this and Laya is skipped for the next one:
# the lane must never fall minutes behind the room (Laya measured up to
# 1.6 s per decision on a shared vServer).
BACKLOG_MAX = 4
PENDING_MAX = 256
LAYA_THREADS = 4
MAX_LAYA_FAILURES = 3
FINISH_TIMEOUT_S = 10.0

ROW_KEYS = frozenset({
    "at", "recognizer_id", "candidate", "threshold", "t_start", "t_end",
    "p_smart_turn", "smart_turn_ms", "smart_turn_error",
    "p_laya", "laya_ms", "laya_error", "p_combo", "cut", "decided_ms",
    "status"})


def default_laya_argv(environ=os.environ):
    """The repo's Laya worker under the bench's environment names, or None."""
    python = environ.get("COTHINKER_LAYA_PYTHON", "")
    model = environ.get("COTHINKER_LAYA_MODEL", "")
    if not python or not model:
        return None
    return [python,
            str(Path(__file__).resolve().parent / "thought_end_laya_worker.py"),
            "--model", model, "--threads", str(LAYA_THREADS)]


def combine(p_smart_turn, p_laya):
    """The mean where both answered -- bench/thought_end.combo_decisions."""
    if p_smart_turn is None or p_laya is None:
        return None
    return (float(p_smart_turn) + float(p_laya)) / 2.0


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except (OverflowError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _token(value):
    return value if isinstance(value, str) and TOKEN_RE.match(value) else None


def _keys(recognizer_id, turn_id, t_end) -> list:
    rid = recognizer_id if isinstance(recognizer_id, str) else None
    out = []
    if isinstance(turn_id, str) and turn_id:
        out.append(("turn", rid, turn_id))
    t = _number(t_end)
    if t is not None:
        out.append(("t_end", rid, round(t, 3)))
    return out


class ThoughtEndLane:
    """Combo decisions per utterance, in the background, into one file."""

    def __init__(self, path, laya_argv, *, threshold=DEFAULT_THRESHOLD,
                 log=print, client_factory=WorkerClient,
                 smart_turn_wait_s=SMART_TURN_WAIT_S,
                 backlog_max=BACKLOG_MAX) -> None:
        self.path = Path(path)
        self.laya_argv = list(laya_argv) if laya_argv else None
        self.threshold = float(threshold)
        self.log = log
        self._factory = client_factory
        self._wait_s = float(smart_turn_wait_s)
        self._backlog_max = int(backlog_max)
        self._queue: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        self._cond = threading.Condition()
        self._pending: "OrderedDict" = OrderedDict()
        self._seen_smart_turn = False
        self._stop = threading.Event()
        self._deadline = None
        self._thread = None
        self._client = None
        self._laya_failures = 0
        self._context = ""
        self._counter = 0
        self.drops = 0

    # ------------------------------------------------------------ in
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True,
                                            name="thought-end")
            self._thread.start()

    def enqueue(self, text, *, at, recognizer_id=None, turn_id=None,
                t_start=None, t_end=None) -> None:
        """One utterance. Never blocks, never raises."""
        try:
            if self._stop.is_set():
                self.drops += 1
                return
            entry = {"text": "" if text is None else str(text), "at": at,
                     "recognizer_id": recognizer_id, "turn_id": turn_id,
                     "t_start": t_start, "t_end": t_end}
            try:
                self._queue.put_nowait(entry)
            except queue.Full:
                self.drops += 1
        except Exception as e:            # noqa: BLE001
            self._say("thought end enqueue skipped:", type(e).__name__)

    def smart_turn(self, ev) -> None:
        """One `thought_end` event off the SSE stream. Never raises."""
        try:
            if not isinstance(ev, dict):
                return
            rid = ev.get("recognizer_id")
            rid = str(rid) if rid not in (None, "") and isinstance(
                rid, (str, int)) else None
            keys = _keys(rid, ev.get("turn_id"), ev.get("t_end"))
            if not keys:
                return
            result = {"p": _number(ev.get("p")), "ms": _number(ev.get("ms")),
                      "error": _token(ev.get("error")), "keys": keys}
            with self._cond:
                self._seen_smart_turn = True
                for k in keys:
                    self._pending[k] = result
                    self._pending.move_to_end(k)
                while len(self._pending) > PENDING_MAX:
                    self._pending.popitem(last=False)
                self._cond.notify_all()
        except Exception as e:            # noqa: BLE001
            self._say("thought end event skipped:", type(e).__name__)

    # -------------------------------------------------------- the thread
    def _say(self, *parts) -> None:
        try:
            self.log(*parts)
        except Exception:                 # noqa: BLE001
            pass

    def _late(self) -> bool:
        return self._deadline is not None and time.monotonic() > self._deadline

    def _work(self) -> None:
        if self.laya_argv is not None:
            # Warm up before the first utterance arrives: Laya takes up
            # to ~16 s to load, and that cost must not land on whoever
            # spoke first. A failed warm-up is just a failed start --
            # _ensure_client() already counts it and the next utterance
            # tries again the same way it always would.
            self._ensure_client()
        while True:
            try:
                entry = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            try:
                self._handle(entry)
            except Exception as e:        # noqa: BLE001
                self._say("thought end row skipped:", type(e).__name__)

    def _take_smart_turn(self, keys, wait: bool):
        deadline = time.monotonic() + (self._wait_s if wait else 0.0)
        with self._cond:
            while True:
                for k in keys:
                    result = self._pending.get(k)
                    if result is not None:
                        for other in result["keys"]:
                            self._pending.pop(other, None)
                        return result
                remaining = deadline - time.monotonic()
                if (remaining <= 0 or not self._seen_smart_turn
                        or self._late()):
                    return None
                self._cond.wait(remaining)

    def _laya(self, text, skip: bool):
        """(p, ms, error token) for one utterance."""
        if self.laya_argv is None:
            return None, None, "not_configured"
        if skip:
            return None, None, "skipped"
        client = self._ensure_client()
        if client is None:
            return None, None, "worker_down"
        self._counter += 1
        try:
            reply = client.ask({"id": self._counter,
                                "kontext": self._context,
                                "aeusserung": text})
        except WorkerError:
            self._drop_client()
            return None, None, "worker"
        if "p" not in reply:
            return None, None, _token(reply.get("error")) or "no_p"
        p = _number(reply.get("p"))
        if p is None:
            return None, None, "bad_p"
        return p, _number(reply.get("ms")), None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if self._late():
            return None
        if self._laya_failures >= MAX_LAYA_FAILURES:
            return None
        client = self._factory(self.laya_argv)
        try:
            client.start()
        except (WorkerError, OSError) as e:
            self._laya_failures += 1
            self._say(f"thought end: laya worker did not start "
                      f"({type(e).__name__}, failure {self._laya_failures} "
                      f"of {MAX_LAYA_FAILURES})")
            try:
                client.close()
            except Exception:             # noqa: BLE001
                pass
            return None
        self._client = client
        return client

    def _drop_client(self) -> None:
        self._laya_failures += 1
        client, self._client = self._client, None
        try:
            if client is not None:
                client.close()
        except Exception:                 # noqa: BLE001
            pass

    def _handle(self, entry) -> None:
        skip = self._queue.qsize() >= self._backlog_max or self._late()
        text = entry["text"]
        p_laya, laya_ms, laya_error = self._laya(text, skip)
        self._context = ((self._context + " " + text) if self._context
                         else text)[-KONTEXT_CHARS:]
        span = valid_span(entry["t_start"], entry["t_end"])
        rid = entry["recognizer_id"]
        rid = str(rid) if rid not in (None, "") else None
        found = self._take_smart_turn(
            _keys(rid, entry["turn_id"], entry["t_end"] if span else None),
            wait=span and not skip)
        p_st = found["p"] if found else None
        p_combo = combine(p_st, p_laya)
        if skip and p_laya is None and p_st is None:
            status = "skipped"
        elif p_st is not None and p_laya is not None:
            status = "ok"
        elif p_st is not None or p_laya is not None:
            status = "partial"
        else:
            status = "none"
        self._write(entry, span, rid, found, p_laya, laya_ms, laya_error,
                    p_combo, status)

    def _write(self, entry, span, rid, found, p_laya, laya_ms, laya_error,
               p_combo, status) -> None:
        at = entry["at"]
        decided_ms = None
        if isinstance(at, datetime):
            try:
                decided_ms = int(round(
                    (datetime.now().astimezone() - at).total_seconds() * 1000))
            except TypeError:
                # A naive `at` (no tzinfo) cannot be subtracted from an
                # aware "now" -- the row is still worth writing, just
                # without the latency number.
                decided_ms = None
            at = at.isoformat(timespec="milliseconds")
        else:
            at = None
        row = {"at": at, "recognizer_id": _token(rid),
               "candidate": CANDIDATE, "threshold": self.threshold,
               "p_smart_turn": found["p"] if found else None,
               "smart_turn_ms": found["ms"] if found else None,
               "p_laya": p_laya, "laya_ms": laya_ms,
               "p_combo": p_combo,
               "cut": None if p_combo is None else p_combo >= self.threshold,
               "decided_ms": decided_ms, "status": status}
        if span:
            row["t_start"] = round(float(entry["t_start"]), 3)
            row["t_end"] = round(float(entry["t_end"]), 3)
        if found and found.get("error"):
            row["smart_turn_error"] = found["error"]
        if laya_error:
            row["laya_error"] = laya_error
        row = {k: v for k, v in row.items() if k in ROW_KEYS}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except (OSError, UnicodeError) as e:
            number = getattr(e, "errno", None)
            self._say(f"thought end not writable ({self.path.name}):",
                      type(e).__name__
                      + (f" errno {number}" if isinstance(number, int) else ""))

    # ----------------------------------------------------------- out
    def finish(self, timeout: float = FINISH_TIMEOUT_S) -> None:
        """Drain for at most `timeout` s (Laya skipped once late). Never raises.

        The client is only closed once the lane thread has actually
        exited: `WorkerClient`'s own budgets (30 s request, 120 s start)
        outlast this join, so a still-busy thread may still be inside
        `client.ask()` when the join times out. Closing under it there
        would hand the caller's thread a closed pipe out from under a
        read in progress. Leaving it open costs one lingering subprocess,
        never a crash.
        """
        try:
            self._deadline = time.monotonic() + max(0.0, timeout)
            self._stop.set()
            with self._cond:
                self._cond.notify_all()
            exited = True
            if self._thread is not None:
                self._thread.join(max(0.0, timeout) + 5.0)
                exited = not self._thread.is_alive()
                if not exited:
                    self._say(f"thought end: still busy after {timeout:.0f} s, "
                              f"{self._queue.qsize()} rows not written")
            if exited and self._client is not None:
                self._client.close()
                self._client = None
        except Exception as e:            # noqa: BLE001
            self._say("thought end finish incomplete:", type(e).__name__)
