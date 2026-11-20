"""Cdp dispatches events off the reader thread, so a slow handler never
delays a call() response. No browser, no real socket: a fake connection
object stands in for websocket.create_connection()."""
import json
import queue
import threading
import time

from fundusapps.bbb_tap import cdp


class FakeConn:
    """Enough of the websocket-client connection interface for Cdp: a
    recv() that blocks on an internal queue, and a send() that -- like a
    real CDP server -- answers a call immediately with a matching id."""

    def __init__(self):
        self._incoming = queue.Queue()
        self.sent = []

    def settimeout(self, timeout):
        pass

    def recv(self):
        msg = self._incoming.get()
        if msg is None:
            raise ConnectionError("fake connection closed")
        return msg

    def send(self, payload):
        msg = json.loads(payload)
        self.sent.append(msg)
        if "id" in msg:
            self._incoming.put(json.dumps({"id": msg["id"], "result": {}}))

    def push_event(self, method, params=None):
        self._incoming.put(json.dumps({"method": method, "params": params or {}}))

    def close(self):
        self._incoming.put(None)


def make_cdp(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(cdp.websocket, "create_connection", lambda *a, **kw: fake)
    return cdp.Cdp("ws://fake"), fake


def test_slow_event_handler_does_not_delay_call_response(monkeypatch):
    c, fake = make_cdp(monkeypatch)
    handler_started = threading.Event()
    handler_done = threading.Event()

    def slow_handler(params):
        handler_started.set()
        time.sleep(0.5)
        handler_done.set()

    c.on("Test.event", slow_handler)
    fake.push_event("Test.event")
    assert handler_started.wait(1.0), "dispatch thread never picked up the event"

    t0 = time.monotonic()
    result = c.call("Some.method", timeout=5)
    elapsed = time.monotonic() - t0

    assert result == {}
    # The handler is still sleeping (0.5s) -- call() came back on its own,
    # not after waiting for the handler to finish on a shared thread.
    assert elapsed < 0.3, f"call() took {elapsed:.3f}s -- the slow handler blocked it"
    assert not handler_done.is_set()
    assert handler_done.wait(1.0), "handler never completed"
    c.close()


def test_call_responses_are_not_reordered_by_events(monkeypatch):
    c, fake = make_cdp(monkeypatch)
    seen = []
    c.on("Noise.event", lambda params: seen.append(params))
    for i in range(5):
        fake.push_event("Noise.event", {"i": i})
        assert c.call("Method.%d" % i) == {}
    c.close()


def test_launch_chromium_does_not_open_the_debug_port_to_every_origin(monkeypatch, tmp_path):
    # Cdp connects with suppress_origin=True; --remote-allow-origins=* would
    # only let arbitrary web pages the browser opens talk to its own port.
    seen = {}

    def fake_popen(args, **kw):
        seen["args"] = args
        return object()

    monkeypatch.setattr(cdp.subprocess, "Popen", fake_popen)
    cdp.launch_chromium("/usr/bin/chromium", 9224, str(tmp_path / "profile"))
    assert "--remote-debugging-port=9224" in seen["args"]
    assert not any(a.startswith("--remote-allow-origins") for a in seen["args"])
