#!/usr/bin/env python3
# CoThinker — agent lanes: 1 running + 1 waiting, newest wins or merged
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""One lane = one dedicated `WorkerClient` process behind a tiny queue.

konzept-spuren-architektur.md's §"Ursache des Staus am 26.09.": a single
agent served calls, protocol and stage one after another, so the queue
grew without bound the moment blocks arrived faster than a 29 s turn.
The fix is per lane, not a bigger queue: AT MOST one job runs and AT MOST
one job waits. A submission that arrives while one is already waiting
REPLACES it -- the newest request always wins, never a third slot --
unless the lane was built with a `merge` function: then the new job is
folded INTO the waiting one (the call lane: every call must be
answered, 2026-10-20 replay dropped 3 of 4 back-to-back calls). Either
way the waiting slot holds at most one job.

This module only schedules; it knows nothing about prompts, topics or
stage text. Call sites (the call lane, the coach lane) build the prompt
and interpret the result.
"""
from __future__ import annotations

import threading
import time
import weakref
from concurrent.futures import ProcessPoolExecutor as _ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool as _BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from worker import WorkerClient

# Matches the literal text of WorkerClient.run()'s own timeout error
# ("timeout after {timeout:.0f}s or worker gone") -- checked, not
# invented, so a wording change there is a wording change here too.
TIMEOUT_MARKER = "timeout after"

# Worst case ~3x this (WorkerClient.stop()'s three phases: wait on EOF,
# SIGTERM, SIGKILL) before a hung worker is actually gone -- a handful
# of seconds on top of the turn's own timeout, never anywhere near 900s.
TIMEOUT_STOP_GRACE_S = 1.0


@dataclass
class Job:
    """One request for a lane. `meta` carries whatever the call site
    needs back in its `on_result` callback (e.g. the topic id a coach
    job was filed under) -- this module never reads it."""
    id: str
    prompt: str
    timeout: float = 900.0
    meta: dict = field(default_factory=dict)
    submitted_at: float = 0.0


class TaskSlot:
    """At most 1 running + 1 waiting job, newest wins or merged.

    The core of `LaneQueue`, extracted so the "1
    running + 1 waiting" rule survives spec section 6's drop of the lane
    *mechanism* (a dedicated `WorkerClient` process per purpose): `run`
    is any `Callable[[Job], dict]` -- a `WorkerClient.run` wrapper, a hub
    coroutine wrapper, an `AgentPort.send_turn` -- this class knows
    nothing about what runs underneath it.

    `submit()` never blocks: it hands the job to a dedicated thread and
    returns immediately, so the caller (the SSE reader, the decider
    callback) is never held up by a 40-180 s agent turn. When a job is
    already waiting, the new one replaces it -- the replaced job's
    `on_dropped` callback fires (if given), it is NEVER run, and the
    slot's waiting count therefore never exceeds 1.

    With `merge(waiting, new) -> Job` given, nothing is ever replaced:
    the new job is merged into the waiting one instead (`jobs_merged`).
    A job still waiting when the slot stops is handed to `on_dropped`
    too, so a caller that showed an acknowledgement can resolve it.
    """

    # Must 8a: every TaskSlot ever built in this process
    # self-registers here (a WeakSet, so a stopped/discarded slot is never
    # kept alive by this alone) -- the hub's God Mode loop-lag alert needs
    # "what was running across every table's terms/bullets/echo/setting/
    # pairs/check slot at the moment the lag spiked" without each of the
    # ~7 call sites across workshop/hub.py handing its slot up to a
    # registry of its own.
    _instances: "weakref.WeakSet[TaskSlot]" = weakref.WeakSet()

    def __init__(self, run: Callable[[Job], dict], *,
                 on_result: Callable[[Job, dict, float], None],
                 on_dropped: Optional[Callable[[Job], None]] = None,
                 merge: Optional[Callable[[Job, Job], Job]] = None,
                 now: Callable[[], float] = time.monotonic,
                 log: Callable[..., None] = print) -> None:
        self.run = run
        self.on_result = on_result
        self.on_dropped = on_dropped
        self.merge = merge
        self.now = now
        self.log = log
        self._waiting: Optional[Job] = None
        self._running_job: Optional[Job] = None
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self.max_waiting_seen = 0
        self.jobs_run = 0
        self.jobs_dropped = 0
        self.jobs_merged = 0
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="lane-runner")
        TaskSlot._instances.add(self)

    @classmethod
    def running_jobs(cls) -> List[str]:
        """The `Job.id` of every job running right now, across every
        `TaskSlot` alive in this process. Each id already carries its
        table and kind (e.g. `"01-terms-P2-172345.678901"`, terms.py's own
        `TermsTaskHook.__call__`), so this alone is "the name of the job"
        Must 8a asks the loop-lag alert to carry -- no second naming
        scheme to keep in sync with the ~7 `Job(id=...)` call sites."""
        out = []
        for slot in list(cls._instances):
            with slot._lock:
                if slot._running_job is not None:
                    out.append(slot._running_job.id)
        return out

    def start(self) -> None:
        self._thread.start()

    def submit(self, job: Job) -> bool:
        """Queues `job`. Returns True if it replaced an already-waiting
        job (that job is dropped, its `on_dropped` fires if given).
        With a `merge` function the waiting job absorbs `job` instead
        and this returns False -- nothing was dropped."""
        job.submitted_at = self.now()
        dropped: Optional[Job] = None
        with self._lock:
            if self._waiting is not None and self.merge is not None:
                merged = self.merge(self._waiting, job)
                # The merged job waited since the OLDER submission.
                merged.submitted_at = self._waiting.submitted_at
                self._waiting = merged
                self.jobs_merged += 1
                self._wakeup.set()
                return False
            if self._waiting is not None:
                dropped = self._waiting
            self._waiting = job
            self.max_waiting_seen = max(self.max_waiting_seen, 1)
        if dropped is not None:
            self.jobs_dropped += 1
            if self.on_dropped is not None:
                self.on_dropped(dropped)
        self._wakeup.set()
        return dropped is not None

    def waiting_count(self) -> int:
        with self._lock:
            return 1 if self._waiting is not None else 0

    def running(self) -> bool:
        with self._lock:
            return self._running_job is not None

    def stats(self) -> dict:
        with self._lock:
            return {
                "waiting": 1 if self._waiting is not None else 0,
                "running": self._running_job is not None,
                "max_waiting_seen": self.max_waiting_seen,
                "jobs_run": self.jobs_run,
                "jobs_dropped": self.jobs_dropped,
                "jobs_merged": self.jobs_merged,
            }

    def stop(self, grace: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        self._thread.join(timeout=grace + 1)
        # A job that never got to run is a drop too: without this its
        # caller's acknowledgement would stay on the stage forever.
        with self._lock:
            leftover, self._waiting = self._waiting, None
        if leftover is not None:
            self.jobs_dropped += 1
            if self.on_dropped is not None:
                try:
                    self.on_dropped(leftover)
                except Exception as e:        # noqa: BLE001
                    self.log("lane: on_dropped failed:", repr(e))

    # ------------------------------------------------------------ internal
    def _loop(self) -> None:
        while not self._stop.is_set():
            fired = self._wakeup.wait(timeout=0.5)
            if fired:
                self._wakeup.clear()
            if self._stop.is_set():
                return
            job: Optional[Job] = None
            with self._lock:
                if self._waiting is not None:
                    job = self._waiting
                    self._waiting = None
                    self._running_job = job
            if job is None:
                continue
            self._run_job(job)
            with self._lock:
                self._running_job = None

    def _run_job(self, job: Job) -> None:
        t0 = self.now()
        try:
            result = self.run(job)
        except Exception as e:                # noqa: BLE001 -- a slot must
            # never take the consumer down with it; it reports the failure
            # as a normal failed result instead of raising into the loop.
            result = {"ok": False, "final": "", "error": repr(e)}
        self.jobs_run += 1
        duration = self.now() - t0
        try:
            self.on_result(job, result, duration)
        except Exception as e:                # noqa: BLE001
            self.log("lane: on_result failed:", repr(e))


class ProcessSlot:
    """Must 7: the pattern any future CPU-heavy job should
    use instead of `TaskSlot` -- a thread still shares the hub's heap and
    its GIL, so a tight pure-Python loop on one (this requirement's own root
    cause: `workshop/term_merge.py`'s `medoid_label`, O(m^2) over 3584-dim
    vectors) can starve the asyncio loop no matter how little of the
    *wall-clock* budget it uses. Running `run` in a separate OS process
    (`ProcessPoolExecutor`) makes that structurally impossible: the heavy
    loop holds a GIL in ITS OWN process, never the hub's.

    Same 1-running + 1-waiting, newest-wins submit() contract as
    `TaskSlot` (no `merge` support here -- no current CPU-heavy job needs
    it; add it the same way `TaskSlot` has it, if one ever does), with two
    differences a caller must respect:

    - `run` MUST be a plain, picklable, module-level function. A bound
      method, a closure or a lambda either fails to pickle outright or
      (worse, on a fork-start platform) "works" by accident via
      copy-on-write memory it was never meant to share -- `Job.meta` is
      the ONLY channel between the hub and the worker, by construction,
      not by convention.
    - A worker crash (OOM-kill, segfault, anything the pool itself can't
      marshal back) raises `BrokenProcessPool` for every future the pool
      still owns and leaves the pool itself unusable. `_run_job` catches
      exactly that, logs it, and replaces the pool before the next job --
      the hub keeps running; only that one job is reported as failed.
    """

    def __init__(self, run: Callable[[Job], dict], *,
                 on_result: Callable[[Job, dict, float], None],
                 max_workers: int = 1,
                 now: Callable[[], float] = time.monotonic,
                 log: Callable[..., None] = print) -> None:
        self.run = run
        self.on_result = on_result
        self.max_workers = max_workers
        self.now = now
        self.log = log
        self._executor = _ProcessPoolExecutor(max_workers=max_workers)
        self._waiting: Optional[Job] = None
        self._running_job: Optional[Job] = None
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self.jobs_run = 0
        self.jobs_dropped = 0
        self.jobs_crashed = 0
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="process-slot-runner")

    def start(self) -> None:
        self._thread.start()

    def submit(self, job: Job) -> bool:
        """Same newest-wins rule as `TaskSlot.submit` -- see there."""
        job.submitted_at = self.now()
        dropped: Optional[Job] = None
        with self._lock:
            if self._waiting is not None:
                dropped = self._waiting
            self._waiting = job
        if dropped is not None:
            self.jobs_dropped += 1
        self._wakeup.set()
        return dropped is not None

    def waiting_count(self) -> int:
        with self._lock:
            return 1 if self._waiting is not None else 0

    def running(self) -> bool:
        with self._lock:
            return self._running_job is not None

    def stats(self) -> dict:
        with self._lock:
            return {"waiting": 1 if self._waiting is not None else 0,
                   "running": self._running_job is not None,
                   "jobs_run": self.jobs_run, "jobs_dropped": self.jobs_dropped,
                   "jobs_crashed": self.jobs_crashed}

    def stop(self, grace: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        self._thread.join(timeout=grace + 1)
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ internal
    def _loop(self) -> None:
        while not self._stop.is_set():
            fired = self._wakeup.wait(timeout=0.5)
            if fired:
                self._wakeup.clear()
            if self._stop.is_set():
                return
            job: Optional[Job] = None
            with self._lock:
                if self._waiting is not None:
                    job = self._waiting
                    self._waiting = None
                    self._running_job = job
            if job is None:
                continue
            self._run_job(job)
            with self._lock:
                self._running_job = None

    def _run_job(self, job: Job) -> None:
        t0 = self.now()
        try:
            future = self._executor.submit(self.run, job)
            result = future.result(timeout=job.timeout)
        except _BrokenProcessPool as e:
            self.jobs_crashed += 1
            self.log(f"ProcessSlot: worker crashed ({e!r}); replacing the pool")
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:                  # noqa: BLE001 -- already broken, nothing more to save
                pass
            self._executor = _ProcessPoolExecutor(max_workers=self.max_workers)
            result = {"ok": False, "final": "", "error": repr(e)}
        except Exception as e:                  # noqa: BLE001 -- a slot must never take the hub down with it
            result = {"ok": False, "final": "", "error": repr(e)}
        self.jobs_run += 1
        duration = self.now() - t0
        try:
            self.on_result(job, result, duration)
        except Exception as e:                  # noqa: BLE001
            self.log("ProcessSlot: on_result failed:", repr(e))


class LaneQueue:
    """`TaskSlot` wired to one `WorkerClient`, plus the worker-specific
    hung-turn kill that `TaskSlot` knows nothing about.

    Same public API as before the `TaskSlot` extraction:
    constructor signature, methods, behaviour and the
    `jobs_timed_out`/`restarts` stats all byte-for-byte unchanged.
    """

    def __init__(self, worker: WorkerClient, *,
                 on_result: Callable[[Job, dict, float], None],
                 on_dropped: Optional[Callable[[Job], None]] = None,
                 merge: Optional[Callable[[Job, Job], Job]] = None,
                 now: Callable[[], float] = time.monotonic,
                 log: Callable[..., None] = print) -> None:
        self.worker = worker
        self.on_result = on_result
        self.on_dropped = on_dropped
        self.now = now
        self.log = log
        self.jobs_timed_out = 0
        self._slot = TaskSlot(self._run_via_worker, on_result=on_result,
                              on_dropped=on_dropped, merge=merge, now=now,
                              log=log)

    @property
    def merge(self) -> Optional[Callable[[Job, Job], Job]]:
        return self._slot.merge

    @merge.setter
    def merge(self, value: Optional[Callable[[Job, Job], Job]]) -> None:
        self._slot.merge = value

    def start(self) -> bool:
        ok = self.worker.start()
        self._slot.start()
        return ok

    def submit(self, job: Job) -> bool:
        """Queues `job`. Returns True if it replaced an already-waiting
        job (that job is dropped, its `on_dropped` fires if given).
        With a `merge` function the waiting job absorbs `job` instead
        and this returns False -- nothing was dropped."""
        return self._slot.submit(job)

    def waiting_count(self) -> int:
        return self._slot.waiting_count()

    def running(self) -> bool:
        return self._slot.running()

    def stats(self) -> dict:
        stats = self._slot.stats()
        stats["jobs_timed_out"] = self.jobs_timed_out
        stats["restarts"] = self.worker.restarts
        return stats

    def stop(self, grace: float = 5.0) -> None:
        self._slot.stop(grace=grace)
        self.worker.stop(grace=grace)

    def _run_via_worker(self, job: Job) -> dict:
        result = self.worker.run(job.prompt, timeout=job.timeout)
        if TIMEOUT_MARKER in str(result.get("error") or ""):
            # `WorkerClient.run()`'s own timeout only stops WAITING for an
            # answer -- the worker process is still mid-turn (live incident:
            # a hung turn ran until the surrounding 900s
            # process timeout). Actively killing it here is what makes
            # "never let a lane turn run anywhere near 900s" true: the
            # NEXT job gets a genuinely fresh process (WorkerClient.run()
            # restarts automatically once `alive()` is false) instead of
            # queueing behind a zombie that may never finish at all.
            self.jobs_timed_out += 1
            try:
                # A short grace, not the default 5s: `stop()`'s first
                # phase sends no signal at all, it only waits politely
                # for the process to notice stdin closed -- useless for
                # a turn that is stuck mid-computation (a tool call, an
                # API retry loop), and every second there is a second
                # the "never anywhere near 900s" promise is not kept.
                self.worker.stop(grace=TIMEOUT_STOP_GRACE_S)
            except Exception as e:            # noqa: BLE001
                self.log("lane: could not stop a timed-out worker:", repr(e))
        return result
