# CoThinker — characterization tests for the lane queue
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Lane queue: 1 running + 1 waiting, newest wins. Real worker processes
(the fake stand-in), never a real agent.

    python3 -m pytest -q tests/test_lanes.py
"""
from __future__ import annotations

import sys
import tempfile
import traceback
import threading
import time
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))
from lanes import Job, LaneQueue, TaskSlot  # noqa: E402
from worker import WorkerClient  # noqa: E402

HERE = Path(__file__).resolve().parent
FAKE = str(HERE / "lane_fake_worker.py")


def forget(session_id: str) -> None:
    for p in Path(tempfile.gettempdir()).glob(f"cothinker-fake-*-{session_id}"):
        try:
            p.unlink()
        except OSError:
            pass


def lane(session_id: str, *, sleep: float = 0.3, mode: str = "ok",
        on_result=None, on_dropped=None) -> LaneQueue:
    forget(session_id)
    w = WorkerClient(
        command=[sys.executable, FAKE, "--mode", mode, "--sleep", str(sleep)],
        session_id=session_id, log=lambda *a: None)
    return LaneQueue(w, on_result=on_result or (lambda *a: None),
                     on_dropped=on_dropped, log=lambda *a: None)


def wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


_RESULTS: dict = {}


def _case_ids(names):
    return [pytest.param(i, id=n) for i, n in enumerate(names)]


def _expect(section, i, expected):
    """Run `section` once (it records its checks), then assert check `i`.

    Every check site of the section is its own pytest case; they share one
    run of the section because the scenarios are sequential and real
    (worker subprocesses, a shared fake clock)."""
    if section not in _RESULTS:
        rec: list = []

        def check(name, cond, info=""):
            rec.append((name, bool(cond), str(info)))

        err = ""
        try:
            section(check)
        except Exception:
            err = traceback.format_exc(limit=4)
        _RESULTS[section] = (rec, err)
    rec, err = _RESULTS[section]
    if i >= len(rec):
        pytest.fail(f"section stopped after {len(rec)} checks: {err}")
    name, ok, info = rec[i]
    assert ok, f"{name}: {info}"
    if i == expected - 1:
        assert len(rec) == expected, f"section recorded {len(rec)} checks"
        assert not err, err


def _run_submit_never_blocks_the_caller_even_while_a_job(check) -> None:
    """submit never blocks the caller, even while a job is running"""
    results: list = []
    dropped: list = []
    sperre = threading.Lock()


    def record(job, result, duration):
        with sperre:
            results.append((job.id, result))


    def record_drop(job):
        with sperre:
            dropped.append(job.id)


    q = lane("cothinker-lane-test-1", sleep=0.3, on_result=record,
            on_dropped=record_drop)
    check("lane starts", q.start() is True)
    t0 = time.monotonic()
    q.submit(Job(id="a", prompt="first"))
    check("job a running soon", wait_until(lambda: q.running()), "never started")
    t1 = time.monotonic()
    q.submit(Job(id="b", prompt="second"))     # goes into the waiting slot
    q.submit(Job(id="c", prompt="third"))      # replaces b -- newest wins
    t2 = time.monotonic()
    check("submit returns immediately even mid-run", (t2 - t1) < 0.05,
          f"{t2 - t1:.3f}s")
    check("replacing a waiting job never grows past 1", q.waiting_count() == 1)

    check("both results eventually arrive",
          wait_until(lambda: len(results) >= 2, timeout=5.0),
          f"only {len(results)} results: {results}")
    with sperre:
        ids = [rid for rid, _ in results]
    check("a ran, b was dropped, c ran", ids == ["a", "c"], str(ids))
    check("b was reported as dropped, never run", dropped == ["b"], str(dropped))

    stats = q.stats()
    check("max_waiting_seen never exceeded 1", stats["max_waiting_seen"] == 1,
          str(stats))
    check("exactly two jobs ran", stats["jobs_run"] == 2, str(stats))
    check("exactly one job dropped", stats["jobs_dropped"] == 1, str(stats))
    q.stop()
    check("nothing running after stop", q.running() is False)


def _run_a_lane_with_no_backlog_just_runs_jobs_one(check) -> None:
    """a lane with no backlog just runs jobs one after another"""
    results2: list = []
    q2 = lane("cothinker-lane-test-2", sleep=0.05,
             on_result=lambda job, r, d: results2.append(job.id))
    q2.start()
    q2.submit(Job(id="x", prompt="one"))
    check("x finishes", wait_until(lambda: "x" in results2), str(results2))
    q2.submit(Job(id="y", prompt="two"))
    check("y finishes", wait_until(lambda: "y" in results2), str(results2))
    check("no drops when nothing overlaps", q2.stats()["jobs_dropped"] == 0)
    q2.stop()


def _run_a_job_that_outlives_its_own_timeout_gets_its(check) -> None:
    """a job that outlives its own timeout gets its worker actively killed, not just abandoned"""
    results3: list = []
    q3 = lane("cothinker-lane-test-3", sleep=5.0,
             on_result=lambda job, r, d: results3.append(r))
    q3.start()
    q3.submit(Job(id="hang", prompt="too slow", timeout=0.3))
    check("the job reports a timeout, not success",
          wait_until(lambda: len(results3) == 1, timeout=3.0), str(results3))
    check("the error names a timeout", results3 and "timeout after"
          in str(results3[0].get("error")), str(results3))
    check("the stuck worker was actively stopped, not left running",
          wait_until(lambda: q3.worker.alive() is False, timeout=3.0),
          f"alive={q3.worker.alive()}")
    check("jobs_timed_out is counted", q3.stats()["jobs_timed_out"] == 1,
          str(q3.stats()))
    q3.stop()


def _run_a_lane_with_a_merge_function_folds_a_new(check) -> None:
    """a lane with a merge function folds a new job into the waiting one -- nothing dropped, waiting stays 1 (call lane, 2026-10-20)"""
    results4: list = []
    dropped4: list = []
    q4 = lane("cothinker-lane-test-4", sleep=0.3,
             on_result=lambda job, r, d: results4.append(job),
             on_dropped=lambda job: dropped4.append(job.id))
    q4.merge = lambda w, n: Job(id=f"{w.id}+{n.id}",
                                prompt=f"{w.prompt}|{n.prompt}")
    q4.start()
    q4.submit(Job(id="a", prompt="one"))
    wait_until(lambda: q4.running())
    q4.submit(Job(id="b", prompt="two"))
    q4.submit(Job(id="c", prompt="three"))
    q4.submit(Job(id="d", prompt="four"))
    check("waiting never exceeds 1 while merging", q4.waiting_count() == 1)
    check("both jobs ran",
          wait_until(lambda: len(results4) == 2, timeout=5.0), str(results4))
    check("the second job is the merge of all three waiting ones, in order",
          [j.id for j in results4] == ["a", "b+c+d"]
          and results4[1].prompt == "two|three|four",
          str([(j.id, j.prompt) for j in results4]))
    check("nothing was dropped", dropped4 == [] and q4.stats()["jobs_dropped"] == 0,
          str(q4.stats()))
    check("merges are counted", q4.stats()["jobs_merged"] == 2, str(q4.stats()))
    q4.stop()


def _run_a_job_still_waiting_when_the_lane_stops_is(check) -> None:
    """a job still waiting when the lane stops is reported as dropped (its caller must resolve the ack)"""
    dropped5: list = []
    q5 = lane("cothinker-lane-test-5", sleep=1.0,
             on_result=lambda job, r, d: None,
             on_dropped=lambda job: dropped5.append(job.id))
    q5.start()
    q5.submit(Job(id="run", prompt="one"))
    wait_until(lambda: q5.running())
    q5.submit(Job(id="left", prompt="two"))
    q5.stop(grace=0.5)
    check("the left-over waiting job went to on_dropped", "left" in dropped5,
          str(dropped5))


def _run_taskslot_1_running_1_waiting_newest_wins_no_workerclient(check) -> None:
    """TaskSlot: 1 running + 1 waiting, newest wins -- no WorkerClient underneath it"""
    slot_results: list = []
    slot_dropped: list = []
    slot_sperre = threading.Lock()


    def slot_run(job: Job) -> dict:
        """A plain `Callable[[Job], dict]` -- not a `WorkerClient.run`, the
        point of the extraction."""
        time.sleep(0.3)
        return {"ok": True, "final": job.id, "error": None}


    def slot_record(job, result, duration):
        with slot_sperre:
            slot_results.append((job.id, result))


    def slot_record_drop(job):
        with slot_sperre:
            slot_dropped.append(job.id)


    slot = TaskSlot(slot_run, on_result=slot_record,
                    on_dropped=slot_record_drop, log=lambda *a: None)
    slot.start()
    slot.submit(Job(id="a", prompt="first"))
    check("TaskSlot: job a running soon", wait_until(lambda: slot.running()),
          "never started")
    # the hub's loop-lag God Mode alert names the job
    # that was running when a lag spike happened -- TaskSlot.running_jobs() is a
    # process-wide classmethod (every live instance self-registers, no plumbing
    # needed at any of the ~7 call sites across the hub) so the hub's
    # single tick loop can ask it without each table handing its slots up.
    check("TaskSlot.running_jobs(): includes job a's id while it is running",
          "a" in TaskSlot.running_jobs(), TaskSlot.running_jobs())
    slot.submit(Job(id="b", prompt="second"))     # goes into the waiting slot
    slot.submit(Job(id="c", prompt="third"))      # replaces b -- newest wins
    check("TaskSlot: replacing a waiting job never grows past 1",
          slot.waiting_count() == 1)
    check("TaskSlot: both results eventually arrive",
          wait_until(lambda: len(slot_results) >= 2, timeout=5.0),
          f"only {len(slot_results)} results: {slot_results}")
    with slot_sperre:
        slot_ids = [rid for rid, _ in slot_results]
    check("TaskSlot: a ran, b was dropped, c ran", slot_ids == ["a", "c"],
          str(slot_ids))
    check("TaskSlot: the replaced job (b) was reported as dropped exactly "
         "once, the running job (a) was never reported as dropped",
          slot_dropped == ["b"], str(slot_dropped))
    check("TaskSlot: stats agree -- one drop, two runs",
          slot.stats()["jobs_dropped"] == 1 and slot.stats()["jobs_run"] == 2,
          str(slot.stats()))
    slot.stop()
    check("TaskSlot: nothing running after stop", slot.running() is False)
    check("TaskSlot.running_jobs(): empty once this slot is stopped and no "
          "other TaskSlot in this process happens to be running right now",
          "a" not in TaskSlot.running_jobs() and "c" not in TaskSlot.running_jobs(),
          TaskSlot.running_jobs())


def _run_order_latency_is_independent_of_batch_size_two_separate(check) -> None:
    """ORDER latency is independent of batch size -- two separate LaneQueues (two separate worker processes), (an ORDER waited behind a running batch turn in the serial single-worker queue)"""
    # The production shape: gateway/call_lane.py's call lane and the
    # batch path never share a WorkerClient, so an ORDER's own lane is never
    # occupied by however long the OTHER lane's job happens to take. Proven
    # here against REAL (fake) worker subprocesses, not mocked timing: the
    # "batch" lane's job duration is varied across three sizes, the "order"
    # lane's job duration stays fixed and low, and the order's own latency
    # must stay flat regardless.
    order_latencies: list = []
    for batch_sleep in (0.2, 1.0, 2.0):
        batch_results: list = []
        order_results: list = []
        batch_q = lane(f"cothinker-lane-batch-{batch_sleep}", sleep=batch_sleep,
                       on_result=lambda job, r, d: batch_results.append(d))
        order_q = lane(f"cothinker-lane-order-{batch_sleep}", sleep=0.1,
                      on_result=lambda job, r, d: order_results.append(d))
        batch_q.start()
        order_q.start()
        batch_q.submit(Job(id="batch", prompt="catch-up batch"))
        t0 = time.monotonic()
        order_q.submit(Job(id="order", prompt="Zuruf"))
        check(f"the order answers while a {batch_sleep}s batch is still running",
              wait_until(lambda: len(order_results) == 1, timeout=5.0),
              str(order_results))
        order_latencies.append(time.monotonic() - t0)
        wait_until(lambda: len(batch_results) == 1, timeout=5.0)
        batch_q.stop()
        order_q.stop()
    check("order latency stays low across every batch size tried",
          all(lat < 0.6 for lat in order_latencies), str(order_latencies))
    check("order latency does not grow with the batch's own duration -- the "
         "spread across a 10x larger batch (0.2s -> 2.0s) is nowhere near "
         "that factor",
          max(order_latencies) - min(order_latencies) < 0.4, str(order_latencies))


_NAMES_submit_never_blocks_the_caller_even_while_a_job = ['lane starts',
 'job a running soon',
 'submit returns immediately even mid-run',
 'replacing a waiting job never grows past 1',
 'both results eventually arrive',
 'a ran, b was dropped, c ran',
 'b was reported as dropped, never run',
 'max_waiting_seen never exceeded 1',
 'exactly two jobs ran',
 'exactly one job dropped',
 'nothing running after stop']


@pytest.mark.parametrize("i", _case_ids(_NAMES_submit_never_blocks_the_caller_even_while_a_job))
def test_submit_never_blocks_the_caller_even_while_a_job(i):
    _expect(_run_submit_never_blocks_the_caller_even_while_a_job, i, len(_NAMES_submit_never_blocks_the_caller_even_while_a_job))


_NAMES_a_lane_with_no_backlog_just_runs_jobs_one = ['x finishes', 'y finishes', 'no drops when nothing overlaps']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_lane_with_no_backlog_just_runs_jobs_one))
def test_a_lane_with_no_backlog_just_runs_jobs_one(i):
    _expect(_run_a_lane_with_no_backlog_just_runs_jobs_one, i, len(_NAMES_a_lane_with_no_backlog_just_runs_jobs_one))


_NAMES_a_job_that_outlives_its_own_timeout_gets_its = ['the job reports a timeout, not success',
 'the error names a timeout',
 'the stuck worker was actively stopped, not left running',
 'jobs_timed_out is counted']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_job_that_outlives_its_own_timeout_gets_its))
def test_a_job_that_outlives_its_own_timeout_gets_its(i):
    _expect(_run_a_job_that_outlives_its_own_timeout_gets_its, i, len(_NAMES_a_job_that_outlives_its_own_timeout_gets_its))


_NAMES_a_lane_with_a_merge_function_folds_a_new = ['waiting never exceeds 1 while merging',
 'both jobs ran',
 'the second job is the merge of all three waiting ones, in order',
 'nothing was dropped',
 'merges are counted']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_lane_with_a_merge_function_folds_a_new))
def test_a_lane_with_a_merge_function_folds_a_new(i):
    _expect(_run_a_lane_with_a_merge_function_folds_a_new, i, len(_NAMES_a_lane_with_a_merge_function_folds_a_new))


_NAMES_a_job_still_waiting_when_the_lane_stops_is = ['the left-over waiting job went to on_dropped']


@pytest.mark.parametrize("i", _case_ids(_NAMES_a_job_still_waiting_when_the_lane_stops_is))
def test_a_job_still_waiting_when_the_lane_stops_is(i):
    _expect(_run_a_job_still_waiting_when_the_lane_stops_is, i, len(_NAMES_a_job_still_waiting_when_the_lane_stops_is))


_NAMES_taskslot_1_running_1_waiting_newest_wins_no_workerclient = ['TaskSlot: job a running soon',
 "TaskSlot.running_jobs(): includes job a's id while it is running",
 'TaskSlot: replacing a waiting job never grows past 1',
 'TaskSlot: both results eventually arrive',
 'TaskSlot: a ran, b was dropped, c ran',
 'TaskSlot: the replaced job (b) was reported as dropped exactly once, the '
 'running job (a) was never reported as dropped',
 'TaskSlot: stats agree -- one drop, two runs',
 'TaskSlot: nothing running after stop',
 'TaskSlot.running_jobs(): empty once this slot is stopped and no other '
 'TaskSlot in this process happens to be running right now']


@pytest.mark.parametrize("i", _case_ids(_NAMES_taskslot_1_running_1_waiting_newest_wins_no_workerclient))
def test_taskslot_1_running_1_waiting_newest_wins_no_workerclient(i):
    _expect(_run_taskslot_1_running_1_waiting_newest_wins_no_workerclient, i, len(_NAMES_taskslot_1_running_1_waiting_newest_wins_no_workerclient))


_NAMES_order_latency_is_independent_of_batch_size_two_separate = ['the order answers while a 0.2s batch is still running',
 'the order answers while a 1.0s batch is still running',
 'the order answers while a 2.0s batch is still running',
 'order latency stays low across every batch size tried',
 "order latency does not grow with the batch's own duration -- the spread "
 'across a 10x larger batch (0.2s -> 2.0s) is nowhere near that factor']


@pytest.mark.parametrize("i", _case_ids(_NAMES_order_latency_is_independent_of_batch_size_two_separate))
def test_order_latency_is_independent_of_batch_size_two_separate(i):
    _expect(_run_order_latency_is_independent_of_batch_size_two_separate, i, len(_NAMES_order_latency_is_independent_of_batch_size_two_separate))


SECTIONS = [_run_submit_never_blocks_the_caller_even_while_a_job, _run_a_lane_with_no_backlog_just_runs_jobs_one, _run_a_job_that_outlives_its_own_timeout_gets_its, _run_a_lane_with_a_merge_function_folds_a_new, _run_a_job_still_waiting_when_the_lane_stops_is, _run_taskslot_1_running_1_waiting_newest_wins_no_workerclient, _run_order_latency_is_independent_of_batch_size_two_separate]
