"""Join a BBB room headless as a listen-only participant and tap its audio.

    BBB_JOIN_URL=... python -m fundusapps.bbb_tap [--cdp http://127.0.0.1:9223]

Prefer the BBB_JOIN_URL environment variable over --join-url: a command
line argument is visible to any local user via `ps` or /proc/<pid>/cmdline,
and the join URL grants access to the room. --join-url is kept (additive,
used by tests); passing it on the command line prints a one-line warning
(no URL, see redact_url below) but still works. The join URL itself is
never printed either way -- status lines carry the host only, EXCEPT the
one join_url_resolved line emitted when resolving a meta-refresh/redirect
actually moved the room to a different host (see redact_url_path below):
that one also carries the path, since the room's own BBB URL can put the
access-granting part of the URL in the path rather than the query string,
same as the original join URL can -- logging it only kicks in once we
know the destination is not the original URL's host.
"""
import argparse
import datetime
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from fundusapps.bbb_tap import cdp
from fundusapps.bbb_tap.cdp import resolve_join_url
from fundusapps.bbb_tap.pcm import PcmSink, WavRecorder, decode_chunk

HERE = Path(__file__).resolve().parent


def _resolve_record_time(record_time):
    """`record_time` if given (format YYYYMMDD-HHMMSS, see --record-time),
    else `datetime.now()` -- the timestamp that names the recording and,
    with --per-track, its sidecar. A caller that starts a matching STT
    server first (scripts/stt-vserver.sh) can fix this instead of letting
    this process pick its own after the fact, see --record-time's help."""
    if record_time:
        return datetime.datetime.strptime(record_time, "%Y%m%d-%H%M%S")
    return datetime.datetime.now()


def redact_url(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}" if p.hostname else "<url>"


def redact_url_path(url):
    p = urlsplit(url)
    host = p.hostname or "<url>"
    return f"{host}{p.path or '/'}"


#: Page-side query for the per-track sidecar: which tapped track (tap
#: number n -> MediaStreamTrack id) belongs to which Jitsi participant.
#: Returns plain data or null outside Jitsi; never page text beyond the
#: participant's display name, which goes only into the sidecar file.
TRACK_OWNERS_JS = r"""(function () {
  var t = window.__roboTap;
  if (!t || !t.trackIds) { return null; }
  var out = {ids: t.trackIds(), owners: []};
  try {
    var room = window.APP && APP.conference && APP.conference._room;
    if (room && room.getParticipants) {
      room.getParticipants().forEach(function (p) {
        (p.getTracks ? p.getTracks() : []).forEach(function (jt) {
          if (jt.getType && jt.getType() !== 'audio') { return; }
          var mt = jt.getTrack ? jt.getTrack() : null;
          out.owners.push({track_id: mt ? mt.id : null, participant_id: p.getId(),
                           display_name: p.getDisplayName ? (p.getDisplayName() || '') : ''});
        });
      });
    }
  } catch (e) { out.error = String(e && e.name || 'error'); }
  return out;
})()"""

#: How often the per-track owner map is polled from the page.
OWNERS_EVERY_S = 10.0


def track_owners_request():
    return {"expression": TRACK_OWNERS_JS, "returnByValue": True}


