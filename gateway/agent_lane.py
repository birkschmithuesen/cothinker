#!/usr/bin/env python3
# CoThinker — a lane driven through the agent seam instead of a worker process
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`COTHINKER_AGENT=api`: a lane's `run(job) -> dict` over an `AgentPort`
(docs/architecture.md §1), today `shared.agent.HermesApiAgentPort`
against a Hermes API server on another machine.

`SeamRunner.run()` returns exactly the dict `gateway.worker.WorkerClient.run`
returns (`ok`, `final`, `usage`, `error`), including the timeout wording
`gateway.lanes.TIMEOUT_MARKER` matches, so `gateway/lane_integration.py`'s
result handlers need no second code path.

Sessions. A lane worker runs with `--no-history`: every
turn's prompt carries the whole bounded window, nothing older is replayed.
The API server, unlike that worker, replays a session's stored conversation
before every turn. So by default every job gets a FRESH session
(`turns_per_session=1`, creating one is a local SQLite insert, ~10 ms) --
the same input the worker sees. A larger value keeps a session for that
many turns; it is also dropped whenever the lane's window rolls
(`on_roll()`) and after any failure or timeout -- the moments the worker
path restarts its process.

Briefing. `open_session` always gets an empty briefing -- the API server
has no per-session profile selector (the session-create call takes no
body, `shared/agent.py`), so whichever profile the server answering
`COTHINKER_AGENT_URL` happens to run is NOT necessarily this lane's own
(a shared server backs every api-lane, so a coach lane
cannot assume the server's ambient profile carries the coach SOUL at
all). `SeamRunner` closes that gap itself: a non-empty `briefing` is
prepended to the first turn's own prompt text instead of sent as a
separate priming turn -- no extra model call, no extra latency, nothing
added to the replayed history beyond what that turn already carries.
`coach_soul_briefing()` below reads it straight off the coach profile's
own `SOUL.md`, the same file `gateway/worker.py`'s coach gets for free
via `--hermes-home`.

`SeamLane` is `gateway.lanes.LaneQueue`'s public API (start/submit/
waiting_count/running/stats/stop/merge) over a `TaskSlot`, so `Lanes` can
hold either.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes import Job, TaskSlot  # noqa: E402


class SeamRunner:
    """`run(job) -> dict` over one `AgentPort` for one lane."""

    def __init__(self, port, *, context_id: str, briefing: str = "",
                 turns_per_session: int = 1,
                 log: Callable[..., None] = print) -> None:
        self.port = port
        self.context_id = context_id
        self.briefing = briefing
        self.turns_per_session = max(1, int(turns_per_session))
        self.log = log
        self._session = None
        self._turns = 0
        self._lock = threading.Lock()
        self.sessions_opened = 0
        self.timeouts = 0

    def on_roll(self) -> None:
        """The lane's window rolled: the next job opens a fresh session."""
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        if self._session is not None:
            try:
                self.port.close(self._session)
            except Exception:                 # noqa: BLE001
                pass
        self._session = None
        self._turns = 0

    def _session_for_turn(self):
        """Returns `(session, opened_now)` -- `opened_now` tells `run()`
        whether this is the session's first turn, the only turn the
        briefing (if any) belongs on."""
        with self._lock:
            if self._session is not None and self._turns >= self.turns_per_session:
                self._drop()
            opened_now = self._session is None
            if opened_now:
                # No briefing passed here (see the module docstring):
                # `run()` prepends it to this turn's own prompt instead.
                self._session = self.port.open_session(
                    f"{self.context_id}-{self.sessions_opened + 1}", "")
                self.sessions_opened += 1
            self._turns += 1
            return self._session, opened_now

    def run(self, job: Job) -> dict:
        try:
            session, opened_now = self._session_for_turn()
        except Exception as e:                # noqa: BLE001
            # The error class only: an HTTP error message may echo input.
            return {"ok": False, "final": "", "usage": {},
                    "error": f"agent session not opened: {type(e).__name__}"}
        prompt = (f"{self.briefing}\n\n{job.prompt}"
                 if opened_now and self.briefing else job.prompt)
        events: queue.Queue = queue.Queue()

        def pump() -> None:
            try:
                for ev in self.port.send_turn(session, prompt):
                    events.put(ev)
            except Exception as e:            # noqa: BLE001
                events.put(e)
            finally:
                events.put(None)

        threading.Thread(target=pump, daemon=True, name="seam-turn").start()
        deadline = time.monotonic() + job.timeout
        chunks: list = []
        usage: dict = {}
        outcome: Optional[str] = None
        reason = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ev = events.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if ev is None:
                break
            if isinstance(ev, Exception):
                outcome, reason = "failed", type(ev).__name__
                break
            if ev.kind == "text_delta":
                chunks.append(ev.text)
            elif ev.kind == "done":
                outcome, usage = "done", dict(ev.usage or {})
            elif ev.kind == "failed":
                outcome, usage, reason = "failed", dict(ev.usage or {}), ev.reason
        if outcome is None and time.monotonic() >= deadline:
            # Same wording as WorkerClient.run(): lanes.TIMEOUT_MARKER.
            self.timeouts += 1
            try:
                self.port.stop(session)
            except Exception:                 # noqa: BLE001
                pass
            with self._lock:
                self._drop()
            return {"ok": False, "final": "", "usage": {},
                    "error": f"timeout after {job.timeout:.0f}s or worker gone"}
        final = "".join(chunks)
        if outcome != "done":
            with self._lock:
                self._drop()
            return {"ok": False, "final": final, "usage": usage,
                    "error": f"agent turn failed: {reason or 'stream ended'}"}
        return {"ok": True, "final": final, "usage": usage, "error": ""}


