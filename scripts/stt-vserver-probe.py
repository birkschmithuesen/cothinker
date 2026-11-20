#!/usr/bin/env python3
# CoThinker — live proof for the vServer STT path: synthetic TTS in, latency out.
# Copyright (C) 2026 Birk Schmithuesen — AGPL-3.0-only, see LICENSE.
"""Feed a synthetic TTS WAV through the STT server and measure latency.

    python3 scripts/stt-vserver-probe.py --mode feed --wav var/probe/tts.wav --out var/probe/result-feed.json
    python3 scripts/stt-vserver-probe.py --mode bbb --wav var/probe/tts.wav \\
        --cdp http://127.0.0.1:9223 --room-url-file var/probe/room_url --out var/probe/result-bbb.json

The WAV (16 kHz mono int16) comes with a sidecar JSON (default: same name,
`.json`) listing each sentence's `start`/`end` offset in seconds and one
synthetic `keyword` per sentence.

  feed  streams the WAV in real time into the server's `tcp:` input, like
        `fundusapps.bbb_tap.feed_wav` does. Sentence end (wall) =
        feed_start + lead_silence + end.
  bbb   opens a speaker tab in a running browser (CDP), joins the room whose
        URL is in --room-url-file with a fake microphone that plays the WAV,
        and records Date.now() at source.start() as play_start. Sentence end
        (wall) = play_start + end. The STT server and the tap must already
        run (scripts/stt-vserver.sh --cdp URL).

Both modes read `final` events from GET /events. For each final:
latency_s = arrival - end of the sentence whose keyword it contains,
processing_s = arrival - (audio_epoch + t_end) when the timestamps are there,
t_end_minus_sentence_end_s = (audio_epoch + t_end) - sentence end (VAD tail
plus, in bbb mode, the room's transport delay). latency_nearest_s is the
naive rule (nearest sentence end at or before arrival), for comparison only.

Caveat, measured 2026-09-26: the `tcp:` input does not re-anchor
`audio_epoch` when a new client connects after a pause, so a second feed
run against the same server shows processing_s off by the length of the
pause (latency_s is unaffected). Restart the server between feed runs.

Prints numbers only. Transcript text is held in memory for the keyword
count and never printed or written; the room URL is never printed either.
Runs on system python3 (3.9) with `websocket-client` for the bbb mode.
"""
import argparse
import base64
import json
import os
import socket
import statistics
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path
from urllib.parse import urlsplit

BLOCK = 2048  # samples per send, 128 ms at 16 kHz — the tap's chunk size


def log(**kw):
    print(json.dumps(kw), flush=True)


