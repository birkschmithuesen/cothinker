#!/usr/bin/env python3
"""Synthetic participant: headless Chrome that opens the demo link and
plays a WAV into the meeting as its microphone (CDP via websocket-client).

usage: participant.py --url-file FILE --name Alex --wav alex.wav --seconds 300
         [--screenshot out.png [--screenshot-after 20]]
         [--ignore-certificate-errors] [--host-resolver-rules "MAP a b"]
         [--cookie-path /demo/] [--cdp-port 9300] [--chrome google-chrome]

The demo URL (it holds the token) is read from --url-file and never
printed. The cookie ct_demo_name is set before navigation so the page
skips its name form. The WAV starts playing when the page grabs the
microphone at join, so start all participants at the same moment.
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


class Cdp:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.n = 0

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

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr,
                      returnByValue=True)
        return r.get("result", {}).get("value")


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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-file", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--screenshot")
    ap.add_argument("--screenshot-after", type=float, default=20)
    ap.add_argument("--ignore-certificate-errors", action="store_true")
    ap.add_argument("--host-resolver-rules")
    ap.add_argument("--cookie-path", default="/demo/")
    ap.add_argument("--cdp-port", type=int, default=9300)
    ap.add_argument("--chrome", default="google-chrome")
    ap.add_argument("--join-timeout", type=float, default=90)
    a = ap.parse_args(argv)

    with open(a.url_file, encoding="utf-8") as fh:
        url = fh.read().strip()
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))

    with tempfile.TemporaryDirectory(prefix="ct-participant-") as profile:
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
