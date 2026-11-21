# CoThinker — tests of the stage's POST /join and POST /action
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The stage records name joins and queue taps in the spool, never in
buehne.json.

    python3 -m pytest -q tests/test_stage_actions.py
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage"))
sys.path.insert(0, str(ROOT / "gateway"))

import stage as b  # noqa: E402

TOKEN = "tok-test"
SESSION = "2026-09-08-testrunde"


@pytest.fixture
def env(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    session = project / "sessions" / SESSION
    session.mkdir(parents=True)
    (session / "buehne.json").write_text('{"version": 2}', encoding="utf-8")
    spool = tmp_path / "spool"
    monkeypatch.setattr(b, "PROJECT", project)
    monkeypatch.setattr(b, "TOKEN", TOKEN)
    monkeypatch.setattr(b, "SPOOL_DIR", spool)
    monkeypatch.setattr(b, "_last_action", {})
    b.clear_cache()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), b.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{srv.server_address[1]}",
           "spool": spool, "session": session}
    srv.shutdown()
    srv.server_close()
    b.clear_cache()


def post(env, path, body, *, token=TOKEN, cookie=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    url = env["base"] + path + (f"?t={token}" if token is not None else "")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", f"cothinker_pid={cookie}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def lines(env):
    f = env["spool"] / SESSION / "actions.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]


def join(env, name="Alice"):
    code, headers, _ = post(env, "/join", {"name": name})
    assert code == 200
    return re.search(r"cothinker_pid=([0-9a-f]{16})",
                     headers["Set-Cookie"]).group(1)


def test_join_sets_session_cookie(env):
    code, headers, _ = post(env, "/join", {"name": "  Alice\x00\x07 "})
    assert code == 200
    cookie = headers["Set-Cookie"]
    assert re.match(r"cothinker_pid=[0-9a-f]{16}; ", cookie)
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Path=/" in cookie and "Max-Age" not in cookie
    assert lines(env)[0]["payload"] == {"name": "Alice"}
    for bad in ("", "   ", "\x01\x02", "x" * 41):
        assert post(env, "/join", {"name": bad})[0] == 400
    assert post(env, "/join", None, raw=b"{nope")[0] == 400


def test_action_without_cookie_is_401(env):
    assert post(env, "/action", {"intent_id": "queue:room",
                                 "op": "request"})[0] == 401
    assert post(env, "/action", {"intent_id": "queue:room", "op": "request"},
                cookie="zz")[0] == 401
    assert not (env["spool"] / SESSION / "actions.jsonl").exists()


def test_wrong_token_is_403(env):
    assert post(env, "/join", {"name": "Alice"}, token="nope")[0] == 403
    assert post(env, "/join", {"name": "Alice"}, token=None)[0] == 403
    assert not env["spool"].exists()


def test_oversize_body_is_413(env):
    code, _, _ = post(env, "/join", None, raw=b"x" * 2049)
    assert code == 413
    assert not env["spool"].exists()


def test_rate_limit_429(env):
    pid = join(env)
    body = {"intent_id": "queue:room", "op": "request"}
    assert post(env, "/action", body, cookie=pid)[0] == 200
    assert post(env, "/action", body, cookie=pid)[0] == 429
    other = join(env, "Bob")
    assert post(env, "/action", body, cookie=other)[0] == 200


def test_line_format(env):
    pid = join(env, "Alice")
    post(env, "/action", {"intent_id": "queue:room", "op": "done"}, cookie=pid)
    first, second = lines(env)
    for entry in (first, second):
        assert set(entry) == {"v", "at", "person", "intent_id", "kind",
                              "payload"}
        assert entry["v"] == 1 and entry["person"] == pid
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z",
                            entry["at"])
    assert (first["kind"], first["intent_id"], first["payload"]) == (
        "edit", "participant:name", {"name": "Alice"})
    assert (second["kind"], second["intent_id"], second["payload"]) == (
        "tap", "queue:room", {"op": "done"})
    for body in ({"intent_id": "queue:room", "op": "bogus"},
                 {"intent_id": "other", "op": "request"}):
        assert post(env, "/action", body, cookie=join(env, "Bob"))[0] == 400


def test_stage_never_writes_buehne_json(env):
    target = env["session"] / "buehne.json"
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    pid = join(env)
    post(env, "/action", {"intent_id": "queue:room", "op": "request"},
         cookie=pid)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert sorted(p.name for p in env["session"].iterdir()) == ["buehne.json"]
    assert (env["spool"] / SESSION / "actions.jsonl").is_file()
