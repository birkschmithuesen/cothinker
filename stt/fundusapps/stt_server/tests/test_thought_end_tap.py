"""Thought end next to the audio: ring, window, reply filter, worker, tap.

No model is ever started. The worker is a standard-library script
written into pytest's tmp dir; the tap is driven with an in-process fake
client. Every number and every byte of audio is synthetic.
"""
import json
import queue
import sys
import threading
import time

import pytest

from fundusapps.stt_server.audio_clock import AudioClock
from fundusapps.stt_server.events import (EventBus, ThoughtEndEvent,
                                          TranscriptionEvent)
from fundusapps.stt_server.thought_end import (
    AudioRing, SmartTurnProcess, ThoughtEndTap, WorkerError, build_tap,
    chain_enrich, clean_reply, window_samples)

KANARI = "KANARIENVOGEL"


def pcm(n, value=1):
    return int(value).to_bytes(2, "little", signed=True) * int(n)


# ------------------------------------------------------------- the ring
def test_ring_returns_exactly_the_requested_samples():
    ring = AudioRing(seconds=1.0)
    ring.append(0, pcm(8000, 1))
    ring.append(8000, pcm(8000, 2))
    assert ring.end == 16000
    got = ring.read(7999, 2)
    assert got == pcm(1, 1) + pcm(1, 2)


def test_ring_forgets_what_is_older_than_its_capacity():
    ring = AudioRing(seconds=1.0)
    for i in range(3):
        ring.append(i * 16000, pcm(16000, i + 1))
    assert ring.read(0, 16000) == b""
    assert ring.read(32000, 16000) == pcm(16000, 3)
    # A request reaching back past the oldest sample gets what is there:
    # the ring holds 32000..48000, so 20000..40000 yields 32000..40000.
    assert len(ring.read(20000, 20000)) == 8000 * 2


def test_ring_restarts_on_a_gap_in_the_clock():
    ring = AudioRing(seconds=1.0)
    ring.append(0, pcm(100, 1))
    ring.append(500, pcm(100, 2))        # 400 samples were never seen
    assert ring.read(0, 100) == b""
    assert ring.read(500, 100) == pcm(100, 2)


def test_ring_read_after_the_end_is_empty():
    ring = AudioRing(seconds=1.0)
    ring.append(0, pcm(100))
    assert ring.read(100, 50) == b""
    assert ring.read(0, 0) == b""


# ------------------------------------------------------------ the window
def test_window_is_the_turn_plus_the_stop_time():
    assert window_samples(1.0, 2.0) == (16000, int(round(1.2 * 16000)))


def test_window_is_capped_at_eight_seconds_ending_at_the_stop():
    first, count = window_samples(0.0, 20.0)
    assert count == 8 * 16000
    assert first + count == int(round(20.2 * 16000))


# ------------------------------------------------------- the reply filter
def test_clean_reply_keeps_numbers_and_a_token_only():
    out = clean_reply({"id": 3, "p": 0.5, "ms": 4.0, "note": KANARI,
                       "error": f"kaputt {KANARI}"})
    assert out == {"id": 3, "p": 0.5, "ms": 4.0, "error": "bad_error"}
    assert clean_reply({"id": True, "p": "0.5"}) == {}
    with pytest.raises(WorkerError):
        clean_reply(["not", "a", "dict"])


def test_clean_reply_drops_non_finite_numbers():
    # json.loads happily parses NaN/Infinity (they are not valid JSON, but
    # the stdlib accepts them by default) -- clean_reply must not let one
    # of those pass through onto the SSE stream, where they are not valid
    # JSON either.
    out = clean_reply({"id": 1, "p": float("nan"), "ms": float("inf"),
                       "cpu_ms": float("-inf"), "load_s": 2.0})
    assert out == {"id": 1, "load_s": 2.0}
    # A huge int that overflows float() must be dropped, not raise.
    assert clean_reply({"ms": 10 ** 400}) == {}


# -------------------------------------------------------- the subprocess
FAKE = r'''
import base64, json, signal, sys, time
mode = sys.argv[1]
if mode == "hang":
    # Ignore SIGTERM so close()'s terminate() cannot end this process --
    # only kill() (SIGKILL, which cannot be ignored) can. POSIX only;
    # the test using this mode is skipped on Windows.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write(json.dumps({"ready": True, "load_s": 0.1}) + "\n")
sys.stdout.flush()
for line in sys.stdin:
    req = json.loads(line)
    n = len(base64.b64decode(req["pcm"]))
    if mode == "slow":
        time.sleep(1.0)
    if mode == "hang":
        # Stuck long enough that only kill() ends it -- close() must not
        # wait for this to finish on its own.
        time.sleep(3600)
    if mode == "stale":
        sys.stdout.write(json.dumps({"id": 999, "p": 0.9}) + "\n")
    if mode == "neverright":
        # Answers, often, but never with the id that was asked for -- a
        # per-reply timeout reset would let this loop forever.
        for _ in range(200):
            sys.stdout.write(json.dumps({"id": 999, "p": 0.1}) + "\n")
            sys.stdout.flush()
            time.sleep(0.01)
        continue
    sys.stdout.write(json.dumps({"id": req["id"], "p": n / 1e6, "ms": 1.5,
                                 "cpu_ms": 1.0, "note": "%s"}) + "\n")
    sys.stdout.flush()
''' % KANARI


