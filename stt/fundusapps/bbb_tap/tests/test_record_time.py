"""`--record-time` lets a caller fix the timestamp that names the
recording (and, with --per-track, its sidecar) instead of this process
picking its own via `datetime.now()`.

Why this exists: `scripts/stt-vserver.sh` starts the STT server BEFORE
the tap, and the STT server's own `--per-track-sidecar` path must name
the exact file the tap is about to create. The tap's sidecar filename
embeds `datetime.now()` from inside `main()`, computed well after the
shell script already had to decide the STT server's argv -- the shell
script cannot predict that moment. `--record-time` removes the guess: the
shell script computes ONE timestamp up front and hands it to both
processes.
"""
import datetime

from fundusapps.bbb_tap import cdp
from fundusapps.bbb_tap.__main__ import _resolve_record_time, main


def test_resolve_record_time_uses_the_override_when_given():
    assert _resolve_record_time("20261123-091530") == datetime.datetime(2026, 11, 23, 9, 15, 30)


def test_resolve_record_time_defaults_to_now_when_empty():
    before = datetime.datetime.now()
    got = _resolve_record_time("")
    after = datetime.datetime.now()
    assert before <= got <= after


class _FakeCdpRaisesOnSetup:
    """Ends the run right after the recorder/sidecar are created (the
    part this test is after)."""

    def __init__(self, ws_url):
        pass

    def on(self, event, fn):
        pass

    def call(self, method, params=None, timeout=30):
        raise OSError("socket is closed")


def test_record_time_override_names_the_sidecar_path_end_to_end(monkeypatch, tmp_path):
    """The path scripts/stt-vserver.sh must be able to predict: with
    --record-time and --per-track, the sidecar lands at exactly
    <record-dir>/<date>/bbb-<time>-tracks.json for the given timestamp,
    not whatever datetime.now() happens to be when main() gets there."""
    monkeypatch.setattr(cdp, "open_target", lambda http_base: ("tid", "ws://fake"))
    monkeypatch.setattr(cdp, "close_target", lambda http_base, target: None)
    monkeypatch.setattr(cdp, "Cdp", _FakeCdpRaisesOnSetup)
    main(["--cdp", "http://127.0.0.1:1", "--join-url", "https://bbb.example.invalid/room/x",
         "--record-dir", str(tmp_path), "--record-time", "20261123-091530",
         "--per-track", "--join-retries", "1"])
    expected = tmp_path / "2026-11-23" / "bbb-091530-tracks.json"
    assert expected.exists()
