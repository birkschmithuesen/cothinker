#!/usr/bin/env python3
"""Demo session endpoint: mints a per-user Jitsi JWT behind a demo token.

Stdlib only. Binds 127.0.0.1:${DEMO_SESSION_PORT:-8091}.

  GET /api/session?t=<demo token>&name=<display name>
    -> {"domain", "room", "jwt", "stage_path"}   (Cache-Control: no-store)

Environment: JITSI_JWT_SECRET, DEMO_TOKEN, STAGE_TOKEN, DEMO_DOMAIN,
DEMO_ROOM, JWT_ISS (default "cothinker"), DEMO_SESSION_PORT.

CLI: `session_server.py --mint-robo <outfile>` writes the listen-only
bot's join URL as `ROBO_JOIN_URL='...'` (mode 0600) and exits. It needs
DEMO_PUBLIC_URL (e.g. https://example.org) besides the variables above.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JWT_TTL_S = 12 * 3600
NAME_MAX = 40


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(secret: str, claims: dict) -> str:
    """HS256 JWT."""
    header = {"alg": "HS256", "typ": "JWT"}
    signing = ".".join(
        _b64(json.dumps(p, separators=(",", ":"), sort_keys=True).encode())
        for p in (header, claims))
    sig = hmac.new(secret.encode(), signing.encode("ascii"), hashlib.sha256)
    return signing + "." + _b64(sig.digest())


def check_token(given: str | None, expected: str) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def clean_name(raw: str | None) -> str:
    """Strip; 1..40 chars; no control characters. ValueError otherwise."""
    name = (raw or "").strip()
    if not name or len(name) > NAME_MAX:
        raise ValueError("bad name length")
    if any(ord(c) < 32 or ord(c) == 127 or 0x80 <= ord(c) < 0xA0
           for c in name):
        raise ValueError("control character in name")
    return name


def jwt_claims(room: str, name: str, user_id: str, iss: str,
               now: int | None = None, ttl_s: int = JWT_TTL_S) -> dict:
    now = int(time.time()) if now is None else now
    return {
        "aud": "jitsi", "iss": iss, "sub": "*", "room": room,
        "nbf": now - 10, "exp": now + ttl_s,
        "context": {"user": {"name": name, "id": user_id}},
    }


def session_payload(env: dict, name: str, now: int | None = None) -> dict:
    claims = jwt_claims(env["DEMO_ROOM"], name, secrets.token_hex(6),
                        env.get("JWT_ISS", "cothinker"), now)
    return {
        "domain": env["DEMO_DOMAIN"],
        "room": env["DEMO_ROOM"],
        "jwt": make_jwt(env["JITSI_JWT_SECRET"], claims),
        "stage_path": "stage/?t=" + urllib.parse.quote(env["STAGE_TOKEN"]),
    }


def robo_join_url(env: dict, now: int | None = None) -> str:
    claims = jwt_claims(env["DEMO_ROOM"], "Robo", "robo",
                        env.get("JWT_ISS", "cothinker"), now,
                        ttl_s=int(env.get("ROBO_TTL_H", "12")) * 3600)
    jwt = make_jwt(env["JITSI_JWT_SECRET"], claims)
    base = env["DEMO_PUBLIC_URL"].rstrip("/")
    return (f"{base}/{env['DEMO_ROOM']}?jwt={jwt}"
            "#config.prejoinConfig.enabled=false"
            "&userInfo.displayName=%22Robo%22"
            "&config.startWithAudioMuted=true"
            "&config.startWithVideoMuted=true")


def write_robo_env(env: dict, path: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("ROBO_JOIN_URL='%s'\n" % robo_join_url(env))
    os.chmod(path, 0o600)


def make_handler(env: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "demo-session"

        def log_message(self, fmt, *args):  # never log the query string
            path = urllib.parse.urlsplit(self.path).path
            sys.stderr.write("%s %s\n" % (self.command, path))

        def _send(self, code: int, body: bytes = b"",
                  ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Cache-Control", "no-store")
            if body:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != "/api/session":
                return self._send(404)
            q = urllib.parse.parse_qs(parts.query)
            if not check_token((q.get("t") or [None])[0], env["DEMO_TOKEN"]):
                return self._send(404)
            try:
                name = clean_name((q.get("name") or [None])[0])
            except ValueError:
                return self._send(400)
            body = json.dumps(session_payload(env, name)).encode()
            self._send(200, body)

    return Handler


REQUIRED = ("JITSI_JWT_SECRET", "DEMO_TOKEN", "STAGE_TOKEN",
            "DEMO_DOMAIN", "DEMO_ROOM")


def main(argv: list[str]) -> int:
    env = dict(os.environ)
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        sys.stderr.write("missing env: %s\n" % ", ".join(missing))
        return 2
    if argv[:1] == ["--mint-robo"]:
        if len(argv) != 2 or not env.get("DEMO_PUBLIC_URL"):
            sys.stderr.write("usage: --mint-robo <outfile> (needs DEMO_PUBLIC_URL)\n")
            return 2
        write_robo_env(env, argv[1])
        return 0
    port = int(env.get("DEMO_SESSION_PORT", "8091"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(env))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
