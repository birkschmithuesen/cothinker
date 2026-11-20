#!/usr/bin/env python3
"""M1b -- path A's two access routes to the same turn: the SSE-over-session
route M1-M3 already use (`/api/sessions/{id}/chat/stream`) vs the
OpenAI-Responses-shaped `/v1/runs` + `GET /v1/runs/{run_id}/events` route.
Reports TIME-TO-FIRST-EVENT and TIME-TO-FIRST-DELTA for each, not total
turn time -- which route a live workshop display sees output from first.

Verified against the installed Hermes source
(gateway/platforms/api_server_runs.py): `POST /v1/runs {"input": text}`
returns `{"run_id": ..., "status": "started"}` immediately (no
`session_id` needed -- it defaults to the run_id, a fresh session per
call). `GET /v1/runs/{run_id}/events` frames every event as a bare
`data: {"event": <name>, ...}` line with NO `event:` SSE field (unlike
`/chat/stream`, which sets one) -- the event name lives inside the JSON
body under the key "event". Streamed text arrives as `message.delta`
(field `delta`), the run ends on `run.completed` / `run.failed` /
`run.cancelled`.

    python3 scripts/measure_hermes/m1b_access_route.py --port <p> --n 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.measure_hermes import prompts as P  # noqa: E402
from scripts.measure_hermes.common import now, percentile  # noqa: E402


class RunsApi:
    def __init__(self, base: str, key: str, timeout_s: float = 600) -> None:
        self.base = base.rstrip("/")
        self.key = key
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def start_run(self, text: str) -> str:
        data = json.dumps({"input": text}).encode("utf-8")
        req = urllib.request.Request(self.base + "/v1/runs", data=data,
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["run_id"]

    def run_turn(self, text: str) -> dict:
        """One `/v1/runs` turn: start it, then stream its events to
        completion. Returns the same shape `api_client.Api.chat_stream()`
        does, so both routes can be compared with one code path."""
        t0 = now()
        try:
            run_id = self.start_run(text)
        except urllib.error.HTTPError as e:
            return {"ok": False, "t_first_event": None, "t_first_delta": None,
                    "t_total": now() - t0, "status": e.code}

        req = urllib.request.Request(
            f"{self.base}/v1/runs/{run_id}/events", headers=self._headers(), method="GET")
        t_first_event = None
        t_first_delta = None
        terminal = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if not line or not line.startswith("data:"):
                        continue
                    if t_first_event is None:
                        t_first_event = now() - t0
                    try:
                        payload = json.loads(line[len("data:"):].strip())
                    except ValueError:
                        continue
                    name = payload.get("event") or ""
                    if name == "message.delta" and t_first_delta is None:
                        t_first_delta = now() - t0
                    if name in ("run.completed", "run.failed", "run.cancelled"):
                        terminal = name
                        break
        except urllib.error.HTTPError as e:
            return {"ok": False, "t_first_event": t_first_event, "t_first_delta": t_first_delta,
                    "t_total": now() - t0, "status": e.code}
        return {"ok": terminal == "run.completed", "t_first_event": t_first_event,
                "t_first_delta": t_first_delta, "t_total": now() - t0, "status": 200,
                "terminal": terminal}


def compare(api, runs_api: RunsApi, *, n: int = 5, text: str = P.SHORT) -> dict:
    """n reps of the SAME prompt on both routes, path A only (this
    compares two ways of TALKING to path A, not A vs B). Each rep opens
    its own fresh session/run -- no shared growing context between reps,
    since the question is routing latency, not per-turn cost."""
    sessions: dict[str, list] = {"chat_stream": [], "runs": []}
    for _ in range(n):
        sid = api.create_session()
        r = api.chat_stream(sid, text)
        sessions["chat_stream"].append(r)
        r2 = runs_api.run_turn(text)
        sessions["runs"].append(r2)

    summary = {}
    for route, rows in sessions.items():
        firsts = [r.get("t_first_delta") for r in rows if r.get("t_first_delta") is not None]
        first_events = [r.get("t_first_event") for r in rows if r.get("t_first_event") is not None] \
            if route == "runs" else [r.get("t_first_delta") for r in rows if r.get("t_first_delta") is not None]
        fails = sum(1 for r in rows if not r.get("ok"))
        summary[route] = {
            "t_first_delta_p50": percentile(firsts, 50) if firsts else None,
            "t_first_event_p50": percentile(first_events, 50) if first_events else None,
            "fail": fails, "n": n}
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--key-env", default="API_SERVER_KEY")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()

    key = os.environ.get(a.key_env, "")
    if not key:
        raise SystemExit(f"m1b_access_route: {a.key_env} is not set")
    from scripts.measure_hermes.api_client import Api
    api = Api(f"http://127.0.0.1:{a.port}", key)
    runs_api = RunsApi(f"http://127.0.0.1:{a.port}", key)
    summary = compare(api, runs_api, n=a.n)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