class PerTrack:
    """--per-track: one WAV per remote audio track next to the mixed WAV,
    plus a JSON sidecar mapping track number -> Jitsi participant.

    Track numbers are global for the tap run: a rejoin loads a fresh page
    whose own numbering restarts at 1, so new_page() starts a new epoch and
    page track (epoch, n) gets the next free global number. Each per-track
    WAV is padded with silence up to the mixed WAV's length at the moment
    the track appears, so every file shares the mixed WAV's time axis
    (accurate to one 128 ms chunk)."""

    def __init__(self, base_path, mixed_frames=lambda: 0):
        self.base = Path(base_path)               # .../bbb-HHMMSS (no suffix)
        self.mixed_frames = mixed_frames
        self.sidecar = self.base.parent / (self.base.name + "-tracks.json")
        self.epoch = 0
        self.global_n = {}                        # (epoch, n) -> global number
        self.recorders = {}                       # global number -> WavRecorder
        self.info = {}                            # global number -> sidecar entry
        self.failed = False

    def new_page(self):
        self.epoch += 1

    def _number(self, n):
        key = (self.epoch, n)
        if key not in self.global_n:
            self.global_n[key] = len(self.global_n) + 1
        return self.global_n[key]

    def write(self, n, pcm):
        if self.failed:
            return
        g = self._number(n)
        try:
            rec = self.recorders.get(g)
            if rec is None:
                path = self.base.parent / f"{self.base.name}-trk{g}.wav"
                rec = self.recorders[g] = WavRecorder(path)
                pad = int(self.mixed_frames())
                if pad > 0:
                    rec.write(b"\x00\x00" * pad)
                self.info.setdefault(g, {"file": path.name, "owners": []})
                self.info[g]["start_frame"] = pad
                self.save()
            rec.write(pcm)
        except OSError as e:
            self.failed = True
            status(event="warning", reason="per_track_failed", detail=type(e).__name__)

    def update_owners(self, answer, now=None):
        """`answer` is TRACK_OWNERS_JS's value. Appends an owner entry to a
        track whenever its participant changes (Jitsi may reuse a receive
        track for another speaker). Returns True if the sidecar changed."""
        if not isinstance(answer, dict):
            return False
        by_tid = {o.get("track_id"): o for o in answer.get("owners") or [] if isinstance(o, dict)}
        changed = False
        for item in answer.get("ids") or []:
            if not isinstance(item, dict) or "n" not in item:
                continue
            g = self._number(item["n"])
            entry = self.info.setdefault(g, {"file": f"{self.base.name}-trk{g}.wav", "owners": []})
            if entry.get("track_id") != item.get("id"):
                entry["track_id"] = item.get("id")
                changed = True
            o = by_tid.get(item.get("id"))
            if o is None:
                continue
            last = entry["owners"][-1] if entry["owners"] else None
            cur = {"participant_id": o.get("participant_id"),
                   "display_name": o.get("display_name") or ""}
            if last is None or (last["participant_id"], last["display_name"]) != \
                    (cur["participant_id"], cur["display_name"]):
                cur["since"] = round(now if now is not None else time.time(), 3)
                entry["owners"].append(cur)
                changed = True
        if changed:
            self.save()
        return changed

    def save(self):
        if self.failed:
            return
        doc = {"mixed": self.base.name + ".wav", "sample_rate": 16000,
               "tracks": {str(g): self.info[g] for g in sorted(self.info)}}
        tmp = self.sidecar.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.sidecar)
        except OSError as e:
            self.failed = True
            status(event="warning", reason="per_track_failed", detail=type(e).__name__)

    def close(self):
        for rec in self.recorders.values():
            try:
                rec.close()
            except OSError:
                pass


class Tap:
    """Owns the audio/event counters and fans a CDP binding call out to
    the recorder (everything) and the sink (best effort, see PcmSink)."""

    def __init__(self, sink, recorder, per_track=None):
        self.sink, self.recorder = sink, recorder
        self.per_track = per_track
        self.chunks = self.bytes = self.tracks = 0
        self.last_rms = 0.0
        self.sink_ok = False
        self.recorder_failed = False

    def on_binding(self, params):
        name = params.get("name")
        if name == "__roboTrack":
            if self.per_track is not None:
                n, _, b64 = params.get("payload", "").partition(":")
                if n.isdigit():
                    self.per_track.write(int(n), decode_chunk(b64))
        elif name == "__roboAudio":
            pcm = decode_chunk(params.get("payload", ""))
            if self.recorder is not None:
                try:
                    self.recorder.write(pcm)
                except OSError as e:
                    # A full disk or a vanished record dir must never cost
                    # the live feed: the STT server is what the meeting
                    # reads. Report once, stop recording, keep sending.
                    self.recorder = None
                    self.recorder_failed = True
                    status(event="warning", reason="recorder_failed", detail=type(e).__name__)
            self.sink_ok = self.sink.send(pcm)
            self.chunks += 1
            self.bytes += len(pcm)
            s = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
            self.last_rms = float(np.sqrt(np.mean(s * s)) / 32768.0) if s.size else 0.0
        elif name == "__roboEvent":
            try:
                ev = json.loads(params.get("payload", "{}"))
            except ValueError:
                return
            if ev.get("kind") == "track":
                self.tracks += 1


