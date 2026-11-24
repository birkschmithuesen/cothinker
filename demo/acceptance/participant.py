#!/usr/bin/env python3
"""Synthetic participant: headless Chrome that opens the demo link and
plays a WAV into the meeting as its microphone (CDP via websocket-client).

usage: participant.py --url-file FILE --name Alex --wav alex.wav --seconds 300
         [--screenshot out.png [--screenshot-after 20]]
         [--ignore-certificate-errors] [--host-resolver-rules "MAP a b"]
         [--cookie-path /meeting/] --cdp-port 9301 [--chrome google-chrome]
         [--stats-out stats.json]

The demo URL (it holds the token) is read from --url-file and never
printed. The cookie ct_demo_name is set before navigation so the page
skips its name form. The WAV starts playing when the page grabs the
microphone at join, so start all participants at the same moment.

--stats-out writes the raw `RTCPeerConnection.getStats()` entries of
every peer connection the page creates (captured via an init script
installed before navigation) as a JSON array, once at least one
connection exists -- proof of which transport actually carried the
call's media (demo/acceptance/e2e.py's media path check, issue #153
point 4).
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websocket  # websocket-client

# Installed before navigation (Page.addScriptToEvaluateOnNewDocument) so it
# runs before any page script, including Jitsi's: every RTCPeerConnection
# the page creates is captured into window.__pcs for later getStats() reads.
CAPTURE_PEER_CONNECTIONS_SCRIPT = """
(() => {
  window.__pcs = [];
  const Real = window.RTCPeerConnection;
  window.RTCPeerConnection = function(...args) {
    const pc = new Real(...args);
    window.__pcs.push(pc);
    return pc;
  };
  window.RTCPeerConnection.prototype = Real.prototype;
})();
"""

GATHER_STATS_SCRIPT = """
(async () => {
  const out = [];
  for (const pc of (window.__pcs || [])) {
    const stats = await pc.getStats();
    for (const entry of stats.values()) out.push(entry);
  }
  return JSON.stringify(out);
})()
"""


class Cdp:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.n = 0
        # call() only correlates responses by id; an unsolicited event
        # (no "id") arriving while waiting on some other call's response
        # would otherwise be silently dropped. Stashed here instead, so
        # find_iframe_context() can look back through them.
        self.events: list[dict] = []

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method,
                                 "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def eval(self, expr: str, await_promise: bool = False,
              context_id: int | None = None):
        params = {"expression": expr, "returnByValue": True,
                  "awaitPromise": await_promise}
        if context_id is not None:
            params["contextId"] = context_id
        r = self.call("Runtime.evaluate", **params)
        return r.get("result", {}).get("value")

    def find_iframe_context(self, frame_id: str) -> int | None:
        """The execution context id of the given frame's default world
        (Runtime.executionContextCreated, seen since Runtime.enable()).
        A page's own window and its iframe's window are separate contexts
        -- RTCPeerConnection.getStats() must run in the iframe's, since
        that is where Jitsi actually creates the connection."""
        for ev in self.events:
            if ev.get("method") != "Runtime.executionContextCreated":
                continue
            ctx = ev["params"]["context"]
            if ctx.get("auxData", {}).get("frameId") == frame_id:
                return ctx["id"]
        return None


def wait_cdp(port: int, timeout: float = 30.0) -> str:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/list" % port, timeout=2) as r:
                pages = [p for p in json.load(r) if p.get("type") == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit("Chrome CDP did not come up")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--screenshot")
    ap.add_argument("--screenshot-after", type=float, default=20)
    ap.add_argument("--ignore-certificate-errors", action="store_true")
    ap.add_argument("--host-resolver-rules")
    # ADR 0020/0021: the call-only page is /meeting/ (was /demo/).
    ap.add_argument("--cookie-path", default="/meeting/")
    ap.add_argument("--cdp-port", type=int, required=True,
                    help="remote-debugging port, distinct per participant")
    ap.add_argument("--chrome", default="google-chrome")
    ap.add_argument("--join-timeout", type=float, default=90)
    ap.add_argument("--stats-out")
    ap.add_argument("--stats-timeout", type=float, default=15)
    return ap


def main(argv: list[str]) -> int:
    a = build_arg_parser().parse_args(argv)

    with open(a.url_file, encoding="utf-8") as fh:
        url = fh.read().strip()
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))

    # ignore_cleanup_errors: Chrome can still hold a lock file in its
    # profile's Default/ dir for a moment after chrome.wait() returns,
    # which turned a successful join into a crash (ENOTEMPTY) on exit --
    # found live. A leftover /tmp profile dir is harmless.
    with tempfile.TemporaryDirectory(prefix="ct-participant-",
                                      ignore_cleanup_errors=True) as profile:
        cmd = [a.chrome, "--headless=new", "--window-size=1920,1080",
               "--remote-debugging-port=%d" % a.cdp_port,
               "--user-data-dir=" + profile, "--no-first-run",
               "--no-default-browser-check", "--no-sandbox",
               "--use-fake-ui-for-media-stream",
               "--use-fake-device-for-media-stream",
               "--use-file-for-fake-audio-capture=%s%%noloop" % a.wav,
               "--autoplay-policy=no-user-gesture-required", "about:blank"]
        if a.ignore_certificate_errors:
            cmd.append("--ignore-certificate-errors")
        if a.host_resolver_rules:
            cmd.append("--host-resolver-rules=" + a.host_resolver_rules)
        chrome = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            cdp = Cdp(wait_cdp(a.cdp_port))
            cdp.call("Network.enable")
            cdp.call("Page.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", width=1920,
                     height=1080, deviceScaleFactor=1, mobile=False)
            cdp.call("Network.setCookie", name="ct_demo_name", value=a.name,
                     url=origin, path=a.cookie_path, secure=True,
                     sameSite="Lax")
            if a.stats_out:
                # Runtime.enable before navigate: executionContextCreated
                # events (one per frame, incl. the Jitsi iframe navigated
                # to below) must already be flowing by the time it fires,
                # or find_iframe_context() has nothing to find.
                cdp.call("Runtime.enable")
                cdp.call("Page.addScriptToEvaluateOnNewDocument",
                         source=CAPTURE_PEER_CONNECTIONS_SCRIPT)
            cdp.call("Page.navigate", url=url)
            t0 = time.time()
            while not cdp.eval(
                    "!!document.querySelector('#jitsi iframe')"):
                if time.time() - t0 > a.join_timeout:
                    print("FAIL: meeting iframe did not appear", file=sys.stderr)
                    return 1
                time.sleep(1)
            print("joined name=%s after %.1fs" % (a.name, time.time() - t0))
            if a.screenshot:
                time.sleep(max(0.0, a.screenshot_after))
                shot = cdp.call("Page.captureScreenshot", format="png")
                with open(a.screenshot, "wb") as fh:
                    fh.write(base64.b64decode(shot["data"]))
                print("screenshot written")
            if a.stats_out:
                # The RTCPeerConnection override lives in the iframe's own
                # window (that is where Jitsi actually runs), not the top
                # page's -- window.__pcs must be read from that frame's
                # execution context, found via its frameId. #jitsi has
                # exactly one child frame (the Jitsi iframe).
                tree = cdp.call("Page.getFrameTree")
                children = tree.get("frameTree", {}).get("childFrames", [])
                frame_id = children[0]["frame"]["id"] if children else None
                t1 = time.time()
                ctx_id = None
                while ctx_id is None:
                    ctx_id = cdp.find_iframe_context(frame_id) if frame_id else None
                    if ctx_id is not None:
                        break
                    if time.time() - t1 > a.stats_timeout:
                        print("FAIL: no RTCPeerConnection appeared for stats",
                              file=sys.stderr)
                        return 1
                    time.sleep(0.5)
                while not cdp.eval("(window.__pcs || []).length > 0", context_id=ctx_id):
                    if time.time() - t1 > a.stats_timeout:
                        print("FAIL: no RTCPeerConnection appeared for stats",
                              file=sys.stderr)
                        return 1
                    time.sleep(0.5)
                raw = cdp.eval(GATHER_STATS_SCRIPT, await_promise=True, context_id=ctx_id)
                with open(a.stats_out, "w", encoding="utf-8") as fh:
                    fh.write(raw)
                entries = len(json.loads(raw))
                print("stats written pcs=%d entries=%d"
                      % (cdp.eval("(window.__pcs || []).length", context_id=ctx_id), entries))
            time.sleep(max(0.0, a.seconds - (time.time() - t0)))
            return 0
        finally:
            chrome.terminate()
            try:
                chrome.wait(timeout=10)
            except subprocess.TimeoutExpired:
                chrome.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
