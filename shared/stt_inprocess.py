#!/usr/bin/env python3
# CoThinker — STT API: in-process transport
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The in-process transport of the STT API (`docs/architecture/target.md`
§2.1, ADR 0003/0004): the caller pushes 16 kHz PCM, the Speechmatics EU
engine runs in the caller's process, and `TranscriptEvent`s come out.

Connection lifecycle: connect in the background; buffer PCM -- with the time
it was received -- while no stream is live (bounded, oldest dropped first);
flush it in order before any live chunk; reconnect with backoff after an
engine error or an unexpected end (the same provider, never another
engine); a send that stalls times out and its chunk is re-sent on the new
stream. Every stream has its own `AudioClock` and its own
`shared.utterance.UtteranceCollector`.

`InprocessSession` is a synchronous facade over one asyncio loop on a
daemon thread: `send_pcm` never blocks and keeps call order, `events()` is
a blocking iterator.

`engine_factory(speakers)` returns an object with the surface of
`shared.speechmatics.SpeechmaticsStream`: `async start()`,
`async send_pcm(chunk)`, `events()` (async iterator of `SttEvent`, ends with
the stream) and `async close()`.
"""
from __future__ import annotations

import asyncio
import bisect
import logging
import queue
import threading
import time
from collections import deque
from typing import Callable, Iterator, List, Optional

from shared.speechmatics import BYTES_PER_S
from shared.stt import SttConfig, TranscriptEvent, run_stages
from shared.utterance import UtteranceCollector

BACKEND = "speechmatics-rt"
RETRY_S = (1.0, 2.0, 5.0)
BUFFER_S = 10.0
CLOSE_TIMEOUT_S = 10.0
SEND_TIMEOUT_S = 1.5

_log = logging.getLogger(__name__)


def wall_ms() -> float:
    return time.time() * 1000.0


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


class AudioClock:
    """Stream audio seconds -> wall ms at which the caller received that audio."""

    def __init__(self) -> None:
        self._ends: list[float] = []
        self._walls: list[float] = []
        self.audio_s = 0.0

    def add(self, n_bytes: int, wall_ms: float) -> None:
        if n_bytes <= 0:
            return
        self.audio_s += n_bytes / BYTES_PER_S
        self._ends.append(self.audio_s)
        self._walls.append(wall_ms)

    def wall_at(self, audio_s) -> float | None:
        if not _number(audio_s):
            return None
        i = bisect.bisect_left(self._ends, audio_s - 1e-6)
        if i >= len(self._ends):
            return None
        return self._walls[i]


class _Stream:
    def __init__(self, no: int, stream) -> None:
        self.no = no
        self.stream = stream
        self.clock = AudioClock()
        self.task: Optional[asyncio.Task] = None
        self.dead = False


class _StreamSession:
    """One engine connection kept alive: buffer, flush, reconnect."""

    def __init__(self, factory: Callable, on_event: Callable, on_status: Callable[[str], None], *,
                 speakers=None, buffer_s: float = BUFFER_S, retry_s=RETRY_S,
                 wall: Callable[[], float] = wall_ms, send_timeout_s: float = SEND_TIMEOUT_S,
                 log: Callable[[str], None] = _log.warning,
                 on_stream_end: Optional[Callable[[int], None]] = None) -> None:
        self._factory = factory
        self._on_event = on_event
        self._on_stream_end = on_stream_end
        self._on_status = on_status
        self._speakers = speakers
        self._retry = tuple(retry_s) or (1.0,)
        self._wall = wall
        self._send_timeout_s = send_timeout_s
        self._log = log
        self._max_buffer = int(buffer_s * BYTES_PER_S)
        self._buffer: deque = deque()
        self._buffer_bytes = 0
        self.dropped_bytes = 0
        self._live: Optional[_Stream] = None
        self._old: List[_Stream] = []
        self._readers: set = set()
        self._count = 0
        self._closing = False
        self._opener: Optional[asyncio.Task] = None
        self._bg: set = set()                # background close tasks, kept so close() can await them
        self.state = "connecting"

    @property
    def streams_started(self) -> int:
        return self._count

    def open(self) -> None:
        """Start connecting in the background. Idempotent."""
        self._closing = False
        if self._live is not None and not self._live.dead:
            return
        if self._opener is not None and not self._opener.done():
            return
        self._opener = asyncio.get_running_loop().create_task(self._connect_loop())

    async def send_pcm(self, chunk: bytes, wall: Optional[float] = None) -> None:
        if not chunk:
            return
        wall = self._wall() if wall is None else wall
        live = self._live
        if live is None or live.dead:
            self._buffer_add(chunk, wall)
            return
        live.clock.add(len(chunk), wall)
        try:
            await asyncio.wait_for(live.stream.send_pcm(chunk), self._send_timeout_s)
        except asyncio.TimeoutError:
            # a stalled socket (full write buffer) must not freeze the caller: the chunk may be
            # half-sent, so it is re-sent on the new stream (a duplicate beats a hole). The
            # reader's close-driven reconnect may never fire on a stalled socket, so reconnect here.
            live.dead = True
            self._buffer_add(chunk, wall)
            self._log(f"stt: stream {live.no} send stalled, reconnecting")
            if self._live is live and not self._closing:
                self._live = None
                self._set_state("degraded")
                self._retire(live)
                self.open()
            else:
                self._retire(live)
        except Exception:                    # noqa: BLE001 -- the reader sees the close and reconnects
            live.dead = True
            self._buffer_add(chunk, wall)

    async def close(self) -> None:
        self._closing = True
        if self._opener is not None and not self._opener.done():
            self._opener.cancel()
            await asyncio.wait([self._opener])
        streams = [s for s in [self._live] + self._old if s is not None]
        self._live, self._old = None, []
        for s in streams:
            await self._close_stream(s)
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
        # the readers deliver the last events the closed streams still had
        readers = [t for t in self._readers if not t.done()]
        if readers:
            _, pending = await asyncio.wait(readers, timeout=CLOSE_TIMEOUT_S)
            for t in pending:
                t.cancel()
        self._set_state("closed")

    # --- internal ---
    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        try:
            self._on_status(state)
        except Exception as e:               # noqa: BLE001
            self._log(f"stt: status handler failed: {type(e).__name__}")

    def _buffer_add(self, chunk: bytes, wall: float) -> None:
        self._buffer.append((chunk, wall))
        self._buffer_bytes += len(chunk)
        while self._buffer_bytes > self._max_buffer and self._buffer:
            old, _ = self._buffer.popleft()
            self._buffer_bytes -= len(old)
            self.dropped_bytes += len(old)

    async def _connect_loop(self) -> None:
        attempt = 0
        while not self._closing:
            s = await self._start(self._speakers)
            if s is not None:
                try:
                    if await self._flush(s) and not s.dead:         # a stream that died while flushing is no stream
                        self._live = s                              # only now: nothing can overtake the buffer
                        self._set_state("ok")
                        return
                except asyncio.CancelledError:
                    self._retire(s)
                    raise
                self._retire(s)
            self._set_state("degraded")
            await asyncio.sleep(self._retry[min(attempt, len(self._retry) - 1)])
            attempt += 1

    async def _start(self, speakers) -> Optional[_Stream]:
        self._count += 1
        no = self._count
        stream = None
        try:
            stream = self._factory(speakers)
            await stream.start()
        except asyncio.CancelledError:
            if stream is not None:
                self._close_in_background(stream)
            raise
        except Exception as e:               # noqa: BLE001
            self._log(f"stt: stream {no} did not start: {type(e).__name__}")
            if stream is not None:
                self._close_in_background(stream)
            return None
        s = _Stream(no, stream)
        s.task = asyncio.get_running_loop().create_task(self._read(s))
        self._readers.add(s.task)
        s.task.add_done_callback(self._readers.discard)
        return s

    async def _flush(self, s: _Stream) -> bool:
        while self._buffer:
            if s.dead:                       # the reader saw the stream die: keep the rest buffered
                return False
            chunk, wall = self._buffer.popleft()
            self._buffer_bytes -= len(chunk)
            s.clock.add(len(chunk), wall)
            try:
                await asyncio.wait_for(s.stream.send_pcm(chunk), self._send_timeout_s)
            except Exception:                # noqa: BLE001 -- includes a send timeout
                s.dead = True
                self._buffer.appendleft((chunk, wall))
                self._buffer_bytes += len(chunk)
                return False
            except BaseException:            # cancelled mid-send: the chunk is not lost
                self._buffer.appendleft((chunk, wall))
                self._buffer_bytes += len(chunk)
                raise
        return True

    async def _read(self, s: _Stream) -> None:
        try:
            async for ev in s.stream.events():
                if ev.kind == "error":
                    self._set_state("degraded")
                try:
                    self._on_event(s.no, ev, s.clock)
                except Exception as e:       # noqa: BLE001 -- one bad event never ends the stream
                    self._log(f"stt: event handler failed: {type(e).__name__}")
        except Exception as e:               # noqa: BLE001
            self._log(f"stt: stream {s.no} reader failed: {type(e).__name__}")
        finally:
            s.dead = True
            if self._on_stream_end is not None:
                try:
                    self._on_stream_end(s.no)
                except Exception as e:       # noqa: BLE001
                    self._log(f"stt: stream-end handler failed: {type(e).__name__}")
            if s is self._live and not self._closing:
                self._live = None
                self._set_state("degraded")
                self._retire(s)
                self.open()

    def _retire(self, s: _Stream) -> None:
        if s not in self._old:
            self._old.append(s)
        self._close_in_background(s, s)

    def _close_in_background(self, stream, s: Optional[_Stream] = None) -> None:
        task = asyncio.get_running_loop().create_task(self._close_stream(s or _Stream(0, stream)))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _close_stream(self, s: _Stream) -> None:
        try:
            await asyncio.wait_for(s.stream.close(), CLOSE_TIMEOUT_S)
        except Exception:                    # noqa: BLE001
            pass
        if s in self._old:
            self._old.remove(s)


_END = object()
_STOP = object()


class _FlushHandoff:
    """Who gets the finals of one `flush()`: the caller, or `events()` once it gave up."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.abandoned = False
        self.done = False
        self.finals: list = []