def join_step_request(name):
    """Runtime.evaluate params for one join step. userGesture matters: the
    tap's AudioContext may only leave 'suspended' under a user activation
    (Chrome autoplay policy), and the join step is where it gets resumed."""
    return {"expression": f"window.__roboJoinStep ? window.__roboJoinStep({json.dumps(name)}) : 'loading'",
            "returnByValue": True, "userGesture": True}


def resume_request():
    """Runtime.evaluate params for nudging a stalled AudioContext. Same
    userGesture reasoning as join_step_request: the browser only lets the
    context leave 'suspended' under a user activation."""
    return {"expression": "window.__roboTap && window.__roboTap.resume()", "userGesture": True}


#: How long the audio may stay stalled -- or the page may hold no live
#: tapped track -- before the tap gives up nudging and joins the room anew.
REJOIN_AFTER_S = 30.0
#: How often, once joined, the page is asked whether a tapped track is
#: still live (window.__roboTap.stats().live).
LIVE_CHECK_EVERY_S = 10.0


class StallWatch:
    """Pure decision logic for the post-join audio watchdog -- no CDP calls
    in here, so it can be unit-tested without a browser.

    Once joined, tap.chunks should keep growing (a chunk every ~128 ms).
    update() returns what the caller should do, or None:

    * 'stall'  -- chunks stopped growing for `stall_after` s: the
      AudioContext likely got suspended again; nudge it (resume).
    * 'retry'  -- still stalled, another `stall_after` s passed: nudge again.
    * 'resume' -- chunks grow again after a stall: report the recovery.
    * 'rejoin' -- no growth for more than `rejoin_after` s, or the page
      reported no live tapped track (`live == 0`) for more than
      `rejoin_after` s: nudging does not help, join the room anew.

    The live check covers the case the chunk counter cannot see: Robo got
    removed from the room, but the ScriptProcessor keeps delivering zeros,
    so chunks keep growing and nothing ever stalls. `live` is passed only
    when the caller actually asked the page (every LIVE_CHECK_EVERY_S);
    None means "no new answer", not "no live track". After 'rejoin' the
    caller starts over with a fresh StallWatch."""

    def __init__(self, stall_after=5.0, rejoin_after=REJOIN_AFTER_S):
        self.stall_after = stall_after
        self.rejoin_after = rejoin_after
        self.last_chunks = None
        self.last_growth = None
        self.last_nudge = None
        self.stalled = False
        self.dead_since = None

    def update(self, now, chunks, live=None):
        if live is not None:
            if live > 0:
                self.dead_since = None
            elif self.dead_since is None:
                self.dead_since = now
        if self.dead_since is not None and now - self.dead_since > self.rejoin_after:
            return "rejoin"
        if self.last_chunks is None or chunks != self.last_chunks:
            self.last_chunks = chunks
            self.last_growth = now
            if self.stalled:
                self.stalled = False
                return "resume"
            return None
        if not self.stalled:
            if now - self.last_growth >= self.stall_after:
                self.stalled = True
                self.last_nudge = now
                return "stall"
            return None
        if now - self.last_growth > self.rejoin_after:
            return "rejoin"
        if now - self.last_nudge >= self.stall_after:
            self.last_nudge = now
            return "retry"
        return None


def live_request():
    """Runtime.evaluate params for asking the page how many tapped tracks
    are still live. Returns a number only -- never page text."""
    return {"expression": "window.__roboTap ? window.__roboTap.stats().live : -1",
            "returnByValue": True}


def page_error_request():
    """Runtime.evaluate params for asking the page for its own visible
    error text (join.js: a banner/alert element, or the whole body for a
    bare error page like BBB's). Empty string means no visible error."""
    return {"expression": "window.__roboPageError ? window.__roboPageError() : ''",
            "returnByValue": True}


def status(**kw):
    print(json.dumps(dict(kw, t=round(time.time(), 3))), flush=True)


def backoff_delays(spec):
    return [float(x) for x in spec.split(",") if x.strip()]


def _backoff_for(delays, attempt):
    """`attempt` is the 1-based number of the attempt that just failed."""
    if not delays:
        return 0.0
    return delays[min(attempt - 1, len(delays) - 1)]


