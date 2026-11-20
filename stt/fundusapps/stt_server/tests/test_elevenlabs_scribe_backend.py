import pytest

from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
    _common_word_prefix,
)


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("das ist ein test", "das ist ein test", "das ist ein test"),
        ("das ist ein", "das ist ein test", "das ist ein"),
        ("das ist ein", "das war ein test", "das"),
        ("hallo welt", "tschuess welt", ""),
        ("", "anything", ""),
        ("anything", "", ""),
        ("solo", "solo", "solo"),
        ("  leading", "  leading whitespace", "leading"),
    ],
)
def test_common_word_prefix(a, b, expected):
    assert _common_word_prefix(a, b) == expected


import time

from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
    _ScribeStateMachine,
)


def _drain(sub, deadline_s=0.2):
    out = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            out.append(sub.get(timeout=0.05))
        except Exception:
            continue
    return out


def test_ui_partials_emit_on_every_distinct_text():
    """Every distinct partial_transcript publishes an extending=False event
    for the operator UI to overwrite the live line."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("das")
    sm.on_partial_transcript("das ist")
    sm.on_partial_transcript("das ist ein")
    events = _drain(sub)
    ui = [e for e in events if e.type == "partial" and e.extending is False]
    assert [p.text for p in ui] == ["das", "das ist", "das ist ein"]


def test_botland_partials_confirm_via_localagreement_2():
    """extending=True events fire when two consecutive partials BOTH had a
    word at a position — the latest word counts as confirmed even if
    revised (the user-stated rule: '1x geändert = bestätigt'). The
    confirmed-prefix path is what the botland delta-suffix uses."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("das")          # need a 2nd to confirm anything
    sm.on_partial_transcript("das ist")       # confirms "das"
    sm.on_partial_transcript("das ist ein")   # confirms "das ist"
    events = _drain(sub)
    botland = [e for e in events if e.type == "partial" and e.extending is True]
    assert [b.text for b in botland] == ["das", "das ist"]


def test_localagreement_treats_revisions_as_confirmed():
    """When prior had word at position N and current also has a word at
    position N, the latest version is confirmed regardless of equality."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("Hallo Welt")
    sm.on_partial_transcript("Hallo, Welt heute")
    events = _drain(sub)
    botland = [e for e in events if e.extending is True]
    # prior=2 words, curr=3 words → confirmed = first 2 of curr = "Hallo, Welt".
    # Pos 0 revised "Hallo"->"Hallo,"; pos 1 same; pos 2 not confirmed yet.
    assert [b.text for b in botland] == ["Hallo, Welt"]


def test_botland_stops_after_committed_prefix_revision():
    """If a later partial would shift an already-confirmed prefix (e.g. a
    comma inserted mid-text), no botland event fires — botserver shouldn't
    receive contradictory partial corrections. The authoritative correction
    arrives later via committed_transcript / final."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("Hallo Welt")
    sm.on_partial_transcript("Hallo Welt heute")  # confirms "Hallo Welt"
    _drain(sub, deadline_s=0.05)
    # Comma revision at position 0 — confirmed candidate "Hallo, Welt heute"
    # does not extend committed prefix "Hallo Welt" → skip botland.
    sm.on_partial_transcript("Hallo, Welt heute")
    events = _drain(sub, deadline_s=0.05)
    botland = [e for e in events if e.extending is True]
    assert botland == []


def test_partials_dedupe_identical_revisions():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("das ist ein")  # UI event; no LA-2 yet
    sm.on_partial_transcript("das ist ein")  # dedupe -> no events at all
    sm.on_partial_transcript("das war ein")  # UI + LA-2 confirms 3 words ("das war ein")
    events = _drain(sub)
    ui = [e for e in events if e.extending is False]
    botland = [e for e in events if e.extending is True]
    assert [p.text for p in ui] == ["das ist ein", "das war ein"]
    assert [p.text for p in botland] == ["das war ein"]


def test_partial_seq_monotonic_starts_at_zero():
    """partial_seq increments on every emitted event (UI and botland)."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("eins")               # seq 0 (UI)
    sm.on_partial_transcript("eins zwei")          # seq 1 (UI), seq 2 (botland: "eins")
    sm.on_partial_transcript("eins zwei drei")     # seq 3 (UI), seq 4 (botland: "eins zwei")
    events = _drain(sub)
    partials = [e for e in events if e.type == "partial"]
    assert [p.partial_seq for p in partials] == [0, 1, 2, 3, 4]


def test_partial_carries_turn_id_and_backend_name():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("eins")
    sm.on_partial_transcript("eins zwei")
    events = _drain(sub)
    p = [e for e in events if e.type == "partial"][0]
    assert p.recognizer_id == "left"
    assert p.backend == "elevenlabs-scribe"
    assert p.turn_id is not None


import logging


def test_committed_transcript_emits_final_and_resets_turn():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("das ist")
    sm.on_partial_transcript("das ist ein")
    turn_a = sm._turn_id
    sm.on_committed_transcript("das ist ein test")
    # New turn should start fresh on the next partial.
    sm.on_partial_transcript("neuer satz")
    turn_b = sm._turn_id
    events = _drain(sub)
    finals = [e for e in events if e.type == "final"]
    assert [f.text for f in finals] == ["das ist ein test"]
    assert finals[0].turn_id == turn_a
    assert turn_b is not None and turn_b != turn_a


def test_committed_transcript_can_diverge_from_partials():
    """ElevenLabs's committed text can differ entirely from prior partials;
    we emit it verbatim as the final — no divergence warning."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("Zeig mir, äh-")
    sm.on_committed_transcript("Hallo, hallo. Test eins, zwei.")
    events = _drain(sub)
    finals = [e for e in events if e.type == "final"]
    assert [f.text for f in finals] == ["Hallo, hallo. Test eins, zwei."]


