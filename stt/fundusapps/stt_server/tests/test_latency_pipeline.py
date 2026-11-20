import logging
import time as _time

from fundusapps.stt_server.events import TranscriptionEvent


def test_transcription_event_has_turn_id_field_default_none():
    ev = TranscriptionEvent(
        recognizer_id="left",
        type="final",
        text="hi",
        timestamp=_time.time(),
        backend="vosk",
    )
    assert hasattr(ev, "turn_id")
    assert ev.turn_id is None


def test_transcription_event_carries_turn_id_when_set():
    ev = TranscriptionEvent(
        recognizer_id="left",
        type="final",
        text="hi",
        timestamp=_time.time(),
        backend="vosk",
        turn_id="01TURN",
    )
    assert ev.turn_id == "01TURN"


def test_dispatcher_logs_stt_final_and_calls_callback_with_turn_id(caplog, monkeypatch):
    """STTServer._dispatch_events_to_callbacks must emit a
    [LATENCY phase=stt_final turn=<id>] line and call the external callback
    with (idx, text, turn_id)."""
    import sys
    import types

    # sr.py imports samplerate and sounddevice which may not be installed in CI,
    # and args.py parses sys.argv at import time.
    for mod_name in ("samplerate", "sounddevice"):
        if mod_name not in sys.modules:
            monkeypatch.setitem(sys.modules, mod_name, types.ModuleType(mod_name))

    # args.py uses argparse and reads sys.argv on import; provide valid argv.
    monkeypatch.setattr(sys, "argv", ["pytest", "vosk"])

    # Force re-import of sr (and args) using the patched argv/stubs.
    for mod in list(sys.modules):
        if "fundusapps.stt_server.sr" in mod or "fundusapps.stt_server.args" in mod:
            monkeypatch.delitem(sys.modules, mod, raising=False)

    from fundusapps.stt_server.sr import STTServer
    from fundusapps.stt_server.events import EventBus

    bus = EventBus()
    # Build an STTServer with no threads / no real audio — we only test dispatch.
    server = STTServer.__new__(STTServer)
    server.threads = []  # empty so id_to_idx is empty → idx defaults to 0
    server.event_bus = bus
    server._dispatch_stop = __import__("threading").Event()
    server._bus_subscriber = bus.subscribe()
    received = []
    server.set_external_record_callback(lambda *args: received.append(args))

    # Publish one final event and let the dispatcher pick it up.
    bus.publish(TranscriptionEvent(
        recognizer_id="left",
        type="final",
        text="hello",
        timestamp=_time.time(),
        backend="vosk",
        turn_id="01TURN",
    ))

    # Run the dispatcher in this thread briefly.
    import threading
    with caplog.at_level(logging.INFO):
        t = threading.Thread(target=server._dispatch_events_to_callbacks, daemon=True)
        t.start()
        deadline = _time.time() + 1.0
        while _time.time() < deadline and not received:
            _time.sleep(0.02)
        server._dispatch_stop.set()
        t.join(timeout=1.0)

    assert len(received) == 1
    args = received[0]
    # New signature: (idx, text, turn_id)
    assert args[0] == 0
    assert args[1] == "hello"
    assert args[2] == "01TURN"

    lat = [r.getMessage() for r in caplog.records if "[LATENCY" in r.getMessage()]
    final_lines = [m for m in lat if "phase=stt_final" in m and "turn=01TURN" in m]
    assert len(final_lines) == 1, lat
