"""STT API contract (ADR 0022 level 2; behaviour: docs/architecture/target.md §2.1).

A subject is a callable `make(scenario)` returning a context manager that
yields an `SttSource` for that scenario: "utterance" (partials, then one
final with a speaker and an audio clock), "two_speakers" (two finals, two
speakers, in audio-clock order) and "end" (the stream ends, no events).

Every assertion message starts with a rule id (STT-...), so a red run names
the rule that broke. Outside transports: `COTHINKER_CONTRACT_STT=
package.module:make` (docs/contract-tests.md).
"""
from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import pkgutil
import threading
import time

import pytest

from shared.speechmatics import SttEvent
from shared.stt import SttConfig, TranscriptEvent, open_stt

CONTRACT_API = "stt"
SCENARIOS = ("utterance", "two_speakers", "end")
EVENT_TYPES = {"partial", "final", "thought_end", "mic_gate"}
EXPECTED_FINALS = {"utterance": 1, "two_speakers": 2, "end": 0}
EXPECTED_SPEAKERS = {"utterance": 1, "two_speakers": 2, "end": 0}


# ------------------------------------------------------------------ the contract
def check_stream(events: list[TranscriptEvent]) -> set:
    """The rules that hold for one stream's events, whichever transport.
    Returns the set of distinct speakers seen on a final."""
    seen_turns_with_final: set = set()
    last_t_start: dict = {}
    speakers: set = set()
    for ev in events:
        assert ev.type in EVENT_TYPES, f"STT-TYPE: unknown event type {ev.type!r}"
        assert isinstance(ev.recognizer_id, str), (
            f"STT-FIELDS: recognizer_id is not a string: {ev.recognizer_id!r}")
        assert isinstance(ev.timestamp, (int, float)), (
            f"STT-FIELDS: timestamp is not a number: {ev.timestamp!r}")
        if ev.t_start is not None and ev.t_end is not None:
            assert ev.t_start <= ev.t_end, (
                f"STT-CLOCK: t_start {ev.t_start} after t_end {ev.t_end}")
        if ev.type == "partial":
            assert ev.turn_id not in seen_turns_with_final, (
                f"STT-ORDER: partial after its turn's final: {ev}")
        if ev.type == "final":
            if ev.t_start is not None:
                prev = last_t_start.get(ev.recognizer_id)
                assert prev is None or ev.t_start >= prev, (
                    f"STT-CLOCK: final t_start went back for recognizer "
                    f"{ev.recognizer_id!r}: {ev.t_start} after {prev}")
                last_t_start[ev.recognizer_id] = ev.t_start
            assert ev.speaker, f"STT-SPEAKER: final without a speaker: {ev}"
            speakers.add(ev.speaker)
            seen_turns_with_final.add(ev.turn_id)
    return speakers


def _drain_until_closed(source, min_finals: int, poll_timeout_s: float = 3.0,
                        close_timeout_s: float = 5.0) -> list:
    """Drains `source.events()` on a thread, waits until at least
    `min_finals` finals arrived (or the poll times out), then closes the
    source and proves `events()` ends within `close_timeout_s` of that --
    and that nothing more arrives afterwards (STT-END)."""
    collected: list = []
    done = threading.Event()

    def run():
        try:
            for ev in source.events():
                collected.append(ev)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + poll_timeout_s
    while time.monotonic() < deadline:
        if sum(1 for e in collected if e.type == "final") >= min_finals:
            break
        time.sleep(0.01)
    before_close = time.monotonic()
    source.close()
    ended = done.wait(close_timeout_s)
    assert ended, "STT-END: events() did not end within 5s of close()"
    assert time.monotonic() - before_close <= close_timeout_s, (
        "STT-END: events() took longer than 5s to end after close()")
    snapshot = list(collected)
    time.sleep(0.05)
    assert collected == snapshot, "STT-END: events arrived after the stream ended"
    return collected


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_stream_keeps_the_event_contract(subject, scenario):
    with subject(scenario) as source:
        events = _drain_until_closed(source, EXPECTED_FINALS[scenario])
    speakers = check_stream(events)
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == EXPECTED_FINALS[scenario], (
        f"STT-SPEAKER: expected {EXPECTED_FINALS[scenario]} finals, got {len(finals)}: {events}")
    assert len(speakers) == EXPECTED_SPEAKERS[scenario], (
        f"STT-SPEAKER: expected {EXPECTED_SPEAKERS[scenario]} distinct speakers, got {speakers}")


# ------------------------------------------------------------ built-in subjects: SSE
def _sse_body(wires: list) -> bytes:
    return "".join(f"data: {json.dumps(d)}\n\n" for d in wires).encode()


SSE_WIRE = {
    "utterance": [
        {"type": "partial", "recognizer_id": "sse-1", "timestamp": 1000.0,
         "text": "Hallo", "turn_id": "t1"},
        {"type": "partial", "recognizer_id": "sse-1", "timestamp": 1000.1,
         "text": "Hallo zusammen", "turn_id": "t1"},
        {"type": "final", "recognizer_id": "sse-1", "timestamp": 1000.2,
         "text": "Hallo zusammen", "turn_id": "t1", "t_start": 0.0, "t_end": 0.8,
         "speaker": "S1"},
    ],
    "two_speakers": [
        {"type": "final", "recognizer_id": "sse-1", "timestamp": 1001.0,
         "text": "eins", "turn_id": "t1", "t_start": 0.0, "t_end": 1.0, "speaker": "S1"},
        {"type": "final", "recognizer_id": "sse-1", "timestamp": 1001.5,
         "text": "zwei", "turn_id": "t2", "t_start": 1.2, "t_end": 2.0, "speaker": "S2"},
    ],
    "end": [],
}


