"""Just enough Chrome DevTools Protocol for one tab: calls, events, targets."""
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urljoin

import websocket  # websocket-client


class Cdp:
    def __init__(self, ws_url):
        # The shared broker rejects the default Origin header (403, measured).
        self._ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self._ws.settimeout(None)
        self._n = 0
        self._lock = threading.Lock()
        self._waiting = {}
        self._handlers = {}
        self.closed = threading.Event()
        # Events go through this queue to a dedicated dispatch thread. A
        # handler (e.g. Tap.on_binding, which writes to disk and calls
        # PcmSink.send() -- up to a 0.5 s connect timeout while the STT
        # server is down) must never run on the reader thread: that thread
        # also delivers call() responses, and a slow handler would stall
        # join-step polling and every further event behind it.
        self._events = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._dispatch, daemon=True).start()

    def on(self, event, fn):
        self._handlers.setdefault(event, []).append(fn)

    def _reader(self):
        try:
            while True:
                msg = json.loads(self._ws.recv())
                if "id" in msg:
                    slot = self._waiting.pop(msg["id"], None)
                    if slot is not None:
                        slot[1] = msg
                        slot[0].set()
                else:
                    self._events.put(msg)
        except Exception:
            self.closed.set()
            for ev, slot in list(self._waiting.values()):
                ev.set()
            self._events.put(None)  # wake the dispatch thread so it can exit

    def _dispatch(self):
        while True:
            msg = self._events.get()
            if msg is None:
                return
            for fn in self._handlers.get(msg.get("method"), []):
                try:
                    fn(msg.get("params", {}))
                except Exception:
                    logging.exception("CDP event handler failed for %s", msg.get("method"))

    def call(self, method, params=None, timeout=30):
        with self._lock:
            self._n += 1
            n = self._n
        slot = [threading.Event(), None]
        self._waiting[n] = slot
        self._ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
        if not slot[0].wait(timeout) or slot[1] is None:
            raise RuntimeError(f"CDP {method}: no answer")
        if "error" in slot[1]:
            raise RuntimeError(f"CDP {method}: {slot[1]['error'].get('message')}")
        return slot[1].get("result", {})

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass
        self._events.put(None)


def open_target(http_base):
    req = urllib.request.Request(http_base + "/json/new?about:blank", method="PUT")
    t = json.load(urllib.request.urlopen(req, timeout=10))
    return t["id"], t["webSocketDebuggerUrl"]


def close_target(http_base, target_id):
    try:
        urllib.request.urlopen(f"{http_base}/json/close/{target_id}", timeout=5).read()
    except Exception:
        pass


def wait_http(http_base, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(http_base + "/json/version", timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def _parse_meta_refresh(html, base_url):
    m = _META_REFRESH_RE.search(html)
    if not m:
        return None
    parts = m.group(1).split(";", 1)
    if len(parts) < 2:
        return None
    target = parts[1].strip()
    if target.lower().startswith("url="):
        target = target[4:].strip()
    target = target.strip("'\"")
    return urljoin(base_url, target) if target else None


def _fetch(url, timeout=10.0):
    # urlopen already follows ordinary HTTP 3xx redirects on its own and
    # resp.geturl() reports where it ended up; only the meta-refresh hop
    # (e.g. meet.example.org -> room.example.net) needs handling here.
    req = urllib.request.Request(url, headers={"User-Agent": "cothinker-bbb-tap"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read(1 << 16).decode("utf-8", errors="replace")
    return resp.geturl(), body


def resolve_join_url(url, max_hops=3, fetch=_fetch):
    """Follows <meta http-equiv="refresh"> hops (HTTP redirects are
    already handled by `fetch`/urlopen) up to `max_hops`. Never raises --
    a fetch error or an unresolved loop just stops the walk and returns
    the best URL reached so far, since the join must still be attempted
    with whatever resolution managed to produce."""
    current = url
    for _ in range(max_hops):
        try:
            final_url, body = fetch(current)
        except Exception:
            return current
        next_url = _parse_meta_refresh(body, final_url)
        if not next_url or next_url == final_url:
            return final_url
        current = next_url
    return current


def launch_chromium(binary, port, profile_dir):
    # No --remote-allow-origins: Cdp connects with suppress_origin=True and
    # the /json endpoints are fetched without an Origin header, so nothing
    # needs it -- and allowing every origin would let any web page the
    # browser opens talk to its own debugging port. The port stays on
    # Chromium's default bind address, loopback.
    os.makedirs(profile_dir, exist_ok=True)
    args = [binary, "--headless=new", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--autoplay-policy=no-user-gesture-required",
            "--use-fake-ui-for-media-stream", "--no-first-run",
            "--no-default-browser-check", "--mute-audio"]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
