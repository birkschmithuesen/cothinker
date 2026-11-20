#!/usr/bin/env python3
# CoThinker — thought end: shared types and the worker client
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Types and the subprocess client both thought-end workers speak to.

WHY A SUBPROCESS AT ALL: the two candidates need interpreters this repo
does not have. smart-turn needs numpy and onnxruntime, Laya needs Python
3.11 and torch; the repo's own interpreter is 3.9 with the standard
library, and `tests/all.py` must never import either of those stacks. So
each model lives behind a line-based JSON protocol, and everything in
this file runs under 3.9 with nothing installed.

THE PROTOCOL, one JSON object per line, in this order:

  out  {"ready": true, "load_s": 1.4}        once, before anything else
  in   {"id": 7, ...}                        one request line
  out  {"id": 7, "p": 0.83, "ms": 47.2, "cpu_ms": 46.9}
  out  {"id": 7, "error": "<a token>"}       a TOKEN, never a message
  out  {"id": -1, "error": "bad_request"}    an unusable line; loop goes on

Line-based and synchronous on purpose: one decision at a time is what
live operation would do, so the latency measured here is the latency that
would matter. stderr belongs to the library (both stacks warn a lot) and
goes to DEVNULL.

NO TEXT EVER COMES BACK. `clean_reply()` enforces that on the PARENT's
side rather than trusting the child: numeric fields have to be numbers,
the error field has to be 1-32 characters of ASCII letters, digits, `_`
or `-`, and every other key is dropped. Same rule and same reason as
`gateway/events.py` -- a model library's exception message quotes its
input, and the input here is speech of third parties.

The read side uses `selectors` on the raw file descriptor instead of
`readline()`: a blocking `readline()` has no timeout, and a worker that
hangs on one 8-second window would hang the whole measurement with it.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

# pipecat's smart-turn VAD ends a turn 0.2 s after speech stops, so that
# is where a live system would ask and where the measured window has to
# end. PINNED (plan D4), not tuned: three sessions are far too few to fit
# a constant on without fitting noise.
STOP_S = 0.2
# The model's input is exactly 8.0 s at hop 160 -> 800 mel frames (F4).
WINDOW_S = 8.0
REQUEST_TIMEOUT_S = 30.0
# Laya loads for about 16 s on this machine (F5); 120 s leaves room for a
# cold page cache without letting a truly stuck worker block a run.
START_TIMEOUT_S = 120.0

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NUMBER_KEYS = ("id", "p", "ms", "cpu_ms", "load_s")
READ_CHUNK = 65536


class WorkerError(Exception):
    """The worker did not answer usably. Messages carry numbers only."""


@dataclass
class UttSpan:
    """Where one utterance sits on the audio clock.

    `sim` is 1.0 when the span came from sub-project A's own
    `t_start`/`t_end` fields, and the Jaccard similarity of the offline
    alignment otherwise. `bench/align_audio.is_aligned()` is the one
    place that turns it into a yes or no.
    """
    index: int
    t_start: float
    t_end: float
    sim: float


@dataclass
class Decision:
    """One candidate's answer for one utterance.

    `t_dec_audio` is the audio-clock second at which the decision would
    have been available -- the same clock the reference point `t_end`
    lives on, so a delay is a subtraction and not a unit conversion. It
    is None when the session has no audio clock at all, which is the
    case for a text candidate run without an alignment.
    """
    index: int
    p: float
    t_dec_audio: Optional[float]
    wall_ms: float
    cpu_ms: float


def clean_reply(obj) -> dict:
    """One reply line, stripped down to numbers and one token.

    An allow-list, not a deny-list: a future worker field nobody
    remembered to think about is dropped, not forwarded. The alternative
    -- passing the child's dict through -- puts whatever a model library
    wrote into a structure the caller prints.
    """
    if not isinstance(obj, dict):
        raise WorkerError("a reply line is not a JSON object")
    out = {}
    for key in NUMBER_KEYS:
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[key] = value
    if "ready" in obj:
        out["ready"] = bool(obj.get("ready"))
    if "error" in obj:
        token = obj.get("error")
        out["error"] = (token if isinstance(token, str)
                        and TOKEN_RE.match(token) else "bad_error")
    return out


def audio_window(span, *, stop_s: float = STOP_S, anchored: bool = True):
    """(begin, end) on the audio clock of the audio one decision sees.

    ANCHORED is the headline variant (plan D4): the window is the turn's
    own audio, `[t_start, t_end + stop_s]`. The worker keeps the last 8 s
    of whatever it is handed and front-pads with zeros -- pipecat's
    convention, where the buffer holds the CURRENT turn. The stream
    variant drops the clip at `t_start` and takes the last 8 s of the raw
    mix instead, which is what a live system with no turn boundary would
    have; it is reported as a secondary number.

    The clipping to 8 s deliberately does NOT happen here: only the
    worker knows how many frames the file actually has.
    """
    end = float(span.t_end) + float(stop_s)
    if end < 0.0:
        end = 0.0
    begin = float(span.t_start) if anchored else end - WINDOW_S
    if begin < 0.0:
        begin = 0.0
    if begin > end:
        begin = end
    return (round(begin, 3), round(end, 3))


class WorkerClient:
    """One model worker as a subprocess, spoken to in JSON lines."""

    def __init__(self, argv, *, timeout_s: float = REQUEST_TIMEOUT_S,
                 start_timeout_s: float = START_TIMEOUT_S, env=None) -> None:
        self.argv = [str(a) for a in argv]
        self.timeout_s = float(timeout_s)
        self.start_timeout_s = float(start_timeout_s)
        self.env = env
        self.proc = None
        self.load_s = 0.0
        self._sel = None
        self._buf = b""

    # ------------------------------------------------------------- API
    def start(self) -> dict:
        """Start the worker and read its ready line."""
        try:
            self.proc = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=self.env, bufsize=0)
        except OSError as exc:
            raise WorkerError(
                f"the worker did not start ({type(exc).__name__})") from None
        self._sel = selectors.DefaultSelector()
        self._sel.register(self.proc.stdout, selectors.EVENT_READ)
        reply = clean_reply(self._read_json(self.start_timeout_s))
        if not reply.get("ready"):
            raise WorkerError("the worker sent no ready line")
        self.load_s = float(reply.get("load_s") or 0.0)
        return reply

    def ask(self, request: dict) -> dict:
        """One request line, one cleaned reply."""
        if self.proc is None:
            raise WorkerError("the worker is not running")
        line = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise WorkerError(
                f"cannot write to the worker ({type(exc).__name__})") from None
        return clean_reply(self._read_json(self.timeout_s))

    def close(self) -> None:
        """Terminate the worker, then kill it if it will not go."""
        if self.proc is None:
            return
        if self._sel is not None:
            self._sel.close()
            self._sel = None
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -------------------------------------------------------- internal
    def _read_json(self, budget: float):
        deadline = time.monotonic() + float(budget)
        while True:
            pos = self._buf.find(b"\n")
            if pos >= 0:
                line, self._buf = self._buf[:pos], self._buf[pos + 1:]
                if not line.strip():
                    continue
                try:
                    return json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    raise WorkerError(
                        f"a reply of {len(line)} bytes is not JSON") from None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError(
                    f"no answer within {float(budget):.1f} s")
            if not self._sel.select(remaining):
                continue
            try:
                chunk = os.read(self.proc.stdout.fileno(), READ_CHUNK)
            except OSError as exc:
                raise WorkerError(
                    f"cannot read the worker ({type(exc).__name__})") from None
            if not chunk:
                raise WorkerError("the worker closed its output")
            self._buf += chunk
