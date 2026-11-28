"""ElevenLabs word timestamps, the session base, and the duplicate.

With include_timestamps on, the server sends a SECOND message per
committed segment. Today's backend wires both onto the same handler, so
every final would be published twice. The state machine therefore holds
plain messages briefly (in a FIFO -- a single slot would let a second
plain final silently overwrite and lose the first), prefers the
timestamped one for whichever entry it names, and swallows whichever of a
matched pair arrives second -- in either order, and it still publishes
each held entry after `hold_s` if its timestamped twin never comes at
all.

Every text here is an invented placeholder.
"""
import time

import numpy as np
import pytest

pytest.importorskip("fundusapps.stt_server.backends.elevenlabs_scribe_backend")

from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
    ElevenLabsScribeBackend, _ScribeStateMachine,
)
from fundusapps.stt_server.events import EventBus

SR = 16000

#: (text, start, end, type) -- a spacing entry in the middle, because the
#: span must come from the WORDS and not from whatever is first in the list.
WORDS = [("Eins", 0.20, 0.55, "word"),
         (" ", 0.55, 0.60, "spacing"),
         ("zwei", 0.60, 1.10, "word")]


def _payload(text, words=WORDS):
    return {"text": text,
            "words": [{"text": t, "start": a, "end": b, "type": ty}
                      for t, a, b, ty in words]}


def _drain(sub, deadline_s=0.3):
    out = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            out.append(sub.get(timeout=0.05))
        except Exception:
            continue
    return out


def _finals(sub, deadline_s=0.3):
    return [e for e in _drain(sub, deadline_s) if e.type == "final"]


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class FakeConnection:
    """Synchronously-invokable fake of the realtime connection."""

    def __init__(self):
        self.handlers = {}
        self.sent = []
        self.closed = False
        self.connect_kwargs = {}

    def on(self, event, cb):
        self.handlers.setdefault(event, []).append(cb)

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        self.closed = True

    def inject(self, event, payload):
        for cb in self.handlers.get(event, []):
            cb(payload)


# --- the state machine ------------------------------------------------

def test_the_timestamps_are_the_session_base_plus_the_word_times():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True)
    sm.session_base_samples = 3 * SR        # this session opened at 3.000 s
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript_with_timestamps(_payload("Eins zwei"), now=1000.0)
    finals = _finals(sub)
    assert len(finals) == 1
    assert abs(finals[0].t_start - 3.200) <= 0.001, finals[0].t_start
    assert abs(finals[0].t_end - 4.100) <= 0.001, finals[0].t_end


def test_the_plain_message_is_held_until_the_timestamped_one_arrives():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=1.5)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Eins zwei", now=1000.0)
    assert _finals(sub, 0.05) == []
    sm.on_committed_transcript_with_timestamps(_payload("Eins zwei"), now=1000.1)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].t_start is not None
    sm.tick(now=1010.0)                     # the held copy must be gone
    assert _finals(sub, 0.05) == []


def test_the_plain_duplicate_is_swallowed_when_it_arrives_second():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=1.5)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript_with_timestamps(_payload("Eins zwei"), now=1000.0)
    sm.on_committed_transcript("Eins zwei", now=1000.1)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].t_start is not None
    sm.tick(now=1010.0)
    assert _finals(sub, 0.05) == []


def test_a_final_without_timestamps_is_published_after_the_hold():
    """The timestamped message never comes -- an utterance must not be
    lost over a field that is optional by contract."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=0.5)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Eins zwei", now=1000.0)
    sm.tick(now=1000.4)
    assert _finals(sub, 0.05) == []
    sm.tick(now=1000.6)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].text == "Eins zwei"
    assert finals[0].t_start is None
    assert finals[0].t_end is None


def test_with_timestamps_off_a_plain_message_publishes_immediately():
    """--no-timestamps is today's behaviour, exactly: no hold, no tick,
    no second message. tests/test_elevenlabs_scribe_backend.py pins the
    rest of it and is not edited."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus)   # default: off
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Eins zwei", now=1000.0)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].t_start is None
    sm.tick(now=1099.0)
    assert _finals(sub, 0.05) == []


# --- the backend ------------------------------------------------------

def test_the_first_chunk_sent_sets_the_session_base_and_the_flag_is_sent():
    bus = EventBus()
    sub = bus.subscribe()
    fake = FakeConnection()

    async def factory(**kwargs):
        fake.connect_kwargs = kwargs
        return fake

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
        timestamps=True)
    try:
        chunk = np.ones(1600, dtype=np.int16)
        list(backend.process_chunk(chunk, recognizer_id="0",
                                   audio_offset=5 * SR))
        assert _wait_for(lambda: bool(fake.sent))
        assert fake.connect_kwargs.get("include_timestamps") is True
        sm = backend._get_state_machine("0")
        assert sm.session_base_samples == 5 * SR
        assert _wait_for(
            lambda: "committed_transcript_with_timestamps" in fake.handlers)
        fake.inject("committed_transcript_with_timestamps",
                    _payload("Eins zwei"))
        finals = _finals(sub, 1.0)
        assert len(finals) == 1
        assert abs(finals[0].t_start - 5.200) <= 0.001, finals[0].t_start
    finally:
        backend.close()


