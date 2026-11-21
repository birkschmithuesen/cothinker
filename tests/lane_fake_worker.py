#!/usr/bin/env python3
# CoThinker — tests/lane_fake_worker.py: worker stand-in for the lane tests
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A worker stand-in. Speaks the protocol, never imports Hermes.

    python3 tests/lane_fake_worker.py --session-id X [--mode MODE]

Modes: ok | fail | partial | apierror | httperror | silent | die |
       die-once | deaf | slow | garbage

The "-once"-ish modes (die-once, deaf) behave differently in the process
that follows a restart. That cannot be kept in memory -- the process is
gone -- so it is kept in a marker file named after the session id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--session-id", required=True)
ap.add_argument("--mode", default="ok")
ap.add_argument("--fail-starts", type=int, default=0,
                help="exit before sending 'ready' on this many consecutive "
                     "process launches of this session, then behave as ok "
                     "(WorkerClient.start()'s own retry)")
ap.add_argument("--sleep", type=float, default=0.0,
                help="seconds to sleep before answering a turn -- a "
                     "controllable stand-in for a real agent's run time, "
                     "for lane-queue tests that must not wait on the fixed "
                     "30s of --mode slow")
ap.add_argument("--final", default="",
                help="overrides --mode ok's default final text -- for "
                     "testing a specific output contract (e.g. the coach "
                     "lane's '[SILENT]' / 'Spiegel:' / 'Struktur:' / "
                     "'Frage:' labels) without a real agent")
ap.add_argument("--hermes-home", default="",
                help="accepted and ignored -- lane commands derived from "
                     "--worker-command append it ")
ap.add_argument("--no-history", action="store_true",
                help="accepted and ignored -- every lane command carries it")
ap.add_argument("--prompt-log", default="",
                help="append every received turn prompt to this file "
                     "(acceptance harness: the not_in_prompts assertions)")
ap.add_argument("--model", default="")
ap.add_argument("--provider", default="")
ap.add_argument("--usage-in", type=int, default=0,
                help="input_tokens reported on every turn's result (the "
                     "gateway worker reports per-turn "
                     "usage; this stands in for the real agent's delta)")
ap.add_argument("--usage-out", type=int, default=0,
                help="output_tokens reported on every turn's result")
a = ap.parse_args()


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def marker(tag: str) -> Path:
    return Path(tempfile.gettempdir()) / f"cothinker-fake-{tag}-{a.session_id}"


def first_process(tag: str) -> bool:
    """True in the first process of this session, False in every restart."""
    try:
        marker(tag).touch(exist_ok=False)   # O_EXCL: only the first one wins
        return True
    except FileExistsError:
        return False


def launch_count(tag: str) -> int:
    """How many times (including this one) a process of this session has
    hit this call -- a counter, not a one-shot marker: `--fail-starts`
    needs to count launches past the first restart."""
    p = marker(tag)
    try:
        n = int(p.read_text())
    except (OSError, ValueError, FileNotFoundError):
        n = 0
    p.write_text(str(n + 1))
    return n + 1


if a.fail_starts and launch_count("fail-starts") <= a.fail_starts:
    sys.exit(1)                            # never sends "ready"

send({"type": "ready", "session_id": a.session_id})

if a.mode == "deaf" and first_process("deaf"):
    # Closes stdout but stays alive: the client's reader thread ends at
    # EOF and never comes back, while the process itself is still there.
    # It has to be the file descriptor -- sys.stdout.close() leaves fd 1
    # open (CPython builds the standard streams with closefd=False), and
    # then no EOF ever reaches the client. Redirected to /dev/null rather
    # than plainly closed, so a later write finds a valid fd.
    sys.stdout.flush()
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)
    for _ in sys.stdin:                    # hangs on until the client lets go
        pass
    sys.exit(0)

slow_once = a.mode == "slow-once" and first_process("slow-once")

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("type") != "turn":
        continue
    tid = msg.get("id")
    if a.prompt_log:
        with open(a.prompt_log, "a", encoding="utf-8") as fh:
            fh.write(str(msg.get("prompt", "")) + "\n\x1e\n")
    if a.mode == "die":
        sys.exit(3)
    if a.mode == "die-once" and first_process("die-once"):
        sys.exit(3)                        # only the first process dies
    if a.mode == "slow":
        time.sleep(30)
    if slow_once:
        time.sleep(30)                     # only the first process is slow
    if a.sleep:
        time.sleep(a.sleep)
    if a.mode == "garbage":
        sys.stdout.write("not json at all\n")
        sys.stdout.flush()
        continue
    result = {"type": "result", "id": tid, "ok": True, "failed": False,
              "partial": False,
              "final": a.final or "OK — mitgeschrieben", "error": None,
              "usage": {"input_tokens": a.usage_in, "output_tokens": a.usage_out}}
    if a.mode == "fail":
        result |= {"ok": False, "failed": True, "final": "",
                   "error": "provider refused"}
    elif a.mode == "partial":
        result |= {"ok": False, "partial": True, "final": "halb fertig"}
    elif a.mode == "apierror":
        result |= {"final": "API call failed after 5 retries: 429"}
    elif a.mode == "httperror":
        # Reports success, but the text betrays it: HTTP 4xx from the
        # provider. The second of the two pinned failure markers.
        result |= {"final": "Antwort verworfen: HTTP 429 Too Many Requests"}
    elif a.mode == "silent":
        result |= {"final": ""}
    send(result)
