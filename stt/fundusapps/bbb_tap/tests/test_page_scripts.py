import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const calls = {audio: [], events: [], sources: 0, ctxRate: null};
class FakePC { constructor() { this.l = {}; } addEventListener(t, f) { (this.l[t] = this.l[t] || []).push(f); }
  fire(t, ev) { (this.l[t] || []).forEach(f => f(ev)); } }
FakePC.generateCertificate = () => 'cert';
class FakeCtx { constructor(o) { calls.ctxRate = o.sampleRate; this.state = 'running'; this.destination = {}; }
  createGain() { return {gain: {value: 1}, connect() {}}; }
  createScriptProcessor(n) { const p = {connect() {}}; calls.proc = p; calls.frame = n; return p; }
  createMediaStreamSource() { calls.sources++; return {connect() {}}; }
  resume() { return Promise.resolve(); } }
const win = {RTCPeerConnection: FakePC, AudioContext: FakeCtx,
  MediaStream: class { constructor(t) { this.t = t; } },
  Audio: class { play() { return Promise.resolve(); } },
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  __roboAudio: (b) => calls.audio.push(b), __roboEvent: (e) => calls.events.push(JSON.parse(e))};
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win); // installing twice is a no-op
const pc = new win.RTCPeerConnection({});
const out = {};
out.instanceofKept = pc instanceof FakePC;
out.staticKept = win.RTCPeerConnection.generateCertificate() === 'cert';
pc.fire('track', {track: {kind: 'video', id: 'v1'}});
out.sourcesAfterVideo = calls.sources;
pc.fire('track', {track: {kind: 'audio', id: 'a1'}});
pc.fire('track', {track: {kind: 'audio', id: 'a1'}});
out.sourcesAfterAudio = calls.sources;
out.ctxRate = calls.ctxRate; out.frame = calls.frame;
const f = new Float32Array(calls.frame); f[0] = 1.5; f[1] = -1.5; f[2] = 0.5;
calls.proc.onaudioprocess({inputBuffer: {getChannelData: () => f}});
const bytes = Buffer.from(calls.audio[0], 'base64');
out.chunkBytes = bytes.length;
out.first = [bytes.readInt16LE(0), bytes.readInt16LE(2), bytes.readInt16LE(4)];
out.trackEvents = calls.events.filter(e => e.kind === 'track').length;
console.log(JSON.stringify(out));
"""


def test_hook_taps_remote_audio_once_at_16k(tmp_path):
    h = tmp_path / "h.js"
    h.write_text(HARNESS)
    r = subprocess.run([NODE, str(h), str(HERE / "hook.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["instanceofKept"] and out["staticKept"]
    assert out["sourcesAfterVideo"] == 0
    assert out["sourcesAfterAudio"] == 1          # same track id twice -> one source
    assert out["ctxRate"] == 16000 and out["frame"] == 2048
    assert out["chunkBytes"] == 4096
    assert out["first"] == [32767, -32768, 16384]
    assert out["trackEvents"] == 1


def test_join_script_defines_the_step_function():
    src = (HERE / "join.js").read_text()
    assert "__roboJoinStep" in src
    # The join URL must never be echoed back to Python (it grants access).
    assert "location.href" not in src


LATCH_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
class FakeElement {
  constructor() { this.offsetWidth = 10; this.offsetHeight = 10; this.clicks = 0; }
  click() { this.clicks++; }
  getClientRects() { return [1]; }
  getAttribute() { return null; }
}
// A fake DOM with exactly one visible control (the listen-only button) and
// nothing else, so __roboJoinStep always takes the "listen" branch -- this
// harness targets the click latch, not the full step sequence.
const listenEl = new FakeElement();
let fakeNow = 1000000;
const win = {
  document: {
    querySelector: function () { return listenEl; },
    querySelectorAll: function () { return []; }
  },
  Date: { now: function () { return fakeNow; } }
};
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win);
const out = {};
out.step1 = win.__roboJoinStep('Robo');
out.clicksAfter1 = listenEl.clicks;
out.step2 = win.__roboJoinStep('Robo');            // polled ~1s later: still latched
out.clicksAfter2 = listenEl.clicks;
fakeNow += 5001;                                    // advance past the 5s latch
out.step3 = win.__roboJoinStep('Robo');
out.clicksAfter3 = listenEl.clicks;
console.log(JSON.stringify(out));
"""