def test_a_reconnect_starts_a_new_session_base():
    """Word times are relative to the start of a WebSocket SESSION, so a
    base carried across a reconnect would be wrong by the whole length of
    the previous one."""
    bus = EventBus()
    fakes = []

    class _BreakOnce(FakeConnection):
        def __init__(self, fail):
            super().__init__()
            self.fail = fail

        async def send(self, msg):
            if self.fail:
                self.fail = False
                raise RuntimeError("network blip")
            await super().send(msg)

    async def factory(**kwargs):
        f = _BreakOnce(fail=(len(fakes) == 0))
        fakes.append(f)
        return f

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
        timestamps=True)
    try:
        list(backend.process_chunk(np.ones(1600, dtype=np.int16),
                                   recognizer_id="0", audio_offset=0))
        assert _wait_for(lambda: len(fakes) >= 2, 8.0), len(fakes)
        list(backend.process_chunk(np.ones(1600, dtype=np.int16),
                                   recognizer_id="0", audio_offset=11 * SR))
        assert _wait_for(lambda: bool(fakes[1].sent), 5.0)
        assert backend._get_state_machine("0").session_base_samples == 11 * SR
    finally:
        backend.close()


# --- fix round 1: a held final must survive losing its session ---------
#
# A plain committed_transcript that is still waiting (within hold_s) for
# its timestamped twin was stored ONLY in the state machine's `_pending`.
# If that state machine is then discarded -- session breaks and the
# reconnect path pops it, or the backend is closed -- the held final was
# never published. That is a regression against the pre-Task-8 behaviour
# ("a lost message costs the timestamps, never the utterance"): before
# timestamps existed, a plain final was published immediately.

def test_a_pending_final_is_flushed_without_timestamps_when_the_session_breaks():
    """The twin was never going to arrive on a session that just died --
    the held final must be released right away, not lost with the state
    machine that held it."""
    bus = EventBus()
    sub = bus.subscribe()
    fakes = []

    class _BreakOnSecondSend(FakeConnection):
        async def send(self, msg):
            if len(self.sent) >= 1:
                raise RuntimeError("network blip")
            await super().send(msg)

    async def factory(**kwargs):
        f = _BreakOnSecondSend()
        fakes.append(f)
        return f

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
        timestamps=True, hold_s=10.0)   # long hold: only the break must
                                         # release this, not tick()
    try:
        list(backend.process_chunk(np.ones(1600, dtype=np.int16),
                                   recognizer_id="0", audio_offset=0))
        assert _wait_for(lambda: bool(fakes) and bool(fakes[0].sent))
        fakes[0].inject("committed_transcript", {"text": "Eins zwei"})
        assert _wait_for(
            lambda: bool(backend._get_state_machine("0")._pending))
        # A second chunk trips the fake's second send() and kills the
        # session the plain final arrived on.
        list(backend.process_chunk(np.ones(1600, dtype=np.int16),
                                   recognizer_id="0", audio_offset=1600))
        finals = _finals(sub, 3.0)
        assert len(finals) == 1
        assert finals[0].text == "Eins zwei"
        assert finals[0].t_start is None
        assert finals[0].t_end is None
    finally:
        backend.close()


def test_a_pending_final_is_flushed_without_timestamps_on_close():
    """Same contract as the reconnect case above, but for backend.close()."""
    bus = EventBus()
    sub = bus.subscribe()
    fake = FakeConnection()

    async def factory(**kwargs):
        return fake

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
        timestamps=True, hold_s=10.0)
    try:
        list(backend.process_chunk(np.ones(1600, dtype=np.int16),
                                   recognizer_id="0", audio_offset=0))
        assert _wait_for(lambda: bool(fake.sent))
        fake.inject("committed_transcript", {"text": "Eins zwei"})
        assert _wait_for(
            lambda: bool(backend._get_state_machine("0")._pending))
    finally:
        backend.close()
    finals = _finals(sub, 1.0)
    assert len(finals) == 1
    assert finals[0].text == "Eins zwei"
    assert finals[0].t_start is None
    assert finals[0].t_end is None


# --- fix round 1: the duplicate filter must not arm on a matched pair --

