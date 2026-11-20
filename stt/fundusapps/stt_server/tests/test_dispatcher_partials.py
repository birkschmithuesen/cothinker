import sys
import threading
import time

# Mock sys.argv to avoid argparse error during sr.py import (via args.py)
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]

from fundusapps.stt_server.events import EventBus, TranscriptionEvent
from fundusapps.stt_server.sr import STTServer

# Restore original argv
sys.argv = _original_argv


class _StubThread:
    """Minimal stand-in for an STTRecognizer thread in id_to_idx."""
    def __init__(self, recognizer_id):
        self.recognizer_id = recognizer_id


def _start_dispatcher_with_threads(bus, threads,
                                   record_cb=None, partial_cb=None):
    server = STTServer.__new__(STTServer)
    server.event_bus = bus
    server.threads = threads
    server.external_record_callback = record_cb
    server.external_stop_callback = None
    server.external_partial_callback = partial_cb
    from threading import Event, Thread
    server._dispatch_stop = Event()
    server._bus_subscriber = bus.subscribe()
    server._dispatch_thread = Thread(
        target=server._dispatch_events_to_callbacks, daemon=True
    )
    server._dispatch_thread.start()
    return server


def _stop(server):
    server._dispatch_stop.set()
    server._dispatch_thread.join(timeout=1.0)
    server.event_bus.unsubscribe(server._bus_subscriber)


def test_partial_routed_to_partial_callback():
    bus = EventBus()
    received = []
    server = _start_dispatcher_with_threads(
        bus,
        [_StubThread("left"), _StubThread("right")],
        partial_cb=lambda idx, text, turn_id, seq: received.append((idx, text, turn_id, seq)),
    )
    try:
        bus.publish(TranscriptionEvent(
            recognizer_id="right", type="partial", text="Hallo",
            timestamp=time.time(), backend="whisper-streaming",
            turn_id="01TURN", partial_seq=2,
        ))
        deadline = time.time() + 2.0
        while not received and time.time() < deadline:
            time.sleep(0.05)
        assert received == [(1, "Hallo", "01TURN", 2)]
    finally:
        _stop(server)


def test_final_still_routed_to_record_callback():
    bus = EventBus()
    received = []
    server = _start_dispatcher_with_threads(
        bus,
        [_StubThread("left")],
        record_cb=lambda idx, text, turn_id: received.append((idx, text, turn_id)),
    )
    try:
        bus.publish(TranscriptionEvent(
            recognizer_id="left", type="final", text="Done",
            timestamp=time.time(), backend="whisper-streaming",
            turn_id="01T",
        ))
        deadline = time.time() + 2.0
        while not received and time.time() < deadline:
            time.sleep(0.05)
        assert received == [(0, "Done", "01T")]
    finally:
        _stop(server)


def test_empty_partial_text_dropped():
    bus = EventBus()
    received = []
    server = _start_dispatcher_with_threads(
        bus,
        [_StubThread("left")],
        partial_cb=lambda *a: received.append(a),
    )
    try:
        bus.publish(TranscriptionEvent(
            recognizer_id="left", type="partial", text="   ",
            timestamp=time.time(), backend="whisper-streaming",
        ))
        time.sleep(0.3)
        assert received == []
    finally:
        _stop(server)


def test_partial_routed_for_regie_audience_recognizer_ids():
    """Dispatcher must map 'regie' -> idx 0 and 'audience' -> idx 1 just like left/right."""
    bus = EventBus()
    received = []
    server = _start_dispatcher_with_threads(
        bus,
        [_StubThread("regie"), _StubThread("audience")],
        partial_cb=lambda idx, text, turn_id, seq: received.append((idx, text, turn_id, seq)),
    )
    try:
        bus.publish(TranscriptionEvent(
            recognizer_id="audience", type="partial", text="Applaus",
            timestamp=time.time(), backend="elevenlabs-scribe",
            turn_id="01A", partial_seq=3,
        ))
        bus.publish(TranscriptionEvent(
            recognizer_id="regie", type="partial", text="Cue 12",
            timestamp=time.time(), backend="elevenlabs-scribe",
            turn_id="01R", partial_seq=5,
        ))
        deadline = time.time() + 2.0
        while len(received) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert (1, "Applaus", "01A", 3) in received
        assert (0, "Cue 12", "01R", 5) in received
    finally:
        _stop(server)
