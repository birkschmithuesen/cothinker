import base64
import hashlib
import hmac
import io
import json
import os
import stat
import sys
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

import session_server as ss  # noqa: E402

ENV = {
    "JITSI_JWT_SECRET": "unit-test-secret",  # pragma: allowlist secret
    "DEMO_TOKEN": "demo-tok",
    "STAGE_TOKEN": "stage-tok",
    "DEMO_DOMAIN": "meet.example.org",
    "DEMO_ROOM": "demo-room",
    "DEMO_PUBLIC_URL": "https://meet.example.org",
}


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _decode(jwt, secret):
    h, p, s = jwt.split(".")
    want = hmac.new(secret.encode(), (h + "." + p).encode(),
                    hashlib.sha256).digest()
    assert hmac.compare_digest(want, _unb64(s))
    return json.loads(_unb64(h)), json.loads(_unb64(p))


def test_jwt_header_claims_signature():
    p = ss.session_payload(ENV, "Alex", now=1000)
    header, claims = _decode(p["jwt"], ENV["JITSI_JWT_SECRET"])
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert claims["aud"] == "jitsi" and claims["iss"] == "cothinker"
    assert claims["sub"] == "*" and claims["room"] == "demo-room"
    assert claims["exp"] == 1000 + 12 * 3600 and claims["nbf"] <= 1000
    assert claims["context"]["user"]["name"] == "Alex"
    assert claims["context"]["user"]["id"]


def test_payload_has_no_secret_but_jwt_and_stage_path():
    p = ss.session_payload(ENV, "Alex")
    assert set(p) == {"domain", "room", "jwt", "stage_path"}
    assert p["stage_path"] == "stage/?t=stage-tok"
    flat = json.dumps({k: v for k, v in p.items() if k != "jwt"})
    assert ENV["JITSI_JWT_SECRET"] not in flat
    assert ENV["DEMO_TOKEN"] not in flat
    assert ENV["JITSI_JWT_SECRET"] not in p["jwt"]


def test_check_token():
    assert ss.check_token("a", "a")
    assert not ss.check_token("a", "b")
    assert not ss.check_token(None, "b")
    assert not ss.check_token("", "")


def test_clean_name():
    assert ss.clean_name("  Alex ") == "Alex"
    for bad in (None, "", "   ", "x" * 41, "a\nb", "a\x00b", "a\x7fb"):
        try:
            ss.clean_name(bad)
        except ValueError:
            continue
        raise AssertionError(repr(bad))
    assert ss.clean_name("x" * 40) == "x" * 40


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ss.make_handler(ENV))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(srv, qs):
    url = "http://127.0.0.1:%d/api/session?%s" % (srv.server_address[1], qs)
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_http_codes_and_no_query_logging():
    srv = _serve()
    buf = io.StringIO()
    try:
        with redirect_stderr(buf):
            code, body, hdr = _get(srv, "t=demo-tok&name=Alex")
            assert code == 200
            assert hdr["Cache-Control"] == "no-store"
            assert json.loads(body)["room"] == "demo-room"
            code, body, _ = _get(srv, "t=wrong&name=Alex")
            assert (code, body) == (404, b"")
            assert _get(srv, "name=Alex")[0] == 404
            assert _get(srv, "t=demo-tok")[0] == 400
            assert _get(srv, "t=demo-tok&name=" + "x" * 41)[0] == 400
            assert _get(srv, "t=demo-tok&name=a%0Ab")[0] == 400
    finally:
        srv.shutdown()
    log = buf.getvalue()
    assert "GET /api/session" in log
    assert "demo-tok" not in log and "Alex" not in log and "?" not in log


def test_mint_robo(tmp_path):
    out = tmp_path / "robo.env"
    ss.write_robo_env(ENV, str(out))
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    line = out.read_text().strip()
    assert line.startswith("ROBO_JOIN_URL='https://meet.example.org/demo-room?jwt=")
    assert line.endswith("startWithVideoMuted=true'")
    jwt = line.split("jwt=")[1].split("#")[0]
    _, claims = _decode(jwt, ENV["JITSI_JWT_SECRET"])
    assert claims["context"]["user"]["name"] == "Robo"
