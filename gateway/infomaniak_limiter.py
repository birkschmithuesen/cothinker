#!/usr/bin/env python3
# CoThinker — one shared Infomaniak rate limiter for both call paths
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Infomaniak answers HTTP 429 above a requests/min level
it has not told us (docs/messungen/2026-10-23-hermes-api-m1-m3.md).
Until Infomaniak raises that level, and as a safety net afterwards, this
turns a 429 into a short delay instead of a failed turn.

This instance has exactly two things that call Infomaniak:

1. The hub's direct calls (`workshop/bullets.py` stage-2/3 ->
   `gateway/infomaniak_chat.py`'s `Client`) -- small, frequent, a ~3-4s
   latency budget with no retry margin (`workshop/bullets.py`'s
   `LIVE_BACKOFF`).
2. The Hermes profile's own provider calls (the S11 prologue scene flow,
   `workshop/profile/config.yaml`'s `custom_providers`) -- large,
   infrequent, a 45-130s per-turn timeout. That client lives in a
   SEPARATE process (the Hermes gateway), not in this repo: the only way
   to put both paths through ONE shared budget is for both of them to
   point their base URL at this proxy instead of at Infomaniak directly
   (`gateway/infomaniak_chat.py`'s `COTHINKER_INFOMANIAK_BASE` for path
   1, `workshop/profile/config.yaml`'s `custom_providers[0].base_url`
   for path 2) -- there is no in-process object both could share, since
   path 2's HTTP client is not Python code this repo controls.

`InfomaniakLimiter` listens on two ports (`high_port` for path 1,
`low_port` for path 2) backed by ONE `TokenBucket`, and transparently
forwards whatever it receives to the real upstream, retrying a 429/500/
502/503 (the same `RETRY_STATUS` `gateway/infomaniak_chat.py` already
classifies as retryable) with jittered backoff, honouring `Retry-After`
when Infomaniak sends one. The caller never sees the 429 -- it waits.

Priority is why there are two ports and not one: under contention, a
HIGH-priority (bullets) acquire is served before an already-queued LOW-
priority (agent) one, every time; a LOW acquire only takes a token when
no HIGH acquire is currently waiting. A LOW call can therefore wait
arbitrarily long under sustained HIGH load -- accepted, per the card
("a lower-priority call waits longer under contention, a higher-priority
one does not starve behind it").

`rpm` is a runtime value (`set_rpm()`, or the `--rpm` / `COTHINKER_INFOMANIAK_RPM`
default below), not a hardcoded constant -- it changes once the operator hears
back from Infomaniak support (docs/messungen/2026-10-27-infomaniak-rate-budget.md).
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]
for _p in (REPO, REPO / "gateway"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from infomaniak_chat import DEFAULT_BASE, RETRY_STATUS  # noqa: E402

PRIORITIES = ("high", "low")

# docs/messungen/2026-10-27-infomaniak-rate-budget.md §6: ~40 req/min
# combined at 10 tables is the current derived figure; this default sits
# comfortably above today's measured load so the limiter is a safety net,
# not a throttle, until the operator has Infomaniak's own answer to tune it by.
DEFAULT_RPM = 120.0
# Matches InfomaniakLimiter.__init__'s own acquire_timeout default -- the
# CLI flag below must not change behaviour for anyone not setting it.
DEFAULT_ACQUIRE_TIMEOUT_S = 300.0
# Hop-by-hop headers a reverse proxy must not blindly forward (RFC 7230 §6.1);
# Content-Length is recomputed from the actual relayed body.
_DROP_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})


def jittered_backoff(attempt: int, *, base: float = 1.0, cap: float = 30.0,
                     rand: Callable[[], float] = random.random) -> float:
    """AWS-style full jitter: U(0, min(cap, base * 2**attempt))."""
    return rand() * min(cap, base * (2.0 ** attempt))


class TokenBucket:
    """A requests/min token bucket with two priority queues. `acquire()`
    blocks (queues) instead of raising -- "queue, not fail" is the whole
    point of this card. `capacity` defaults to `max(1.0, rpm)`: one
    second's worth of budget can burst through at once, matching how a
    real per-minute vendor limit is usually enforced (not a strict
    one-every-(60/rpm)s metronome)."""

    def __init__(self, rpm: float, *, capacity: Optional[float] = None,
                now: Callable[[], float] = time.monotonic) -> None:
        self.rpm = float(rpm)
        self._capacity = float(capacity) if capacity is not None else max(1.0, float(rpm))
        self._now = now
        self._tokens = self._capacity
        self._last = now()
        self._cv = threading.Condition()
        self._waiting = {"high": 0, "low": 0}

    def _refill_locked(self) -> None:
        now = self._now()
        elapsed = max(0.0, now - self._last)
        self._last = now
        if self.rpm > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * (self.rpm / 60.0))

    def set_rpm(self, rpm: float) -> None:
        with self._cv:
            self._refill_locked()
            self.rpm = float(rpm)
            self._cv.notify_all()

    def acquire(self, priority: str = "low", *, timeout: Optional[float] = None) -> bool:
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}, got {priority!r}")
        deadline = None if timeout is None else self._now() + timeout
        with self._cv:
            self._waiting[priority] += 1
            try:
                while True:
                    self._refill_locked()
                    blocked_by_higher = priority == "low" and self._waiting["high"] > 0
                    if self._tokens >= 1.0 and not blocked_by_higher:
                        self._tokens -= 1.0
                        return True
                    remaining = None if deadline is None else deadline - self._now()
                    if remaining is not None and remaining <= 0:
                        return False
                    step = 60.0 / self.rpm if self.rpm > 0 else 1.0
                    wait_s = step if remaining is None else min(step, remaining)
                    self._cv.wait(timeout=max(0.0, wait_s))
            finally:
                self._waiting[priority] -= 1