def test_tick_is_a_noop():
    """Finalisation is driven by `committed_transcript` from server VAD,
    not by wall-clock silence. `tick` should never emit anything."""
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus, silence_timeout_s=0.5)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("das ist ein")
    _drain(sub, deadline_s=0.05)  # discard the partial
    sm.tick(now=1000.3)
    sm.tick(now=1099.0)  # way past any plausible timeout
    events = _drain(sub, deadline_s=0.05)
    finals = [e for e in events if e.type == "final"]
    assert finals == []


def test_final_carries_same_turn_id_as_partials():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("eins")
    sm.on_partial_transcript("eins zwei")
    sm.on_committed_transcript("eins zwei drei")
    events = _drain(sub)
    partials = [e for e in events if e.type == "partial"]
    finals = [e for e in events if e.type == "final"]
    assert finals[0].turn_id == partials[-1].turn_id


def test_two_consecutive_turns_get_distinct_turn_ids():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus)
    # Turn 1
    sm.on_audio_present(now=1000.0)
    sm.on_partial_transcript("eins")
    sm.on_partial_transcript("eins zwei")
    sm.on_committed_transcript("eins zwei drei")
    # Turn 2
    sm.on_audio_present(now=1002.0)
    sm.on_partial_transcript("drei")
    sm.on_partial_transcript("drei vier")
    sm.on_committed_transcript("drei vier fünf")
    events = _drain(sub)
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 2
    assert finals[0].turn_id != finals[1].turn_id


def test_tick_with_no_audio_does_nothing():
    bus = EventBus()
    sub = bus.subscribe()
    sm = _ScribeStateMachine(recognizer_id="left", bus=bus, silence_timeout_s=0.5)
    sm.tick(now=1000.0)
    sm.tick(now=2000.0)
    assert _drain(sub, deadline_s=0.05) == []


async def _noop_factory(**kwargs):
    """Returns an object that quietly absorbs everything - for tests that
    don't exercise the WS layer."""
    class _Noop:
        def on(self, event, cb): pass
        async def send(self, msg): pass
        async def close(self): pass
    return _Noop()


def test_backend_state_machines_are_per_recognizer():
    """Two recognizers maintain independent committed prefixes and turn_ids."""
    bus = EventBus()
    sub = bus.subscribe()
    from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
        ElevenLabsScribeBackend,
    )
    backend = ElevenLabsScribeBackend(
        event_bus=bus,
        api_key="dummy",
        connection_factory=_noop_factory,   # no real WS
    )
    try:
        sm_l = backend._get_state_machine("left")
        sm_r = backend._get_state_machine("right")
        sm_l.on_audio_present(now=1000.0)
        sm_l.on_partial_transcript("links eins")
        sm_l.on_partial_transcript("links eins zwei")
        sm_r.on_audio_present(now=1000.0)
        sm_r.on_partial_transcript("rechts eins")
        sm_r.on_partial_transcript("rechts eins zwei")
        events = _drain(sub)
        partials = [e for e in events if e.type == "partial"]
        left = [p for p in partials if p.recognizer_id == "left"]
        right = [p for p in partials if p.recognizer_id == "right"]
        ui_left = [p for p in left if p.extending is False]
        ui_right = [p for p in right if p.extending is False]
        assert ui_left and ui_right
        assert ui_left[-1].text == "links eins zwei"
        assert ui_right[-1].text == "rechts eins zwei"
        assert ui_left[-1].turn_id != ui_right[-1].turn_id
    finally:
        backend.close()


def test_backend_wants_streaming_is_true():
    bus = EventBus()
    from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
        ElevenLabsScribeBackend,
    )
    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=_noop_factory,
    )
    try:
        assert backend.wants_streaming() is True
    finally:
        backend.close()


def test_from_args_fails_fast_on_missing_key(monkeypatch):
    from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
        ElevenLabsScribeBackend,
    )
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    class _Args:
        api_key_env = "ELEVENLABS_API_KEY"
        model = "scribe_v2_realtime"
        language = "de"
        commit_strategy = "vad"
        silence_timeout = 0.7

    bus = EventBus()
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY not set"):
        ElevenLabsScribeBackend.from_args(bus, _Args())


