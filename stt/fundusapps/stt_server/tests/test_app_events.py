import json
import sys
import threading
import time

# Mock sys.argv to avoid argparse error during app import
original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]

from fundusapps.stt_server.app import app
from fundusapps.stt_server.events import EventBus, TranscriptionEvent

# Restore original argv
sys.argv = original_argv


def test_events_route_streams_published_events():
    bus = EventBus()
    app.event_bus = bus

    client = app.test_client()
    received = []

    def consume():
        with client.get("/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            for raw in resp.response:
                if not raw:
                    continue
                line = raw.decode("utf-8").strip()
                if line.startswith("data:"):
                    received.append(json.loads(line[len("data:"):].strip()))
                if len(received) >= 1:
                    break

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(0.1)  # let the subscriber attach

    bus.publish(TranscriptionEvent(
        recognizer_id="left", type="partial", text="hi",
        timestamp=1.0, backend="vosk",
    ))

    t.join(timeout=2.0)
    assert received, "no SSE event received within timeout"
    assert received[0]["text"] == "hi"
    assert received[0]["recognizer_id"] == "left"

def test_operator_page_renders():
    app.sst_server = None
    client = app.test_client()
    resp = client.get("/operator")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STT Operator" in body
    assert 'EventSource("/events")' in body