def _sse_handler(wires: list):
    body = _sse_body(wires)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.path != "/events":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    return Handler


@contextlib.contextmanager
def sse_subject(scenario):
    """`SseSource` against a fake SSE server on `127.0.0.1`."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _sse_handler(SSE_WIRE[scenario]))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = SttConfig(transport="sse", url=f"http://127.0.0.1:{server.server_address[1]}",
                        read_timeout_s=5.0)
        yield open_stt(cfg)
    finally:
        server.shutdown()
        server.server_close()


def test_sse_wire_round_trips_through_to_wire():
    with sse_subject("utterance") as source:
        events = list(source.events())
        source.close()
    assert [e.to_wire() for e in events] == SSE_WIRE["utterance"], (
        "STT-WIRE: to_wire() did not reproduce the recorded wire dicts")


# ------------------------------------------------------- built-in subjects: in-process
CHUNK = bytes(1280)  # 40 ms of s16le mono 16 kHz


def _sttev(kind: str, *, text: str = "", label=None, start=None, end=None,
          msg_id: str = "e") -> SttEvent:
    return SttEvent(kind=kind, text=text, speaker_label=label, start_s=start, end_s=end,
                    msg_id=msg_id)


RECORDED_SCRIPTS = {
    "utterance": [
        _sttev("partial", text="Hallo", label="P1", start=0.0, end=0.4, msg_id="p1"),
        _sttev("partial", text="Hallo zusammen", label="P1", start=0.0, end=0.8, msg_id="p2"),
        _sttev("final", text="Hallo zusammen", label="P1", start=0.0, end=0.8, msg_id="f1"),
        _sttev("eou", msg_id="u1"),
    ],
    "two_speakers": [
        _sttev("final", text="eins", label="P1", start=0.0, end=1.0, msg_id="f1"),
        _sttev("eou", msg_id="u1"),
        _sttev("final", text="zwei", label="P2", start=1.2, end=2.0, msg_id="f2"),
        _sttev("eou", msg_id="u2"),
    ],
    "end": [],
}


class RecordedEngine:
    """Minimal in-process engine fake (the surface `InprocessSession` expects --
    see `tests/test_stt_inprocess.py`'s `FakeEngine`): replays a scripted
    `SttEvent` list once `send_pcm` is first called."""

    def __init__(self, script):
        self._script = list(script)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._started = False

    async def start(self) -> None:
        pass

    async def send_pcm(self, chunk: bytes) -> None:
        if not self._started:
            self._started = True
            for ev in self._script:
                await self._queue.put(ev)

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        await self._queue.put(None)


@contextlib.contextmanager
def inprocess_subject(scenario):
    """The in-process transport with a `RecordedEngine` in place of the real
    Speechmatics client."""
    script = RECORDED_SCRIPTS[scenario]
    cfg = SttConfig(transport="inprocess", url="")
    session = open_stt(cfg, engine_factory=lambda speakers: RecordedEngine(script))
    try:
        session.send_pcm(CHUNK)
        yield session
    finally:
        session.close()


BUILTIN_SUBJECTS = {"sse": sse_subject, "inprocess": inprocess_subject}


def resolve_outside(value: str):
    return pkgutil.resolve_name(value)


# ------------------------------------------------- red proof: the broken fake
class _BackwardsClockSource:
    """Breaks exactly STT-CLOCK: the second final's t_start is before the
    first's, same recognizer."""

    def __init__(self):
        self._events = [
            TranscriptEvent(type="final", recognizer_id="broken", timestamp=1.0,
                            text="eins", turn_id="t1", t_start=5.0, t_end=6.0, speaker="S1"),
            TranscriptEvent(type="final", recognizer_id="broken", timestamp=2.0,
                            text="zwei", turn_id="t2", t_start=1.0, t_end=2.0, speaker="S2"),
        ]

    def events(self):
        return iter(self._events)

    def close(self) -> None:
        pass


@contextlib.contextmanager
def broken_clock_subject(scenario):
    yield _BackwardsClockSource()


def test_the_broken_fake_fails_the_contract_for_the_clock_rule():
    with pytest.raises(AssertionError, match=r"^STT-CLOCK:"):
        test_a_stream_keeps_the_event_contract(broken_clock_subject, "utterance")


class _BrokenOrderSource:
    """Breaks exactly STT-ORDER: a partial for turn t1 arrives after t1's
    final."""

    def __init__(self):
        self._events = [
            TranscriptEvent(type="final", recognizer_id="broken", timestamp=1.0,
                            text="eins", turn_id="t1", t_start=0.0, t_end=1.0, speaker="S1"),
            TranscriptEvent(type="partial", recognizer_id="broken", timestamp=2.0,
                            text="eins zwei", turn_id="t1"),
        ]

    def events(self):
        return iter(self._events)

    def close(self) -> None:
        pass


@contextlib.contextmanager
def broken_order_subject(scenario):
    yield _BrokenOrderSource()


def test_the_broken_fake_fails_the_contract_for_the_order_rule():
    with pytest.raises(AssertionError, match=r"^STT-ORDER:"):
        test_a_stream_keeps_the_event_contract(broken_order_subject, "utterance")
