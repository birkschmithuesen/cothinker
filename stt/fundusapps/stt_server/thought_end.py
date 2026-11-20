"""Thought end next to the audio: smart-turn on the recording machine.

WHY HERE: smart-turn listens to the audio, and the audio never leaves the
recording machine (docs/spec-2026-09-07.md). So the window is cut here,
from a short ring buffer in memory, handed to a smart-turn CHILD PROCESS
on this same machine over a local pipe, and what leaves this process on
the SSE stream is the finished number -- a `thought_end` event with `p`,
`ms` and the keys of the final it belongs to. Never the sound, never text.

WHY A CHILD PROCESS: smart-turn needs onnxruntime and faster_whisper's
mel frontend, which the STT server does not install, and a crash or hang
in a model library must not take the transcription with it. The worker
is gateway/smart_turn_worker.py, which speaks one JSON object per line;
the live request carries the window as base64 PCM16 (`pcm`).

WHY A READER THREAD AND NOT `selectors`: the recording PC runs Windows,
where `selectors` cannot wait on a pipe. A daemon thread reads lines into
a queue, and `queue.get(timeout=...)` gives the timeout.

THE AUDIO THREAD IS NEVER BLOCKED. `ThoughtEndTap.__call__` runs inside
`EventBus.publish()`, on whichever thread published the final. It does
arithmetic and one `put_nowait`; the worker call happens on the tap's own
thread, and a full queue drops the job and counts it.

OFF BY DEFAULT (`--thought-end off`). Nothing here runs unless asked for,
and it never cuts anything: the consumer only writes the number down
(plan docs/superpowers/plans/2026-09-26-thought-end-parallelspur.md).
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fundusapps.stt_server.events import ThoughtEndEvent

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
# pipecat's smart-turn VAD asks 0.2 s after speech stops -- the same
# pinned value as gateway/thought_end.STOP_S (plan 2026-09-25, D4).
STOP_S = 0.2
WINDOW_S = 8.0
# How much audio the ring keeps. A final arrives after the chunker's
# silence (0.5-0.7 s) plus transcription; 30 s covers that with room and
# costs 960 KB per recognizer.
RING_S = 30.0
REQUEST_TIMEOUT_S = 5.0
START_TIMEOUT_S = 60.0
# How long the tap waits for the stop time's audio to arrive in the ring.
END_WAIT_S = 1.0
QUEUE_MAX = 8
MAX_FAILURES = 3
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NUMBER_KEYS = ("id", "p", "ms", "cpu_ms", "load_s")
# stt/fundusapps/stt_server/thought_end.py -> the repository root. The
# recording PC clones the whole repository (docs/stt.md section 4.1), so
# the worker sits at the same relative place there.
DEFAULT_WORKER = (Path(__file__).resolve().parents[3] / "gateway"
                  / "smart_turn_worker.py")


class WorkerError(Exception):
    """The worker did not answer usably. Messages carry no text."""


class AudioRing:
    """The last `seconds` of one recognizer's 16 kHz PCM16, by clock position.

    Positions are AudioClock sample positions: `append(start, data)` is
    called with the offset `AudioClock.advance()` returned for exactly
    those samples. A start that does not continue the ring (a gap or an
    overlap) restarts it there -- the clock is the truth, and splicing
    two stretches together would hand the model audio that never
    followed each other.
    """

    def __init__(self, seconds: float = RING_S,
                 sample_rate: int = SAMPLE_RATE) -> None:
        self.capacity = int(round(float(seconds) * sample_rate))
        self._buf = bytearray()
        self._end = 0
        self._lock = threading.Lock()

    @property
    def end(self) -> int:
        with self._lock:
            return self._end

    def append(self, start_sample: int, data) -> None:
        data = bytes(data)
        n = len(data) // SAMPLE_WIDTH
        with self._lock:
            if int(start_sample) != self._end:
                self._buf = bytearray()
            self._buf += data[:n * SAMPLE_WIDTH]
            self._end = int(start_sample) + n
            excess = len(self._buf) // SAMPLE_WIDTH - self.capacity
            if excess > 0:
                del self._buf[:excess * SAMPLE_WIDTH]

    def read(self, first: int, count: int) -> bytes:
        with self._lock:
            begin = self._end - len(self._buf) // SAMPLE_WIDTH
            lo = max(int(first), begin)
            hi = min(int(first) + int(count), self._end)
            if hi <= lo:
                return b""
            return bytes(self._buf[(lo - begin) * SAMPLE_WIDTH:
                                   (hi - begin) * SAMPLE_WIDTH])


def window_samples(t_start, t_end, *, stop_s: float = STOP_S,
                   window_s: float = WINDOW_S,
                   sample_rate: int = SAMPLE_RATE):
    """(first sample, count) of the audio one decision sees.

    The turn's own audio up to `stop_s` after it ended, at most the last
    `window_s` of it -- the anchored variant the bench measured
    (gateway/thought_end.audio_window, gateway/smart_turn_worker.
    frame_range), on sample positions instead of a file.
    """
    end = max(0, int(round((float(t_end) + stop_s) * sample_rate)))
    begin = max(0, int(round(float(t_start) * sample_rate)))
    begin = min(begin, end)
    window = int(round(window_s * sample_rate))
    if end - begin > window:
        begin = end - window
    return begin, end - begin


def clean_reply(obj) -> dict:
    """One reply line, stripped down to numbers and one token.

    The same allow-list as gateway/thought_end.clean_reply, repeated here
    because this package must not import the gateway: a model library's
    exception message quotes its input, so nothing but numbers and a
    short token is ever passed on.
    """
    if not isinstance(obj, dict):
        raise WorkerError("a reply line is not a JSON object")
    out = {}
    for key in NUMBER_KEYS:
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            if not math.isfinite(value):
                continue
        except OverflowError:
            continue
        out[key] = value
    if "ready" in obj:
        out["ready"] = bool(obj.get("ready"))
    if "error" in obj:
        token = obj.get("error")
        out["error"] = (token if isinstance(token, str)
                        and TOKEN_RE.match(token) else "bad_error")
    return out


class SmartTurnProcess:
    """The smart-turn worker as a child process, spoken to in JSON lines."""

    def __init__(self, argv, *, timeout_s: float = REQUEST_TIMEOUT_S,
                 start_timeout_s: float = START_TIMEOUT_S) -> None:
        self.argv = [str(a) for a in argv]
        self.timeout_s = float(timeout_s)
        self.start_timeout_s = float(start_timeout_s)
        self.proc = None
        self.load_s = 0.0
        self._lines: "queue.Queue" = queue.Queue()

    def start(self) -> None:
        try:
            # bufsize=-1 (the platform default, buffered): a live request
            # line runs to roughly 350 KB of base64 PCM, and raw FileIO
            # (bufsize=0) is allowed to return from write() having written
            # only part of that -- a short write silently truncating the
            # line for the worker. flush() below still forces the buffer
            # out before ask() waits on a reply.
            self.proc = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=-1)
        except OSError as exc:
            raise WorkerError(
                f"the worker did not start ({type(exc).__name__})") from None
        threading.Thread(target=self._read, args=(self.proc.stdout,
                                                  self._lines),
                         daemon=True, name="smart-turn-reader").start()
        reply = self._next(self.start_timeout_s)
        if not reply.get("ready"):
            raise WorkerError("the worker sent no ready line")
        self.load_s = float(reply.get("load_s") or 0.0)

    @staticmethod
    def _read(stream, lines) -> None:
        try:
            for raw in iter(stream.readline, b""):
                lines.put(raw)
        except (OSError, ValueError):
            pass
        lines.put(None)

    def _next(self, budget: float) -> dict:
        deadline = time.monotonic() + float(budget)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError(f"no answer within {float(budget):.1f} s")
            try:
                raw = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise WorkerError(
                    f"no answer within {float(budget):.1f} s") from None
            if raw is None:
                raise WorkerError("the worker closed its output")
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                raise WorkerError(
                    f"a reply of {len(raw)} bytes is not JSON") from None
            return clean_reply(obj)

    def ask(self, req_id: int, pcm: bytes) -> dict:
        if self.proc is None:
            raise WorkerError("the worker is not running")
        line = json.dumps({"id": int(req_id),
                           "pcm": base64.b64encode(pcm).decode("ascii")})
        try:
            self.proc.stdin.write((line + "\n").encode("ascii"))
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise WorkerError(
                f"cannot write to the worker ({type(exc).__name__})") from None
        # One deadline for the whole call, not one per reply: a worker
        # that keeps answering -- just always to the wrong id -- must not
        # be able to keep resetting the clock forever by replying often
        # enough. Each _next() gets what is left of the original budget.
        deadline = time.monotonic() + self.timeout_s
        while True:
            reply = self._next(deadline - time.monotonic())
            # A late answer to an earlier request that timed out is
            # skipped: handing it to this request would put one turn's
            # probability on another.
            if reply.get("id") == int(req_id) or reply.get("id") == -1:
                return reply

    def close(self) -> None:
        if self.proc is None:
            return
        proc = self.proc
        # Ask first, close the pipes last: on Windows, closing stdin/
        # stdout while the reader thread is blocked in a synchronous read
        # can hang that thread forever. terminate()/kill() end the
        # process (and its read) first; only then are the streams closed,
        # each guarded on its own so one failing close never skips the
        # other or leaves the process reference behind.
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                pass
        except OSError:
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        self.proc = None


def _usable_span(t_start, t_end) -> bool:
    for value in (t_start, t_end):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            if not math.isfinite(value) or value < 0:
                return False
        except (OverflowError, ValueError):
            return False
    return t_end >= t_start


class ThoughtEndTap:
    """Sits on the EventBus after the TimestampEnricher; scores finals."""

    def __init__(self, publish, client_factory, *,
                 queue_max: int = QUEUE_MAX, end_wait_s: float = END_WAIT_S,
                 max_failures: int = MAX_FAILURES) -> None:
        self._publish = publish
        self._factory = client_factory
        self._end_wait_s = float(end_wait_s)
        self._max_failures = int(max_failures)
        self._state: dict = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue(maxsize=int(queue_max))
        self._stop = threading.Event()
        self._thread = None
        self._client = None
        self._counter = 0
        self.drops = 0
        self.failures = 0

    def register(self, recognizer_id, clock, ring) -> None:
        with self._lock:
            self._state[str(recognizer_id)] = (clock, ring)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True,
                                            name="thought-end")
            self._thread.start()

    def __call__(self, event) -> None:
        """On the publishing thread: arithmetic and put_nowait, nothing else."""
        try:
            if getattr(event, "type", None) != "final":
                return
            t_start = getattr(event, "t_start", None)
            t_end = getattr(event, "t_end", None)
            if not _usable_span(t_start, t_end):
                return
            rid = str(getattr(event, "recognizer_id", ""))
            with self._lock:
                if rid not in self._state:
                    # speechmatics-rt: recognizer_id is the speaker label,
                    # the audio channel rides in `channel`.
                    rid = str(getattr(event, "channel", ""))
                if rid not in self._state:
                    return
            first, count = window_samples(t_start, t_end)
            turn = getattr(event, "turn_id", None)
            job = {"recognizer_id": rid,
                   "turn_id": turn if isinstance(turn, str) else None,
                   "t_end": round(float(t_end), 3),
                   "first": first, "count": count}
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                self.drops += 1
        except Exception as exc:          # noqa: BLE001 -- the audio thread
            logging.warning("thought end: final not queued (%s)",
                            type(exc).__name__)

    def _work(self) -> None:
        # Warm up the child process before the first job arrives: starting
        # it (onnxruntime import, model load) takes up to START_TIMEOUT_S
        # (60 s), and a meeting's first thought_end events must not wait
        # on that -- the start happens here, once, up front, instead of
        # lazily inside the first _handle(). Any failure is swallowed and
        # counted the same way _ensure_client() already does; a broad
        # guard on top so the warm-up itself can never take this thread
        # down before it reaches the loop.
        try:
            self._ensure_client()
        except Exception as exc:          # noqa: BLE001 -- must not kill the thread
            logging.warning("thought end: warm-up failed (%s)",
                            type(exc).__name__)
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle(job)
            except Exception as exc:      # noqa: BLE001
                logging.warning("thought end skipped (%s)",
                                type(exc).__name__)

    def _handle(self, job) -> None:
        with self._lock:
            clock, ring = self._state[job["recognizer_id"]]
        want_end = job["first"] + job["count"]
        deadline = time.monotonic() + self._end_wait_s
        while (ring.end < want_end and time.monotonic() < deadline
               and not self._stop.is_set()):
            time.sleep(0.02)
        data = ring.read(job["first"], job["count"])
        p = ms = None
        error = None
        if not data:
            error = "no_audio"
        else:
            client = self._ensure_client()
            if client is None:
                error = "worker_down"
            else:
                self._counter += 1
                try:
                    reply = client.ask(self._counter, data)
                except WorkerError:
                    self._drop_client()
                    error = "worker"
                else:
                    if "p" in reply:
                        p = float(reply["p"])
                        ms = float(reply.get("ms") or 0.0)
                        # A usable answer clears the streak: `failures`
                        # counts CONSECUTIVE trouble, not a lifetime
                        # total -- three timeouts on one bad minute must
                        # not disable the lane for the rest of a
                        # four-hour meeting.
                        self.failures = 0
                    else:
                        error = reply.get("error") or "no_p"
        self._publish(ThoughtEndEvent(
            recognizer_id=job["recognizer_id"], turn_id=job["turn_id"],
            t_end=job["t_end"], p=p, ms=ms, error=error,
            audio_s=round(len(data) / SAMPLE_WIDTH / SAMPLE_RATE, 3),
            timestamp=time.time()))

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if self.failures >= self._max_failures:
            return None
        client = self._factory()
        try:
            client.start()
        except (WorkerError, OSError) as exc:
            self.failures += 1
            logging.warning("thought end: smart-turn worker did not start "
                            "(%s, failure %d of %d)", type(exc).__name__,
                            self.failures, self._max_failures)
            try:
                client.close()
            except Exception:             # noqa: BLE001
                pass
            return None
        self._client = client
        return client

    def _drop_client(self) -> None:
        self.failures += 1
        client, self._client = self._client, None
        try:
            if client is not None:
                client.close()
        except Exception:                 # noqa: BLE001
            pass

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # Take a local copy first: the tap thread may still be running
        # (join() above only waits up to 2 s) and could concurrently call
        # _drop_client(), which sets self._client to None. Reading
        # self._client twice -- once for the None check, once for close()
        # -- would race against that and raise AttributeError on None.
        client, self._client = self._client, None
        if client is not None:
            client.close()


def chain_enrich(*hooks):
    """One bus hook that runs `hooks` in order; a broken one costs itself.

    The tap must run AFTER the TimestampEnricher: it reads the t_start/
    t_end the enricher has just filled in.
    """
    def run(event) -> None:
        for hook in hooks:
            try:
                hook(event)
            except Exception as exc:      # noqa: BLE001
                logging.warning("event hook failed (%s)", type(exc).__name__)
    return run


def build_tap(mode, publish, environ=os.environ) -> Optional[ThoughtEndTap]:
    """The tap for `--thought-end`, or None.

    Paths come from the environment, like the bench's
    (docs/bench-thought-end.md): COTHINKER_SMART_TURN_PYTHON (an
    interpreter with numpy, onnxruntime and faster_whisper),
    COTHINKER_SMART_TURN_MODEL (the .onnx file), and optionally
    COTHINKER_SMART_TURN_WORKER (default: gateway/smart_turn_worker.py of
    this checkout). Their values never live in the repository.
    """
    if mode != "smart_turn":
        return None
    python = environ.get("COTHINKER_SMART_TURN_PYTHON", "")
    model = environ.get("COTHINKER_SMART_TURN_MODEL", "")
    missing = [n for n, v in (("COTHINKER_SMART_TURN_PYTHON", python),
                              ("COTHINKER_SMART_TURN_MODEL", model)) if not v]
    if missing:
        logging.error("thought end off: %s not set", ", ".join(missing))
        return None
    worker = environ.get("COTHINKER_SMART_TURN_WORKER", "") or str(DEFAULT_WORKER)
    argv = [python, worker, "--model", model, "--threads", "1"]
    tap = ThoughtEndTap(publish, lambda: SmartTurnProcess(argv))
    tap.start()
    logging.info("thought end on: smart-turn next to the audio, "
                 "numbers only on the event stream")
    return tap