class SeamLane:
    """`LaneQueue`'s API over a `TaskSlot` running a `SeamRunner`."""

    def __init__(self, runner: SeamRunner, *,
                 on_result: Callable[[Job, dict, float], None],
                 on_dropped: Optional[Callable[[Job], None]] = None,
                 merge: Optional[Callable[[Job, Job], Job]] = None,
                 log: Callable[..., None] = print) -> None:
        self.runner = runner
        self._slot = TaskSlot(runner.run, on_result=on_result,
                              on_dropped=on_dropped, merge=merge, log=log)

    @property
    def merge(self):
        return self._slot.merge

    @merge.setter
    def merge(self, value) -> None:
        self._slot.merge = value

    def start(self) -> bool:
        self._slot.start()
        return True

    def submit(self, job: Job) -> bool:
        return self._slot.submit(job)

    def waiting_count(self) -> int:
        return self._slot.waiting_count()

    def running(self) -> bool:
        return self._slot.running()

    def on_roll(self) -> None:
        self.runner.on_roll()

    def stats(self) -> dict:
        stats = self._slot.stats()
        stats["jobs_timed_out"] = self.runner.timeouts
        stats["restarts"] = 0
        stats["sessions_opened"] = self.runner.sessions_opened
        return stats

    def stop(self, grace: float = 5.0) -> None:
        self._slot.stop(grace=grace)


def coach_soul_briefing(hermes_home: Path) -> str:
    """The coach profile's `SOUL.md`, stripped; `""` if it cannot be read.

    See the module docstring: the API seam has no per-session profile
    selector, so a coach lane over it cannot assume the server's ambient
    profile is this one. Reading the SOUL straight off the coach
    profile's own directory and sending it as the session's briefing
    (`SeamRunner`) gets the API coach the same standing instructions the
    worker coach gets for free via `--hermes-home`.
    """
    try:
        return Path(hermes_home, "SOUL.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def api_lane_factory(url: str, key: str, *, turns_per_session: int = 1,
                     log: Callable[..., None] = print):
    """Returns `make(context_id, on_result, on_dropped, merge, briefing)
    -> SeamLane` over one shared `HermesApiAgentPort`. `briefing` (default
    `""`) is this lane's own standing instructions -- see
    `coach_soul_briefing()` for the coach lane's."""
    from shared.agent import HermesApiAgentPort
    port = HermesApiAgentPort(url, key, timeout_s=180)

    def make(context_id, *, on_result, on_dropped=None, merge=None,
            briefing: str = ""):
        runner = SeamRunner(port, context_id=context_id, briefing=briefing,
                            turns_per_session=turns_per_session, log=log)
        return SeamLane(runner, on_result=on_result, on_dropped=on_dropped,
                        merge=merge, log=log)
    return make