def _wait_backoff(stop, seconds):
    """Waits out one retry backoff. Returns True if `stop` fired while
    waiting -- the caller should give up cleanly rather than retry."""
    return stop.wait(seconds) if seconds > 0 else False


def _terminate_browser(browser):
    if browser is None:
        return
    browser.terminate()
    try:
        browser.wait(5)
    except Exception:
        browser.kill()


def run_join_loop(a, c, tap, stop, join_url):
    """Runs the join + post-join stall/rejoin loop on an already-connected
    `c`. Returns (rc, retry_reason):

    * retry_reason is None once no more retries make sense here -- `stop`
      fired, the join timed out, or the websocket closed on its own. `rc`
      is this function's own return value in that case (0 or 1).
    * retry_reason is 'join_page_error' when the page shows a visible
      error before reaching 'listening' (e.g. a "limit of server
      connections exceeded" page). `rc` is None -- the caller tears this
      connection down and starts a fresh attempt with backoff instead of
      treating it as fatal.
    """
    joined, t_join, last_step, last_status = False, time.monotonic(), None, 0.0
    stall_watch, last_live_check = StallWatch(), 0.0
    last_owner_check = 0.0
    while not stop.is_set() and not c.closed.is_set():
        if not joined:
            try:
                r = c.call("Runtime.evaluate", page_error_request(), timeout=10)
                err_text = r.get("result", {}).get("value") or ""
            except (RuntimeError, OSError, cdp.websocket.WebSocketException):
                err_text = ""
            if err_text:
                status(event="error", reason="join_page_error", detail=err_text.strip()[:200])
                return None, "join_page_error"
            try:
                r = c.call("Runtime.evaluate", join_step_request(a.name), timeout=10)
                step = r.get("result", {}).get("value")
            except (RuntimeError, OSError, cdp.websocket.WebSocketException):
                step = "loading"
            if step != last_step:
                status(event="join_step", step=step)
                last_step = step
            joined = step == "listening"
            if joined:
                stall_watch, last_live_check = StallWatch(), time.monotonic()
            elif a.join_timeout > 0 and time.monotonic() - t_join > a.join_timeout:
                status(event="error", reason="join_timeout", step=step)
                return 1, None
        else:
            live = None
            if time.monotonic() - last_live_check >= LIVE_CHECK_EVERY_S:
                last_live_check = time.monotonic()
                try:
                    r = c.call("Runtime.evaluate", live_request(), timeout=10)
                    v = r.get("result", {}).get("value")
                    live = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
                except (RuntimeError, OSError, cdp.websocket.WebSocketException):
                    live = None
            action = stall_watch.update(time.monotonic(), tap.chunks, live)
            if action in ("stall", "retry"):
                if action == "stall":
                    status(event="warning", reason="audio_stalled")
                try:
                    c.call("Runtime.evaluate", resume_request())
                except (RuntimeError, OSError, cdp.websocket.WebSocketException):
                    pass
            elif action == "resume":
                status(event="info", reason="audio_resumed")
            if (tap.per_track is not None
                    and time.monotonic() - last_owner_check >= OWNERS_EVERY_S):
                last_owner_check = time.monotonic()
                try:
                    r = c.call("Runtime.evaluate", track_owners_request(), timeout=10)
                    tap.per_track.update_owners(r.get("result", {}).get("value"))
                except (RuntimeError, OSError, cdp.websocket.WebSocketException):
                    pass
            if action == "rejoin":
                # Nudging did not bring the audio back, or the page holds
                # no live track any more (Robo removed from the room).
                # A fresh navigation gives a fresh document -- hook.js is
                # re-injected with no tapped tracks, so the join step
                # cannot report 'listening' off the dead page's state.
                # From here the join loop and its timeout govern again.
                status(event="warning", reason="rejoin")
                joined, t_join, last_step = False, time.monotonic(), None
                if tap.per_track is not None:
                    tap.per_track.new_page()
                try:
                    c.call("Page.navigate", {"url": join_url})
                except (RuntimeError, OSError, cdp.websocket.WebSocketException):
                    pass
        if time.monotonic() - last_status >= a.status_every:
            status(event="tap", chunks=tap.chunks, tracks=tap.tracks,
                   rms=round(tap.last_rms, 5), sink_connected=tap.sink_ok,
                   dropped_bytes=tap.sink.dropped_bytes)
            last_status = time.monotonic()
        stop.wait(1.0)
    if stop.is_set():
        return 0, None
    # The loop only exits without `stop` set when the websocket closed on
    # its own (c.closed) -- an unexpected disconnect, not a signal. This is
    # not one of the retried failure classes (cdp_setup, join_page_error),
    # so it ends the tap outright, same as before.
    status(event="error", reason="cdp_closed")
    return 1, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--join-url", dest="join_url", default=os.environ.get("BBB_JOIN_URL", ""),
                    help="prefer env var BBB_JOIN_URL instead -- a value passed here is visible "
                         "to local users via ps/proc/<pid>/cmdline")
    ap.add_argument("--name", default="Robo")
    ap.add_argument("--pcm", default="tcp:127.0.0.1:5053")
    ap.add_argument("--record-dir", dest="record_dir",
                    default=os.environ.get("STT_RECORD_DIR", str(Path.home() / "cothinker-audio")))
    ap.add_argument("--no-record", dest="no_record", action="store_true")
    ap.add_argument("--record-time", dest="record_time", default="",
                    help="override the timestamp that names the recording "
                         "(format YYYYMMDD-HHMMSS) instead of computing it "
                         "from datetime.now(). Lets a wrapper script that "
                         "starts a matching STT server first "
                         "(scripts/stt-vserver.sh) predict this run's "
                         "--per-track sidecar path and hand both processes "
                         "the same timestamp, rather than racing this "
                         "process's own choice of now().")
    ap.add_argument("--per-track", dest="per_track", action="store_true",
                    help="also record each remote audio track to its own WAV "
                         "(bbb-HHMMSS-trkN.wav) plus a -tracks.json participant map")
    ap.add_argument("--cdp", default="", help="attach to a running browser, e.g. http://127.0.0.1:9223")
    ap.add_argument("--cdp-fallback", dest="cdp_fallback", default="",
                    help="broker to switch to if the own-chromium path's cdp_setup keeps failing, "
                         "e.g. under memory pressure")
    ap.add_argument("--chromium", default="/usr/bin/chromium")
    ap.add_argument("--cdp-port", dest="cdp_port", type=int, default=9224)
    ap.add_argument("--profile-dir", dest="profile_dir",
                    default=str(Path.home() / ".cache" / "cothinker-bbb-tap"))
    ap.add_argument("--status-every", dest="status_every", type=float, default=10.0)
    ap.add_argument("--join-timeout", dest="join_timeout", type=float, default=120.0,
                    help="seconds to reach 'listening' (also after a rejoin); 0 = no limit")
    ap.add_argument("--join-retries", dest="join_retries", type=int, default=3,
                    help="max join attempts; retried on cdp_setup errors and visible page errors")
    ap.add_argument("--join-backoff", dest="join_backoff", default="5,15,45",
                    help="comma-separated backoff seconds between join attempts")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if any(x == "--join-url" or x.startswith("--join-url=") for x in raw_argv):
        # The URL itself never appears here -- only the fact that the flag
        # was used, which is the leak (ps / /proc/<pid>/cmdline).
        status(event="warning", reason="join_url_on_argv")
    if not a.join_url:
        print("no join URL: pass --join-url or set BBB_JOIN_URL", file=sys.stderr)
        return 2

    resolved_url = resolve_join_url(a.join_url)
    # Reveal the path only once a redirect/meta-refresh actually relocated
    # the room to a different host (e.g. meet.example.org -> room.example.net)
    # -- that is the case the path is useful for ("did it land on the
    # redirect target"). Unresolved or same-host, the path may still carry
    # the original URL's own access secret, so it stays out, same as every
    # other status line (redact_url, host only).
    if urlsplit(resolved_url).hostname != urlsplit(a.join_url).hostname:
        resolved_detail = redact_url_path(resolved_url)
    else:
        resolved_detail = redact_url(resolved_url)
    status(event="info", reason="join_url_resolved", detail=resolved_detail)

    recorder = per_track = None
    if not a.no_record:
        now = _resolve_record_time(a.record_time)
        path = Path(a.record_dir) / now.strftime("%Y-%m-%d") / f"bbb-{now:%H%M%S}.wav"
        recorder = WavRecorder(path)
        status(event="recording", dir=str(path.parent))
        if a.per_track:
            per_track = PerTrack(path.with_suffix(""), mixed_frames=lambda: recorder.frames)
            status(event="recording", per_track=True)
    elif a.per_track:
        status(event="warning", reason="per_track_needs_recording")
    tap = Tap(PcmSink(a.pcm), recorder, per_track)

    backoff = backoff_delays(a.join_backoff)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    browser = None
    using_fallback = False
    try:
        attempt = 0
        while True:
            attempt += 1
            target = None
            owns_browser = False
            if using_fallback:
                http_base = a.cdp_fallback
            elif a.cdp:
                http_base = a.cdp
            else:
                owns_browser = True
                _terminate_browser(browser)
                browser = cdp.launch_chromium(a.chromium, a.cdp_port, a.profile_dir)
                http_base = f"http://127.0.0.1:{a.cdp_port}"
                if not cdp.wait_http(http_base):
                    status(event="error", reason="chromium_no_cdp")
                    return 1
            try:
                target, ws_url = cdp.open_target(http_base)
            except Exception as e:
                status(event="error", reason="cdp_unreachable", detail=type(e).__name__)
                return 1
            try:
                try:
                    # Inside the guard: the websocket connect itself can
                    # fail (the broker drops the fresh target, a
                    # refused/reset socket), and that must end as a status
                    # line, with the target still closed, not a traceback.
                    c = cdp.Cdp(ws_url)
                    c.on("Runtime.bindingCalled", tap.on_binding)
                    c.call("Runtime.enable")
                    c.call("Page.enable")
                    c.call("Runtime.addBinding", {"name": "__roboAudio"})
                    c.call("Runtime.addBinding", {"name": "__roboEvent"})
                    if per_track is not None:
                        c.call("Runtime.addBinding", {"name": "__roboTrack"})
                        c.call("Page.addScriptToEvaluateOnNewDocument",
                               {"source": "window.__roboPerTrack = true;"})
                        per_track.new_page()
                    for f in ("hook.js", "join.js"):
                        c.call("Page.addScriptToEvaluateOnNewDocument",
                               {"source": (HERE / f).read_text(encoding="utf-8")})
                    c.call("Page.navigate", {"url": resolved_url})
                except (RuntimeError, OSError, ValueError, cdp.websocket.WebSocketException) as e:
                    # RuntimeError: Cdp.call()'s own "no answer" /
                    # protocol-error path. OSError/WebSocketException: the
                    # websocket connect, or ws.send() on a dead/closed
                    # socket, or read_text() failing on hook.js/join.js.
                    # ValueError: a malformed ws URL. Measured live under
                    # memory pressure on the own-chromium path -- retried
                    # below instead of ending the tap.
                    status(event="error", reason="cdp_setup", detail=type(e).__name__)
                    if not using_fallback and a.cdp_fallback:
                        using_fallback = True
                        _terminate_browser(browser)
                        browser = None
                        status(event="warning", reason="cdp_fallback")
                    if attempt >= a.join_retries:
                        return 1
                    if _wait_backoff(stop, _backoff_for(backoff, attempt)):
                        return 0
                    continue
                status(event="navigate", host=redact_url(resolved_url), name=a.name)
                rc, retry_reason = run_join_loop(a, c, tap, stop, resolved_url)
            finally:
                if target is not None and not owns_browser:
                    cdp.close_target(http_base, target)      # never leave tabs in a shared broker
            if retry_reason is None:
                return rc
            if attempt >= a.join_retries:
                return 1
            if _wait_backoff(stop, _backoff_for(backoff, attempt)):
                return 0
    finally:
        _terminate_browser(browser)
        tap.sink.close()
        if per_track is not None:
            per_track.save()
            per_track.close()
        if recorder is not None:
            try:
                recorder.close()
            except OSError:
                pass            # already reported as recorder_failed


if __name__ == "__main__":
    sys.exit(main())
