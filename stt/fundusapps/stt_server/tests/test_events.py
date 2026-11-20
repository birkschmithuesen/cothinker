import time
import pytest
from fundusapps.stt_server.events import TranscriptionEvent


def test_minimal_partial_event():
    ev = TranscriptionEvent(
        recognizer_id="left",
        type="partial",
        text="hallo",
        timestamp=time.time(),
        backend="vosk",
    )
    assert ev.recognizer_id == "left"
    assert ev.type == "partial"
    assert ev.text == "hallo"
    assert ev.backend == "vosk"
    assert ev.status is None
    assert ev.confidence is None


def test_event_round_trips_through_dict():
    ev = TranscriptionEvent(
        recognizer_id="right",
        type="final",
        text="das ist ein test",
        timestamp=1234.5,
        backend="whisper",
        status=None,
        confidence=0.92,
    )
    d = ev.to_dict()
    assert d == {
        "recognizer_id": "right",
        "type": "final",
        "text": "das ist ein test",
        "timestamp": 1234.5,
        "backend": "whisper",
        "status": None,
        "confidence": 0.92,
        "turn_id": None,
        "partial_seq": None,
        "extending": None,
        "t_start": None,
        "t_end": None,
        "audio_epoch": None,
    }


import queue
from fundusapps.stt_server.events import EventBus


def _make(text="x", typ="partial"):
    return TranscriptionEvent(
        recognizer_id="left", type=typ, text=text, timestamp=0.0, backend="vosk"
    )


def test_subscriber_receives_published_event():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish(_make("hi"))
    got = q.get(timeout=0.5)
    assert got.text == "hi"


def test_multiple_subscribers_each_receive_event():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    bus.publish(_make("multi"))
    assert q1.get(timeout=0.5).text == "multi"
    assert q2.get(timeout=0.5).text == "multi"


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish(_make("none"))
    with pytest.raises(queue.Empty):
        q.get(timeout=0.1)


def test_publish_does_not_block_when_subscriber_full():
    bus = EventBus(max_queue_size=2)
    q = bus.subscribe()
    bus.publish(_make("a"))
    bus.publish(_make("b"))
    bus.publish(_make("c"))  # should drop, not raise, not block
    items = [q.get_nowait().text for _ in range(2)]
    assert items == ["a", "b"]
