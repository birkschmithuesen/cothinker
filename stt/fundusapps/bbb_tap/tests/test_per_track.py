"""--per-track: one WAV per remote audio track + a participant sidecar."""
import base64
import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from fundusapps.bbb_tap.__main__ import (PerTrack, Tap, TRACK_OWNERS_JS, main,
                                         track_owners_request)
from fundusapps.bbb_tap.pcm import WavRecorder

HERE = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


class FakeSink:
    dropped_bytes = 0

    def __init__(self):
        self.sent = []

    def send(self, pcm):
        self.sent.append(pcm)
        return True


def _b64(pcm):
    return base64.b64encode(pcm).decode()


def _frames(path):
    with wave.open(str(path)) as w:
        return w.getnframes(), w.readframes(w.getnframes())


def test_track_chunks_go_to_their_own_wav_and_never_into_the_mix(tmp_path):
    rec = WavRecorder(tmp_path / "bbb-120000.wav")
    pt = PerTrack(tmp_path / "bbb-120000", mixed_frames=lambda: rec.frames)
    pt.new_page()
    sink = FakeSink()
    tap = Tap(sink, rec, pt)
    mixed = b"\x01\x00" * 2048
    tap.on_binding({"name": "__roboAudio", "payload": _b64(mixed)})
    tap.on_binding({"name": "__roboTrack", "payload": "1:" + _b64(b"\x02\x00" * 2048)})
    tap.on_binding({"name": "__roboTrack", "payload": "2:" + _b64(b"\x03\x00" * 2048)})
    tap.on_binding({"name": "__roboTrack", "payload": "garbage"})
    pt.close(); rec.close()
    # Mixed WAV and PCM stream are exactly as without --per-track.
    assert sink.sent == [mixed]
    assert _frames(tmp_path / "bbb-120000.wav") == (2048, mixed)
    n1, d1 = _frames(tmp_path / "bbb-120000-trk1.wav")
    n2, d2 = _frames(tmp_path / "bbb-120000-trk2.wav")
    # Each track is padded to the mixed WAV's length when it first appears.
    assert (n1, n2) == (4096, 4096)
    assert d1 == b"\x00\x00" * 2048 + b"\x02\x00" * 2048
    assert d2 == b"\x00\x00" * 2048 + b"\x03\x00" * 2048
    side = json.loads((tmp_path / "bbb-120000-tracks.json").read_text())
    assert side["mixed"] == "bbb-120000.wav"
    assert side["tracks"]["1"]["start_frame"] == 2048


def test_without_per_track_the_track_binding_is_ignored(tmp_path):
    tap = Tap(FakeSink(), None, None)
    tap.on_binding({"name": "__roboTrack", "payload": "1:" + _b64(b"\x02\x00" * 8)})
    assert list(tmp_path.iterdir()) == []


def test_owner_map_follows_participants_and_records_changes(tmp_path):
    pt = PerTrack(tmp_path / "bbb-1")
    pt.new_page()
    ans = {"ids": [{"n": 1, "id": "ta"}, {"n": 2, "id": "tb"}],
           "owners": [{"track_id": "ta", "participant_id": "p1", "display_name": "Anna"},
                      {"track_id": "tb", "participant_id": "p2", "display_name": "Ben"}]}
    assert pt.update_owners(ans, now=10.0) is True
    assert pt.update_owners(ans, now=20.0) is False          # nothing new
    ans["owners"][1]["display_name"] = "Ben B."
    assert pt.update_owners(ans, now=30.0) is True
    side = json.loads((tmp_path / "bbb-1-tracks.json").read_text())
    t1, t2 = side["tracks"]["1"], side["tracks"]["2"]
    assert t1["track_id"] == "ta" and [o["display_name"] for o in t1["owners"]] == ["Anna"]
    assert [o["display_name"] for o in t2["owners"]] == ["Ben", "Ben B."]
    assert t2["owners"][-1] == {"participant_id": "p2", "display_name": "Ben B.", "since": 30.0}
    assert pt.update_owners(None) is False                   # outside Jitsi: no-op


def test_a_rejoined_page_gets_fresh_global_track_numbers(tmp_path):
    pt = PerTrack(tmp_path / "bbb-2")
    pt.new_page()
    pt.write(1, b"\x01\x00" * 4)
    pt.new_page()                    # rejoin: the page numbers from 1 again
    pt.write(1, b"\x02\x00" * 4)
    pt.close()
    assert (tmp_path / "bbb-2-trk1.wav").exists() and (tmp_path / "bbb-2-trk2.wav").exists()


def test_owner_query_returns_data_only():
    p = track_owners_request()
    assert p["returnByValue"] is True
    assert "location" not in TRACK_OWNERS_JS


def test_per_track_flag_without_recording_warns(monkeypatch, tmp_path, capsys):
    from fundusapps.bbb_tap import cdp

    def boom(http_base):
        raise OSError("no browser")
    monkeypatch.setattr(cdp, "open_target", boom)
    rc = main(["--cdp", "http://127.0.0.1:1", "--join-url", "https://x.invalid/r",
               "--no-record", "--per-track"])
    assert rc == 1
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert any(d.get("reason") == "per_track_needs_recording" for d in lines)


HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const procs = [];
class FakeCtx { constructor(o) { this.state = 'running'; this.destination = {}; }
  createGain() { return {gain: {value: 1}, connect() {}}; }
  createScriptProcessor(n) { const p = {connect() {}}; procs.push(p); return p; }
  createMediaStreamSource() { return {connect() {}}; }
  resume() { return Promise.resolve(); } }
class FakePC { constructor() { this.l = []; } addEventListener(t, f) { this.l.push(f); } }
const sent = [], mixed = [];
const win = {RTCPeerConnection: FakePC, AudioContext: FakeCtx,
  MediaStream: class { constructor(t) { this.t = t; } },
  Audio: class { play() { return Promise.resolve(); } },
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  __roboEvent: () => {}, __roboAudio: (b) => mixed.push(b), __roboTrack: (s) => sent.push(s),
  __roboPerTrack: process.argv[3] === 'on'};
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), win);
const pc = new win.RTCPeerConnection({});
pc.l[0]({track: {kind: 'audio', id: 'ta'}});
pc.l[0]({track: {kind: 'audio', id: 'tb'}});
const f = new Float32Array(2048); f[0] = 0.5;
procs.forEach(p => p.onaudioprocess({inputBuffer: {getChannelData: () => f}}));
console.log(JSON.stringify({procs: procs.length, mixed: mixed.length,
  tags: sent.map(s => s.split(':')[0]), ids: win.__roboTap.trackIds()}));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("mode", ["on", "off"])
def test_hook_sends_tagged_chunks_per_track_only_when_enabled(tmp_path, mode):
    h = tmp_path / "pt.js"
    h.write_text(HARNESS)
    r = subprocess.run([NODE, str(h), str(HERE / "hook.js"), mode],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ids"] == [{"n": 1, "id": "ta"}, {"n": 2, "id": "tb"}]
    assert out["mixed"] == 1                     # the mixed path is unchanged
    if mode == "on":
        assert out["procs"] == 3 and out["tags"] == ["1", "2"]
    else:
        assert out["procs"] == 1 and out["tags"] == []