@pytest.fixture
def fake_worker(tmp_path):
    path = tmp_path / "fake_smart_turn.py"
    path.write_text(FAKE, encoding="utf-8")
    return path


def test_process_round_trip(fake_worker):
    proc = SmartTurnProcess([sys.executable, str(fake_worker), "ok"])
    proc.start()
    try:
        reply = proc.ask(1, pcm(1000))
    finally:
        proc.close()
    assert reply == {"id": 1, "p": 0.002, "ms": 1.5, "cpu_ms": 1.0}


def test_process_skips_a_late_answer_to_an_older_request(fake_worker):
    proc = SmartTurnProcess([sys.executable, str(fake_worker), "stale"])
    proc.start()
    try:
        assert proc.ask(5, pcm(10))["id"] == 5
    finally:
        proc.close()


def test_process_times_out_instead_of_hanging(fake_worker):
    proc = SmartTurnProcess([sys.executable, str(fake_worker), "slow"],
                            timeout_s=0.2)
    proc.start()
    try:
        with pytest.raises(WorkerError):
            proc.ask(1, pcm(10))
    finally:
        proc.close()


def test_process_that_cannot_start_raises_worker_error(tmp_path):
    proc = SmartTurnProcess([str(tmp_path / "does-not-exist")])
    with pytest.raises(WorkerError):
        proc.start()


def test_process_gives_up_within_its_deadline_when_never_answered(fake_worker):
    # A worker that keeps replying -- always with the wrong id -- must not
    # be able to keep resetting a per-reply timeout forever. ask() has to
    # use ONE overall deadline for the whole call.
    proc = SmartTurnProcess(
        [sys.executable, str(fake_worker), "neverright"], timeout_s=0.3)
    proc.start()
    try:
        start = time.monotonic()
        with pytest.raises(WorkerError):
            proc.ask(1, pcm(10))
        elapsed = time.monotonic() - start
    finally:
        proc.close()
    # Generous margin over the 0.3 s deadline; the fake keeps sending
    # wrong-id replies for 2 s, so a pre-fix per-reply reset would run
    # well past 1 s before the fake's reply stream runs dry.
    assert elapsed < 1.0


@pytest.mark.skipif(sys.platform == "win32",
                    reason="ignores SIGTERM to force the kill() path; "
                          "POSIX only")
def test_close_returns_quickly_when_the_worker_is_stuck(fake_worker):
    # The pipe-close-before-terminate ordering hazard this fix addresses
    # is itself Windows-only (a reader thread blocked in a synchronous
    # pipe read hangs when the pipe is closed under it) and cannot be
    # reproduced on Linux, where closing a pipe never blocks a reader.
    # What this test CAN and does verify on any POSIX box: a worker that
    # ignores SIGTERM forces close() past terminate()'s wait(timeout=5)
    # into the kill() + second wait() branch, and close() still returns
    # in bounded time with the process actually reaped -- not the
    # ordering itself, but that the kill/reap fallback works.
    proc = SmartTurnProcess(
        [sys.executable, str(fake_worker), "hang"], timeout_s=0.3)
    proc.start()
    with pytest.raises(WorkerError):
        proc.ask(1, pcm(10))
    child = proc.proc
    start = time.monotonic()
    proc.close()
    elapsed = time.monotonic() - start
    # terminate() is ignored, so this must run past its 5 s wait() into
    # kill() + a second wait(); bound it well above 5 s but far below
    # "hung forever".
    assert elapsed < 12.0
    assert child.poll() is not None


# --------------------------------------------------------------- the tap
class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.seen = []
        self.closed = False

    def start(self):
        pass

    def ask(self, req_id, data):
        self.seen.append(len(data))
        if self.fail:
            raise WorkerError("down")
        return {"id": req_id, "p": 0.75, "ms": 12.0}

    def close(self):
        self.closed = True