def test_join_script_latches_a_click_for_a_few_seconds(tmp_path):
    h = tmp_path / "latch.js"
    h.write_text(LATCH_HARNESS)
    r = subprocess.run([NODE, str(h), str(HERE / "join.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["step1"] == "listen" and out["clicksAfter1"] == 1
    # Polling again before the latch expires must not click the same
    # element a second time while the page is still transitioning.
    assert out["step2"] == "waiting" and out["clicksAfter2"] == 1
    # Once the latch window has passed, a still-visible control is fair
    # game again (e.g. it genuinely needs a second click).
    assert out["step3"] == "listen" and out["clicksAfter3"] == 2


# Measured live (Senfcall, BBB 3.0, headless Chrome 120): the tap's
# AudioContext is created from a 'track' event, which carries no user
# activation, so Chrome's autoplay policy leaves it 'suspended' -- tracks=1
# but the ScriptProcessor never fires (chunks stayed 0 for 80 s). The page
# must expose a resume() Python can call under a CDP user gesture, and the
# join step must not report 'listening' until the context actually runs.
RESUME_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
let resumes = 0;
class FakeCtx { constructor(o) { this.state = 'suspended'; this.destination = {}; }
  createGain() { return {gain: {value: 1}, connect() {}}; }
  createScriptProcessor(n) { return {connect() {}}; }
  createMediaStreamSource() { return {connect() {}}; }
  // Like Chrome: the state flips asynchronously, and only for a resume()
  // made under a user gesture (here: the second call from a join step).
  resume() { resumes++; const self = this;
    return Promise.resolve().then(() => { if (resumes >= 2) { self.state = 'running'; } }); } }
class FakePC { constructor() { this.l = []; } addEventListener(t, f) { this.l.push(f); } }
const win = {RTCPeerConnection: FakePC, AudioContext: FakeCtx,
  MediaStream: class { constructor(t) { this.t = t; } },
  Audio: class { play() { return Promise.resolve(); } },
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  __roboEvent: () => {},
  document: { querySelector: () => null, querySelectorAll: () => [] },
  Date: { now: () => 0 }};
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), win);
(async function () {
  const tick = () => new Promise((r) => setTimeout(r, 0));   // one poll interval
  const out = {};
  out.before = win.__roboJoinStep('Robo');             // no track yet
  const pc = new win.RTCPeerConnection({});
  pc.l[0]({track: {kind: 'audio', id: 'a1'}});         // resume #1 from tapTrack: no effect
  await tick();
  out.first = win.__roboJoinStep('Robo');              // resume #2 issued, not settled yet
  await tick();
  out.second = win.__roboJoinStep('Robo');             // now running
  out.ctx = win.__roboTap.stats().ctx;
  console.log(JSON.stringify(out));
})();
"""


def test_join_step_resumes_a_suspended_tap_before_listening(tmp_path):
    h = tmp_path / "resume.js"
    h.write_text(RESUME_HARNESS)
    r = subprocess.run([NODE, str(h), str(HERE / "hook.js"), str(HERE / "join.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["before"] == "waiting"
    assert out["first"] == "resuming"
    assert out["second"] == "listening"
    assert out["ctx"] == "running"


# Robo removed from the room: the remote tracks end, but the ScriptProcessor
# keeps firing with zeros, so the chunk counter keeps growing. stats().live
# counts tapped tracks whose readyState is still 'live'; Python rejoins when
# it stays 0.
LIVE_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
class FakeCtx { constructor(o) { this.state = 'running'; this.destination = {}; }
  createGain() { return {gain: {value: 1}, connect() {}}; }
  createScriptProcessor(n) { return {connect() {}}; }
  createMediaStreamSource() { return {connect() {}}; }
  resume() { return Promise.resolve(); } }
class FakePC { constructor() { this.l = []; } addEventListener(t, f) { this.l.push(f); } }
const win = {RTCPeerConnection: FakePC, AudioContext: FakeCtx,
  MediaStream: class { constructor(t) { this.t = t; } },
  Audio: class { play() { return Promise.resolve(); } },
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'), __roboEvent: () => {}};
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win);
const out = {};
out.none = win.__roboTap.stats().live;
const pc = new win.RTCPeerConnection({});
const a = {kind: 'audio', id: 'a1', readyState: 'live'};
const b = {kind: 'audio', id: 'a2', readyState: 'live'};
const v = {kind: 'video', id: 'v1', readyState: 'live'};
pc.l[0]({track: a}); pc.l[0]({track: b}); pc.l[0]({track: v});
out.two = win.__roboTap.stats().live;
a.readyState = 'ended';
out.one = win.__roboTap.stats().live;
b.readyState = 'ended';
out.zero = win.__roboTap.stats().live;
out.tracks = win.__roboTap.stats().tracks;
console.log(JSON.stringify(out));
"""


def test_hook_stats_count_live_tapped_tracks(tmp_path):
    h = tmp_path / "live.js"
    h.write_text(LIVE_HARNESS)
    r = subprocess.run([NODE, str(h), str(HERE / "hook.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"none": 0, "two": 2, "one": 1, "zero": 0, "tracks": 2}


# join.js's __roboPageError runs the regex-detection fallback entirely in
# browser JS (BODY_ERROR_RE). Every other page-error test in this repo
# (test_tap_cli.py) uses a Python FakeCdp* class that returns the error
# string directly for the __roboPageError expression -- it never actually
# runs join.js's pageErrorText()/BODY_ERROR_RE in Node. This harness closes
# that gap: it loads the real join.js into a Node vm context with a fake
# DOM that has no alert/banner element (BBB's connection-limit error page
# has no app shell), sets document.body.innerText to the body text passed
# as argv[3], and asserts what __roboPageError() returns.
PAGE_ERROR_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const win = {
  document: {
    querySelector: function () { return null; },     // no alert/banner element
    querySelectorAll: function () { return []; },
    body: { innerText: process.argv[3] }
  }
};
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win);
console.log(JSON.stringify({ result: win.__roboPageError() }));
"""


def _run_page_error(tmp_path, body_text):
    h = tmp_path / "page_error.js"
    h.write_text(PAGE_ERROR_HARNESS)
    r = subprocess.run([NODE, str(h), str(HERE / "join.js"), body_text],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["result"]


def test_join_script_reports_the_connection_limit_error_page(tmp_path):
    # BBB's actual connection-limit error page: no app shell, just this text.
    body = "limit of server connections exceeded"
    assert _run_page_error(tmp_path, body) == body


def test_join_script_does_not_flag_the_normal_join_screen_as_an_error(tmp_path):
    # False-positive guard described at join.js:44-49: a plain join screen
    # with no error pattern in its body text must report no error.
    body = "Enter your name to join the meeting\nJoin"
    assert _run_page_error(tmp_path, body) == ""