def test_from_args_uses_custom_key_env(monkeypatch):
    from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
        ElevenLabsScribeBackend,
    )
    monkeypatch.setenv("CUSTOM_EL_KEY", "sk_test")

    class _Args:
        api_key_env = "CUSTOM_EL_KEY"
        model = "scribe_v2_realtime"
        language = "de"
        commit_strategy = "vad"
        silence_timeout = 0.7

    bus = EventBus()
    backend = ElevenLabsScribeBackend.from_args(bus, _Args())
    try:
        assert backend._api_key == "sk_test"
    finally:
        backend.close()


import numpy as np

from fundusapps.stt_server.backends.elevenlabs_scribe_backend import (
    ElevenLabsScribeBackend,
)


class FakeConnection:
    """Synchronously-invokable fake of the ElevenLabs realtime connection.

    Captures sent audio chunks and exposes `inject(event, payload)` to fire
    the registered handlers.
    """
    def __init__(self):
        self.handlers = {}
        self.sent: list = []
        self.closed = False
        self.connect_kwargs: dict = {}

    def on(self, event, cb):
        self.handlers.setdefault(event, []).append(cb)

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        self.closed = True

    def inject(self, event, payload):
        for cb in self.handlers.get(event, []):
            cb(payload)


def test_process_chunk_forwards_audio_to_fake_ws():
    """process_chunk -> WS receives one input_audio_chunk message."""
    bus = EventBus()
    fake = FakeConnection()

    async def factory(**kwargs):
        fake.connect_kwargs = kwargs
        return fake

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
    )
    try:
        chunk = np.ones(1600, dtype=np.int16)  # 0.1s @ 16k
        list(backend.process_chunk(chunk, recognizer_id="left"))
        # Give the loop thread a moment to drain.
        deadline = time.time() + 1.0
        while time.time() < deadline and not fake.sent:
            time.sleep(0.05)
        assert len(fake.sent) >= 1
        msg = fake.sent[0]
        assert "audioBase64" in msg or "audio_base_64" in msg
    finally:
        backend.close()


def test_ws_partial_event_flows_to_state_machine():
    """FakeConnection injects a partial_transcript -> bus sees partial events."""
    bus = EventBus()
    sub = bus.subscribe()
    fake = FakeConnection()

    async def factory(**kwargs):
        return fake

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
    )
    try:
        # Force WS task to start.
        chunk = np.ones(1600, dtype=np.int16)
        list(backend.process_chunk(chunk, recognizer_id="left"))
        # Wait for the per-rid task to register handlers.
        deadline = time.time() + 1.0
        while time.time() < deadline and "partial_transcript" not in fake.handlers:
            time.sleep(0.05)
        assert "partial_transcript" in fake.handlers
        # Inject two partials.
        fake.inject("partial_transcript", {"message_type": "partial_transcript", "text": "das ist"})
        fake.inject("partial_transcript", {"message_type": "partial_transcript", "text": "das ist ein"})
        events = _drain(sub, deadline_s=1.0)
        partials = [e for e in events if e.type == "partial"]
        assert any(p.text == "das ist" for p in partials)
    finally:
        backend.close()


import asyncio
import threading


def test_auth_error_stops_reconnect(caplog):
    """auth_error fires -> _run_recognizer returns; no further connect attempts."""
    caplog.set_level(logging.ERROR)
    bus = EventBus()
    connect_calls = []

    class _AuthFailFake(FakeConnection):
        pass

    async def factory(**kwargs):
        connect_calls.append(kwargs)
        fake = _AuthFailFake()
        # Schedule an auth-error injection right after registration.
        async def _fire():
            await asyncio.sleep(0.05)
            fake.inject("auth_error", {"message_type": "auth_error"})
        asyncio.get_event_loop().create_task(_fire())
        return fake

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
    )
    try:
        chunk = np.ones(1600, dtype=np.int16)
        list(backend.process_chunk(chunk, recognizer_id="left"))
        time.sleep(1.0)
        # Exactly one connect attempt - no reconnect after auth failure.
        assert len(connect_calls) == 1
    finally:
        backend.close()


def test_generic_error_triggers_reconnect_with_backoff():
    """A plain Exception in the WS loop -> reconnect after 1s."""
    bus = EventBus()
    connect_count = [0]
    barrier = threading.Event()

    async def factory(**kwargs):
        connect_count[0] += 1
        if connect_count[0] == 1:
            raise RuntimeError("network blip")
        # Second call succeeds.
        barrier.set()
        return FakeConnection()

    backend = ElevenLabsScribeBackend(
        event_bus=bus, api_key="dummy", connection_factory=factory,
    )
    try:
        chunk = np.ones(1600, dtype=np.int16)
        list(backend.process_chunk(chunk, recognizer_id="left"))
        assert barrier.wait(timeout=3.0), "Expected reconnect did not happen"
        assert connect_count[0] >= 2
    finally:
        backend.close()