class ScriptedClient:
    """A client whose ask() succeeds or fails per call, by a fixed script.

    Calls past the end of the script succeed -- convenient for "then it
    keeps working".
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.starts = 0
        self.closed = False

    def start(self):
        self.starts += 1

    def ask(self, req_id, data):
        ok = self.script[self.calls] if self.calls < len(self.script) else True
        self.calls += 1
        if not ok:
            raise WorkerError("down")
        return {"id": req_id, "p": 0.75, "ms": 12.0}

    def close(self):
        self.closed = True


def final(t_start=1.0, t_end=2.0, rid="0", turn="T1", text=KANARI):
    return TranscriptionEvent(recognizer_id=rid, type="final", text=text,
                              timestamp=time.time(), backend="whisper",
                              turn_id=turn, t_start=t_start, t_end=t_end)


def tap_with(client, published):
    tap = ThoughtEndTap(published.append, lambda: client, end_wait_s=0.05)
    ring = AudioRing(seconds=30.0)
    ring.append(0, pcm(16000 * 5))
    tap.register("0", AudioClock(), ring)
    tap.start()
    return tap


def wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_start_warms_up_the_client_before_any_final_is_fed():
    # The child process can take up to START_TIMEOUT_S (60 s) to come up
    # (onnxruntime import, model load). start() must trigger that warm-up
    # right away, on the tap's own thread, so the first real final of a
    # meeting does not queue behind the worker's cold start.
    client = ScriptedClient([])
    tap = ThoughtEndTap(lambda ev: None, lambda: client, end_wait_s=0.05)
    ring = AudioRing(seconds=30.0)
    ring.append(0, pcm(16000 * 5))
    tap.register("0", AudioClock(), ring)
    tap.start()
    try:
        assert wait_for(lambda: client.starts == 1)
    finally:
        tap.close()
    # No final was ever fed; the one start() call is the warm-up alone.
    assert client.calls == 0


def test_a_final_with_a_span_becomes_one_thought_end_event():
    published, client = [], FakeClient()
    tap = tap_with(client, published)
    try:
        tap(final())
        assert wait_for(lambda: published)
    finally:
        tap.close()
    ev = published[0]
    assert isinstance(ev, ThoughtEndEvent)
    assert (ev.type, ev.recognizer_id, ev.turn_id, ev.t_end, ev.p, ev.ms,
            ev.error) == ("thought_end", "0", "T1", 2.0, 0.75, 12.0, None)
    assert client.seen == [int(round(1.2 * 16000)) * 2]
    assert ev.audio_s == 1.2
    text = json.dumps(ev.to_dict())
    assert KANARI not in text and "text" not in ev.to_dict()


def test_the_tap_ignores_what_it_cannot_score():
    published, client = [], FakeClient()
    tap = tap_with(client, published)
    try:
        tap(final(t_start=None, t_end=None))
        tap(final(rid="unknown"))
        tap(TranscriptionEvent(recognizer_id="0", type="partial", text="x",
                               timestamp=0.0, backend="whisper"))
        tap(object())
        tap(final(t_start=float("nan")))
        time.sleep(0.2)
    finally:
        tap.close()
    assert published == [] and client.seen == []


def test_a_full_queue_drops_and_counts_instead_of_blocking():
    published = []
    tap = ThoughtEndTap(published.append, FakeClient, queue_max=1)
    ring = AudioRing()
    tap.register("0", AudioClock(), ring)
    # Not started: nothing drains the queue.
    tap(final())
    tap(final())
    assert tap.drops == 1


def test_a_failing_worker_costs_the_number_not_the_event():
    published, client = [], FakeClient(fail=True)
    tap = tap_with(client, published)
    try:
        tap(final())
        assert wait_for(lambda: published)
    finally:
        tap.close()
    assert published[0].p is None and published[0].error == "worker"
    assert tap.failures == 1 and client.closed


def test_consecutive_failures_reset_so_the_lane_does_not_lock_up():
    # Four failures spread across eight calls, but never two in a row.
    # `failures` counts a lifetime total in the pre-fix code: the fourth
    # failure would find `failures >= MAX_FAILURES` (3) and short-circuit
    # to "worker_down" without ever asking the client again -- disabling
    # a four-hour meeting's lane over three timeouts spread through it.
    # With a reset on every successful answer, no failure here is ever
    # the third IN A ROW, so the client keeps getting asked.
    published = []
    script = [False, True, False, True, False, True, False, True]
    client = ScriptedClient(script)
    tap = ThoughtEndTap(published.append, lambda: client, end_wait_s=0.05)
    ring = AudioRing(seconds=30.0)
    ring.append(0, pcm(16000 * 5))
    tap.register("0", AudioClock(), ring)
    tap.start()
    try:
        for i in range(len(script)):
            tap(final(turn=f"T{i}"))
            assert wait_for(lambda: len(published) == i + 1)
    finally:
        tap.close()
    errors = [ev.error for ev in published]
    assert errors == ["worker", None, "worker", None, "worker", None,
                      "worker", None]
    assert "worker_down" not in errors
    assert tap.failures == 0


def test_chain_runs_in_order_and_survives_a_broken_hook():
    calls = []

    def broken(ev):
        raise RuntimeError("boom")

    hook = chain_enrich(lambda ev: calls.append("a"), broken,
                        lambda ev: calls.append("b"))
    hook(object())
    assert calls == ["a", "b"]


def test_build_tap_is_off_by_default_and_without_configuration():
    assert build_tap("off", lambda ev: None, environ={}) is None
    assert build_tap("smart_turn", lambda ev: None, environ={}) is None


def test_the_event_goes_through_the_bus_unchanged():
    bus = EventBus()
    sub = bus.subscribe()
    ev = ThoughtEndEvent(recognizer_id="0", turn_id=None, t_end=1.0, p=0.5,
                         ms=1.0, error=None, audio_s=1.2, timestamp=0.0)
    bus.publish(ev)
    got = sub.get_nowait()
    assert got.to_dict()["type"] == "thought_end"
    json.dumps(got.to_dict())