def _make_handler(bucket: TokenBucket, priority: str, upstream: str, *,
                  max_attempts: int, acquire_timeout: float,
                  sleep: Callable[[float], None], rand: Callable[[], float]):

    class _ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def _relay(self, status: int, headers, body: bytes) -> None:
            try:
                self.send_response(status)
                for k, v in headers:
                    if k.lower() not in _DROP_HEADERS:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def _retry_after(self, http_error: urllib.error.HTTPError) -> Optional[float]:
            if http_error.headers is None:
                return None
            v = http_error.headers.get("Retry-After")
            if not v:
                return None
            try:
                return max(0.0, float(v))
            except ValueError:
                return None

        def _handle(self, method: str) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            if not bucket.acquire(priority, timeout=acquire_timeout):
                self._relay(503, [("Content-Type", "application/json")],
                           json.dumps({"error": "rate limiter: no token within the wait budget"}).encode())
                return
            fwd_headers = {k: v for k, v in self.headers.items() if k.lower() not in _DROP_HEADERS}
            attempt = 0
            while True:
                try:
                    req = urllib.request.Request(
                        upstream + self.path, data=body or None, method=method, headers=fwd_headers)
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        self._relay(resp.status, resp.getheaders(), resp.read())
                    return
                except urllib.error.HTTPError as e:
                    payload = e.read()
                    if e.code in RETRY_STATUS and attempt < max_attempts - 1:
                        attempt += 1
                        wait = self._retry_after(e)
                        wait = (wait + rand()) if wait is not None else jittered_backoff(attempt, rand=rand)
                        sleep(wait)
                        continue
                    self._relay(e.code, (e.headers.items() if e.headers else []), payload)
                    return
                except (urllib.error.URLError, OSError, HTTPException) as e:
                    if attempt < max_attempts - 1:
                        attempt += 1
                        sleep(jittered_backoff(attempt, rand=rand))
                        continue
                    self._relay(502, [("Content-Type", "application/json")],
                               json.dumps({"error": f"upstream unreachable ({type(e).__name__})"}).encode())
                    return

        def do_POST(self) -> None:
            self._handle("POST")

        def do_GET(self) -> None:
            self._handle("GET")

    return _ProxyHandler


class InfomaniakLimiter:
    """Two ports, one shared `TokenBucket`, one upstream. `high_port` is
    for the hub's direct bullets calls, `low_port` for the Hermes
    profile's agent-path calls (see the module docstring for why two
    ports, not a header, carry the priority)."""

    def __init__(self, upstream: str, rpm: float, *, capacity: Optional[float] = None,
                host: str = "127.0.0.1", high_port: int = 0, low_port: int = 0,
                max_attempts: int = 5, acquire_timeout: float = 300.0,
                now: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep,
                rand: Callable[[], float] = random.random) -> None:
        self.upstream = upstream.rstrip("/")
        self.bucket = TokenBucket(rpm, capacity=capacity, now=now)
        self._servers = {}
        for name, priority, port in (("high", "high", high_port), ("low", "low", low_port)):
            handler = _make_handler(self.bucket, priority, self.upstream,
                                    max_attempts=max_attempts, acquire_timeout=acquire_timeout,
                                    sleep=sleep, rand=rand)
            srv = ThreadingHTTPServer((host, port), handler)
            srv.daemon_threads = True
            self._servers[name] = srv
        self._threads: list = []

    @property
    def high_port(self) -> int:
        return self._servers["high"].server_address[1]

    @property
    def low_port(self) -> int:
        return self._servers["low"].server_address[1]

    def start(self) -> None:
        for srv in self._servers.values():
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            self._threads.append(t)

    def set_rpm(self, rpm: float) -> None:
        self.bucket.set_rpm(rpm)

    def shutdown(self) -> None:
        for srv in self._servers.values():
            srv.shutdown()
        for t in self._threads:
            t.join(timeout=5)


def build_arg_parser():
    import argparse
    ap = argparse.ArgumentParser(description="One shared Infomaniak rate limiter for this instance.")
    ap.add_argument("--upstream", default=DEFAULT_BASE)
    ap.add_argument("--rpm", type=float,
                    default=float(os.environ.get("COTHINKER_INFOMANIAK_RPM") or DEFAULT_RPM))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--high-port", type=int, default=8710,
                    help="the hub's direct bullets calls (stage-2/3, latency-critical)")
    ap.add_argument("--low-port", type=int, default=8711,
                    help="the Hermes profile's own agent-path calls")
    ap.add_argument("--acquire-timeout", type=float,
                    default=float(os.environ.get("COTHINKER_INFOMANIAK_ACQUIRE_TIMEOUT_S")
                                 or DEFAULT_ACQUIRE_TIMEOUT_S),
                    help="max wait for a token before a caller gets a 503 "
                         "(docs/messungen/2026-10-27-infomaniak-rate-budget.md §5a: "
                         "deploy wants this well under bullets' own drop threshold, "
                         "not the generous default meant for the agent path alone)")
    return ap


def build_limiter(a) -> InfomaniakLimiter:
    return InfomaniakLimiter(a.upstream, a.rpm, host=a.host, high_port=a.high_port,
                             low_port=a.low_port, acquire_timeout=a.acquire_timeout)


def main(argv=None) -> int:
    a = build_arg_parser().parse_args(argv)
    limiter = build_limiter(a)
    limiter.start()
    print(f"infomaniak_limiter: upstream={limiter.upstream} rpm={a.rpm} "
         f"high=http://{a.host}:{limiter.high_port} low=http://{a.host}:{limiter.low_port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        limiter.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