class FinalCollector(threading.Thread):
    """Reads the SSE stream and keeps the final events with their arrival time."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self.finals = []
        self.connected = threading.Event()
        self.error = None

    def run(self):
        try:
            resp = urllib.request.urlopen(self.url, timeout=3600)
            self.connected.set()
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                arrival = time.time()
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev.get("type") == "final":
                    ev["_arrival"] = arrival
                    self.finals.append(ev)
        except Exception as e:  # reported as a count/kind, never as content
            self.error = type(e).__name__
            self.connected.set()


def read_wav(path):
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise SystemExit("need a 16 kHz mono 16-bit WAV")
        return w.readframes(w.getnframes())


def parse_tcp(dev):
    if not dev.startswith("tcp:"):
        raise SystemExit("--pcm must be tcp:HOST:PORT")
    host, _, port = dev[4:].rpartition(":")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("--pcm must be a loopback address")
    return host, int(port)


def run_feed(a, pcm):
    """Stream lead silence + WAV in real time; return the wall time of offset 0."""
    host, port = parse_tcp(a.pcm)
    sock = None
    for _ in range(50):
        try:
            sock = socket.create_connection((host, port), timeout=5)
            break
        except OSError:
            time.sleep(0.2)
    if sock is None:
        raise SystemExit("PCM input %s not reachable" % a.pcm)
    data = b"\x00\x00" * int(16000 * a.lead_silence) + pcm
    step = BLOCK * 2
    t0 = time.monotonic()
    feed_start = time.time()
    log(event="feed_start", wall=feed_start, lead_silence=a.lead_silence)
    for i in range(0, len(data), step):
        delay = t0 + i / 2 / 16000 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        sock.sendall(data[i:i + step])
    # Trailing silence so the VAD sees the last sentence end.
    for _ in range(int(2.0 * 16000 / BLOCK)):
        time.sleep(BLOCK / 16000)
        sock.sendall(b"\x00\x00" * BLOCK)
    log(event="feed_end", wall=time.time())
    return sock, feed_start + a.lead_silence


# ---------------------------------------------------------------- bbb mode

MIC_JS = r"""(function (b64) {
  var w = window;
  if (w.__probeMic) { return 'already'; }
  var bin = atob(b64), n = bin.length >> 1, f = new Float32Array(n);
  for (var i = 0; i < n; i++) {
    var v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
    if (v >= 32768) { v -= 65536; }
    f[i] = v / 32768;
  }
  var ctx = new w.AudioContext();
  var buf = ctx.createBuffer(1, n, 16000);
  buf.copyToChannel(f, 0);
  var dest = ctx.createMediaStreamDestination();
  w.__probeMic = { ctx: ctx, buf: buf, dest: dest, calls: 0 };
  var md = navigator.mediaDevices;
  var origGum = md.getUserMedia.bind(md);
  md.getUserMedia = function (c) {
    w.__probeMic.calls++;
    try { ctx.resume(); } catch (e) {}
    if (c && c.audio) {
      var tracks = dest.stream.getAudioTracks().map(function (t) { return t.clone(); });
      if (c.video) {
        return origGum({ video: c.video }).then(function (v) {
          return new MediaStream(tracks.concat(v.getVideoTracks()));
        });
      }
      return Promise.resolve(new MediaStream(tracks));
    }
    return origGum(c);
  };
  var origEnum = md.enumerateDevices.bind(md);
  md.enumerateDevices = function () {
    return origEnum().then(function (list) {
      if (list.some(function (d) { return d.kind === 'audioinput' && d.deviceId; })) { return list; }
      var d = { kind: 'audioinput', deviceId: 'probe-tts', groupId: 'probe', label: 'Probe TTS' };
      d.toJSON = function () { return { kind: d.kind, deviceId: d.deviceId, groupId: d.groupId, label: d.label }; };
      return list.concat([d]);
    });
  };
  return 'installed';
})(%s)"""

PLAY_JS = r"""(function () {
  var m = window.__probeMic;
  return m.ctx.resume().then(function () {
    var s = m.ctx.createBufferSource();
    s.buffer = m.buf; s.connect(m.dest); s.start();
    return Date.now();
  });
})()"""

# One step per call; returns the step name. Consent -> name -> audio modal.
JOIN_STEP_JS = r"""(function (name) {
  if (document.querySelector('[data-test=microphoneBtn],[data-test=listenOnlyBtn]')) { return 'modal'; }
  if (document.querySelector('[data-test=unmuteMicButton],[data-test=muteMicButton]')) { return 'joined'; }
  var yes = Array.from(document.querySelectorAll('button.yes')).filter(function (e) { return e.offsetWidth; })[0];
  if (yes) { yes.click(); return 'consent'; }
  var i = document.getElementById('name');
  if (i && !window.__probeNamed) {
    window.__probeNamed = 1; i.value = name;
    i.form.querySelector('input[type=submit]').click(); return 'name';
  }
  return 'waiting';
})(%s)"""

AUDIO_STEP_JS = r"""(function () {
  if (document.querySelector('[data-test=muteMicButton]')) { return 'unmuted'; }
  var u = document.querySelector('[data-test=unmuteMicButton]');
  if (u) { var c = document.querySelector('[data-test=closeModal]'); if (c) { c.click(); } u.click(); return ''; }
  var y = document.querySelector('[data-test=echoYesBtn]'); if (y) { y.click(); return ''; }
  var mb = document.querySelector('[data-test=microphoneBtn]');
  if (mb && !window.__probeMicClicked) { window.__probeMicClicked = 1; mb.click(); return ''; }
  var b = document.querySelector('[data-test=joinEchoTestButton]'), now = Date.now();
  if (b && !b.disabled && (!window.__probeJt || now - window.__probeJt > 3000)) { window.__probeJt = now; b.click(); }
  return '';
})()"""


class Cdp:
    def __init__(self, base):
        import websocket  # websocket-client; only the bbb mode needs it
        self.base = base.rstrip("/")
        req = urllib.request.Request(self.base + "/json/new?about:blank", method="PUT")
        t = json.load(urllib.request.urlopen(req, timeout=10))
        self.id = t["id"]
        try:
            self.ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=60,
                                                  suppress_origin=True)
        except BaseException:
            # The target from /json/new above was created before the websocket
            # connect was attempted; on any failure here it would otherwise
            # leak as a blank tab in the shared broker.
            try:
                urllib.request.urlopen(self.base + "/json/close/" + self.id, timeout=10).read()
            except Exception:
                pass
            raise
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                if "error" in r:
                    # Never include params in the message: they can carry a
                    # room/navigate URL, and this tool prints numbers only.
                    raise RuntimeError("%s: %s" % (method, r["error"].get("message")))
                return r

    def ev(self, js):
        r = self.call("Runtime.evaluate", {"expression": js, "returnByValue": True,
                                           "awaitPromise": True, "userGesture": True})
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return None
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        finally:
            urllib.request.urlopen(self.base + "/json/close/" + self.id, timeout=10).read()


def wait_for(fn, timeout, what):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(1)
    raise SystemExit("timeout waiting for " + what)


def run_bbb(a, pcm):
    """Join as speaker with the WAV as microphone; returns (tab, play_start wall)."""
    url = Path(a.room_url_file).read_text().strip()
    tab = Cdp(a.cdp)
    log(event="speaker_tab", target=tab.id, host=urlsplit(url).hostname)
    try:
        tab.call("Page.enable")
        tab.call("Page.navigate", {"url": url})
        step = JOIN_STEP_JS % json.dumps(a.speaker_name)
        last = None
        t0 = time.monotonic()
        while True:
            s = tab.ev(step) or "waiting"
            if s != last:
                log(event="join_step", step=s, s=round(time.monotonic() - t0, 1))
                last = s
            if s in ("modal", "joined"):
                break
            if time.monotonic() - t0 > a.join_timeout:
                raise SystemExit("timeout joining the room")
            time.sleep(1)
        log(event="mic", state=tab.ev(MIC_JS % json.dumps(base64.b64encode(pcm).decode("ascii"))))
        wait_for(lambda: tab.ev(AUDIO_STEP_JS), a.join_timeout, "microphone joined and unmuted")
        log(event="unmuted", s=round(time.monotonic() - t0, 1))
        time.sleep(a.settle)
        before = time.time()
        play_ms = tab.ev(PLAY_JS)
        after = time.time()
        if not play_ms:
            raise SystemExit("playback did not start")
        play_start = play_ms / 1000.0
        log(event="play_start", wall=play_start, cdp_roundtrip_s=round(after - before, 3),
            browser_minus_local_s=round(play_start - (before + after) / 2, 3))
        return tab, play_start
    except BaseException:
        tab.close()
        raise


# ---------------------------------------------------------------- analysis

def analyse(finals, sentences, zero_wall):
    """Per final: which sentence it is (by its synthetic keyword; the last
    matching one if a final spans several), latency from that sentence's end.
    `latency_nearest_s` is the naive rule (nearest sentence end at or before
    arrival); it under-reports as soon as latency exceeds the sentence gap,
    so it is kept only for comparison."""
    ends = sorted(zero_wall + s["end"] for s in sentences)
    nonempty = [f for f in finals if (f.get("text") or "").strip()]
    lat, nearest, proc, tend_err, hits = [], [], [], [], set()
    with_ts = 0
    for f in nonempty:
        arrival = f["_arrival"]
        prior = [e for e in ends if e <= arrival]
        if prior:
            nearest.append(round(arrival - prior[-1], 3))
        text = f["text"].lower()
        matched = [i for i, s in enumerate(sentences) if s["keyword"].lower() in text]
        hits.update(matched)
        if matched:
            lat.append(round(arrival - (zero_wall + sentences[matched[-1]]["end"]), 3))
        if f.get("t_start") is not None and f.get("t_end") is not None:
            with_ts += 1
            if f.get("audio_epoch") is not None:
                speech_end = f["audio_epoch"] + f["t_end"]
                proc.append(round(arrival - speech_end, 3))
                if matched:
                    tend_err.append(round(speech_end - (zero_wall + sentences[matched[-1]]["end"]), 3))

    def summary(xs):
        return {"median": round(statistics.median(xs), 3) if xs else None,
                "max": max(xs) if xs else None}

    return {
        "n_sentences": len(sentences),
        "n_finals": len(nonempty),
        "n_finals_empty": len(finals) - len(nonempty),
        "finals_with_t_start_t_end": with_ts,
        "keyword_hits": len(hits),
        "latency_s": lat,
        "median": summary(lat)["median"],
        "max": summary(lat)["max"],
        "latency_nearest_s": nearest,
        "t_end_minus_sentence_end_s": tend_err,
        "processing_s": proc,
        "processing_median": summary(proc)["median"],
        "processing_max": summary(proc)["max"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("feed", "bbb"), required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--meta", help="sentence offsets JSON (default: WAV name with .json)")
    ap.add_argument("--stt", default="http://127.0.0.1:%s" % os.environ.get("STT_PORT", "5051"))
    ap.add_argument("--pcm", default="tcp:127.0.0.1:%s" % os.environ.get("STT_PCM_PORT", "5053"))
    ap.add_argument("--lead-silence", dest="lead_silence", type=float, default=1.0)
    ap.add_argument("--cdp", default="http://127.0.0.1:9223")
    ap.add_argument("--room-url-file", dest="room_url_file")
    ap.add_argument("--speaker-name", dest="speaker_name", default="Probe-Sprecher")
    ap.add_argument("--join-timeout", dest="join_timeout", type=float, default=90.0)
    ap.add_argument("--settle", type=float, default=3.0, help="seconds between unmute and playback")
    ap.add_argument("--tail", type=float, default=20.0, help="seconds to wait for finals after the audio")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.mode == "bbb" and not a.room_url_file:
        ap.error("--mode bbb needs --room-url-file")

    pcm = read_wav(a.wav)
    meta = json.loads(Path(a.meta or str(Path(a.wav).with_suffix(".json"))).read_text())
    sentences = meta["sentences"]
    duration = len(pcm) / 2 / 16000

    col = FinalCollector(a.stt.rstrip("/") + "/events")
    col.start()
    # The server sends the response headers only with the first event or
    # its 15 s keep-alive, so the subscription can take that long to show.
    if not col.connected.wait(20) or col.error:
        raise SystemExit("SSE /events not reachable (%s)" % (col.error or "timeout"))

    keep = None
    try:
        if a.mode == "feed":
            keep, zero = run_feed(a, pcm)
            deadline = time.time() + a.tail
        else:
            keep, zero = run_bbb(a, pcm)
            deadline = zero + duration + 2.0 + a.tail
        last_end = zero + sentences[-1]["end"]
        while time.time() < deadline:
            time.sleep(0.5)
            # Stop early 8 s after the first final that arrives after the last sentence.
            late = [f["_arrival"] for f in col.finals if f["_arrival"] >= last_end]
            if late:
                deadline = min(deadline, late[0] + 8.0)

        res = {"mode": a.mode, "wav_duration_s": round(duration, 3)}
        res.update(analyse(col.finals, sentences, zero))
        out = json.dumps(res)
        print(out, flush=True)
        if a.out:
            Path(a.out).write_text(out + "\n")
        return 0 if res["n_finals"] else 1
    finally:
        # However the post-join phase ends — normal completion, an exception
        # in the wait loop or analyse(), or Ctrl-C — the speaker tab (bbb) or
        # socket (feed) must not outlive this process with its mic live.
        if keep is not None:
            try:
                keep.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