class _StreamState:
    """Per stream: its clock and its utterance collector."""

    def __init__(self, no: int, emit: Callable) -> None:
        self.no = no
        self.clock: Optional[AudioClock] = None
        self.frag_times: tuple = (None, None)
        self.collector = UtteranceCollector(
            on_flush_words=lambda text, label, start, end, volume, words, utt_id: emit(
                self, "final", text, label, start, end, utt_id,
                {"stream": no, "volume": volume, "words": words}),
            on_fragment=lambda text, label, utt_id: emit(
                self, "partial", text, label, self.frag_times[0], self.frag_times[1], utt_id,
                {"confirmed": True}))


class InprocessSession:
    """Synchronous facade over `_StreamSession` and one event loop on a
    daemon thread. Implements `shared.stt.SttAudioSession`."""

    def __init__(self, cfg: SttConfig, engine_factory: Callable, *,
                 buffer_s: float = BUFFER_S, retry_s=RETRY_S,
                 wall: Callable[[], float] = wall_ms,
                 send_timeout_s: float = SEND_TIMEOUT_S,
                 log: Callable[[str], None] = _log.warning) -> None:
        self._cfg = cfg
        self._factory = engine_factory
        self._wall = wall
        self._log = log
        self._stage_log = lambda msg: self._log(msg)   # own identity: stage-error dedupe is per session
        self._kw = dict(buffer_s=buffer_s, retry_s=retry_s, wall=wall, send_timeout_s=send_timeout_s)
        self._out: queue.Queue = queue.Queue()
        self._capture: Optional[list] = None   # set (on the loop) while flush() collects finals
        self._streams: dict[int, _StreamState] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="stt-inprocess", daemon=True)
        self._inq: Optional[asyncio.Queue] = None
        self._sender: Optional[asyncio.Task] = None
        self._ss: Optional[_StreamSession] = None
        self._closed = False
        self._close_lock = threading.Lock()
        self.states: list[str] = []            # every state change, in order

    # ----------------------------------------------------------- lifecycle
    @property
    def state(self) -> str:
        return self._ss.state if self._ss is not None else "connecting"

    @property
    def streams_started(self) -> int:
        return self._ss.streams_started if self._ss is not None else 0

    def start(self) -> "InprocessSession":
        self._thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self._astart(), self._loop).result()
        except BaseException:
            self._stop_loop()
            raise
        return self

    def _stop_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        if not self._thread.is_alive():
            self._loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _astart(self) -> None:
        self._inq = asyncio.Queue()
        self._ss = _StreamSession(self._factory, self._on_event, self.states.append,
                                  speakers=self._cfg.speakers, log=self._log,
                                  on_stream_end=self._on_stream_end, **self._kw)
        self._sender = asyncio.get_running_loop().create_task(self._send_loop())
        self._ss.open()

    async def _send_loop(self) -> None:
        # one sender: chunks reach the engine in exactly the order they were queued
        while True:
            item = await self._inq.get()
            if item is _STOP:
                return
            chunk, wall = item
            await self._ss.send_pcm(chunk, wall)

    # --------------------------------------------------------------- API
    def send_pcm(self, pcm16k: bytes) -> None:
        if not pcm16k or self._closed:
            return
        item = (bytes(pcm16k), self._wall())
        try:
            self._loop.call_soon_threadsafe(self._inq.put_nowait, item)
        except RuntimeError:                   # the loop is already gone
            pass

    def events(self) -> Iterator[TranscriptEvent]:
        while True:
            item = self._out.get()
            if item is _END:
                self._out.put(_END)            # a second iterator ends too
                return
            yield item

    def flush(self, timeout_s: float = 2.0) -> list[TranscriptEvent]:
        if self._closed:
            return []
        hand = _FlushHandoff()
        coro = self._aflush(hand)
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:                   # the loop closed under us
            coro.close()
            return []
        try:
            fut.result(timeout_s)
        except Exception:                      # noqa: BLE001 -- timeout or closed loop
            pass
        # exactly one side gets the finals: the caller (returned) or events() (abandoned)
        with hand.lock:
            if hand.done:
                return hand.finals
            hand.abandoned = True
        fut.cancel()
        return []

    async def _aflush(self, hand: "_FlushHandoff") -> None:
        with hand.lock:
            abandoned = hand.abandoned
        if abandoned:                          # the caller gave up: finals go out through events()
            self._flush_collectors()
            return
        self._capture = []
        try:
            self._flush_collectors()
            finals = list(self._capture)
        finally:
            self._capture = None
        with hand.lock:
            if hand.abandoned:
                for ev in finals:
                    self._out.put(ev)
            else:
                hand.finals, hand.done = finals, True

    def _flush_collectors(self) -> None:
        for st in list(self._streams.values()):
            st.collector.flush()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if self._thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(self._aclose(), self._loop).result()
            except Exception as e:             # noqa: BLE001
                self._log(f"stt: close failed: {type(e).__name__}")
            self._stop_loop()
        self._out.put(_END)

    async def _aclose(self) -> None:
        try:
            if self._sender is not None:
                # audio already handed over goes through before the stream closes
                self._inq.put_nowait(_STOP)
                _, pending = await asyncio.wait([self._sender], timeout=CLOSE_TIMEOUT_S)
                if pending:
                    self._sender.cancel()
                    await asyncio.wait([self._sender])
            await self._ss.close()
        finally:
            self._flush_collectors()           # nothing buffered is lost on close

    def _on_stream_end(self, no: int) -> None:
        """A stream's reader ended (engine error or end): its open utterance is final now."""
        st = self._streams.pop(no, None)
        if st is not None:
            st.collector.flush()

    # ------------------------------------------------- SttEvent -> TranscriptEvent
    def _on_event(self, no: int, ev, clock: AudioClock) -> None:
        st = self._streams.get(no)
        if st is None:
            st = self._streams[no] = _StreamState(no, self._emit)
        st.clock = clock
        if ev.kind == "partial":
            if (ev.text or "").strip():
                utt_id = st.collector.current_utt_id(ev.speaker_label)
                self._emit(st, "partial", ev.text, ev.speaker_label, ev.start_s, ev.end_s, utt_id, {})
        elif ev.kind == "final":
            st.frag_times = (ev.start_s, ev.end_s)
            st.collector.on_final(ev.text, ev.speaker_label, ev.start_s, ev.end_s, ev.volume, ev.words)
        elif ev.kind == "eou":
            st.collector.on_eou()
        # "error": the session already degraded and reconnects; nothing is emitted

    def _emit(self, st: _StreamState, kind: str, text: str, label, start, end,
              utt_id, extra: dict) -> None:
        wall = st.clock.wall_at(start) if st.clock is not None and _number(start) else None
        tev = TranscriptEvent(
            type=kind, recognizer_id=self._cfg.recognizer_id, timestamp=time.time(),
            text=text, turn_id=f"{st.no}-{utt_id}", speaker=label,
            t_start=start, t_end=end,
            audio_epoch=wall / 1000.0 - start if wall is not None else None,
            backend=BACKEND, extra=dict(extra))
        for out in run_stages(self._cfg.stages, tev, self._stage_log):
            if self._capture is not None and out.type == "final":
                self._capture.append(out)
            else:
                self._out.put(out)