def test_a_second_plain_duplicate_with_no_twin_is_not_swallowed():
    """`_published_ts` must only be armed when the timestamped message
    found NO matching pending plain twin (that is the only case where a
    plain twin is still to come). Otherwise a genuinely repeated short
    utterance within hold_s -- someone saying "Ja." twice -- has its
    second, twin-less occurrence silently dropped instead of held and
    released after hold_s."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=0.5)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Ja.", now=1000.0)
    sm.on_committed_transcript_with_timestamps(_payload("Ja."), now=1000.1)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].t_start is not None

    # A genuinely repeated utterance: plain "Ja." again, no twin this time.
    sm.on_committed_transcript("Ja.", now=1000.2)
    assert _finals(sub, 0.05) == []             # held, waiting for hold_s
    sm.tick(now=1000.8)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].text == "Ja."
    assert finals[0].t_start is None


# --- fix round 2: a FIFO, not a single pending slot --------------------
#
# The single `_pending` slot let a second plain committed_transcript
# silently overwrite the first, with no twin arriving for either -- the
# first utterance was never published at all. That breaks the design
# promise: "a lost message costs the timestamps, never the utterance."

def test_two_plain_finals_with_no_twins_are_both_published_after_the_hold():
    """Two plain finals arrive back to back, no timestamped twin for
    either. The single-slot bug: the second overwrites the first in
    `_pending`, so only "Satz zwei" is ever published. A FIFO must hold
    and eventually publish both, in the order they arrived."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=1.0)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Satz eins", now=1000.0)
    sm.on_committed_transcript("Satz zwei", now=1000.8)
    assert _finals(sub, 0.05) == []
    sm.tick(now=1002.5)                     # past both deadlines
    finals = _finals(sub)
    assert [e.text for e in finals] == ["Satz eins", "Satz zwei"]
    assert all(e.t_start is None for e in finals)


def test_a_timestamped_twin_matches_the_first_pending_entry_with_that_text():
    """P1, P2, then T1 (the twin of P1) must resolve P1 specifically --
    not overwrite or drop P2, which is still waiting on its own twin or
    its own hold_s."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=1.0)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Satz eins", now=1000.0)
    sm.on_committed_transcript("Satz zwei", now=1000.2)
    sm.on_committed_transcript_with_timestamps(_payload("Satz eins"),
                                                now=1000.3)
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].text == "Satz eins"
    assert finals[0].t_start is not None

    sm.tick(now=1002.0)                     # "Satz zwei"'s hold has passed
    finals = _finals(sub)
    assert len(finals) == 1
    assert finals[0].text == "Satz zwei"
    assert finals[0].t_start is None


def test_a_twin_for_the_second_pending_entry_flushes_the_first_ahead_of_it():
    """P1's twin never arrives; P2's twin (T2) does, while P1 is still
    held. Publishing the T2 match right away would let P2 -- said AFTER
    P1 -- leave the bus (with timestamps) before P1 does: the two
    sentences would swap in the output. P1 must be flushed, without
    timestamps, before the T2 match is published."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=1.0)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Satz eins", now=1000.0)
    sm.on_committed_transcript("Satz zwei", now=1000.2)
    sm.on_committed_transcript_with_timestamps(_payload("Satz zwei"),
                                                now=1000.3)
    finals = _finals(sub)
    assert [(e.text, e.t_start is not None) for e in finals] == [
        ("Satz eins", False), ("Satz zwei", True)]

    sm.tick(now=1002.0)                     # past both deadlines
    assert _finals(sub, 0.05) == []


def test_flush_pending_publishes_every_held_entry_in_order():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="0", bus=bus, timestamps=True,
                             hold_s=10.0)
    sm.on_audio_present(now=1000.0)
    sm.on_committed_transcript("Satz eins", now=1000.0)
    sm.on_committed_transcript("Satz zwei", now=1000.2)
    sm.flush_pending()
    finals = _finals(sub)
    assert [e.text for e in finals] == ["Satz eins", "Satz zwei"]
    assert all(e.t_start is None for e in finals)


# --- T8-6: --no-timestamps is the rollback lever, at the backend level -

def test_with_timestamps_off_the_backend_requests_no_timestamps_and_publishes_immediately():
    """docs/stt.md's rollback lever: --no-timestamps must (a) request
    include_timestamps=False on connect and (b) publish plain finals right
    away, with no hold_s wait -- not just at the state-machine level, but
    through the real backend/connection wiring."""
    bus = EventBus()
    sub = bus.subscribe()
    fake = FakeConnection()

    async def factory(**kwargs):
        fake.connect_kwargs = kwargs
        return fake

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
        timestamps=False)
    try:
        list(backend.process_chunk(np.ones(1600, dtype=np.int16),
                                   recognizer_id="0", audio_offset=0))
        assert _wait_for(lambda: bool(fake.sent))
        assert fake.connect_kwargs.get("include_timestamps") is False
        fake.inject("committed_transcript", {"text": "Eins zwei"})
        finals = _finals(sub, 0.3)
        assert len(finals) == 1
        assert finals[0].text == "Eins zwei"
        assert finals[0].t_start is None
    finally:
        backend.close()
