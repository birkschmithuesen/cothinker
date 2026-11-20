#!/usr/bin/env python3
"""M2 -- background delegation via the Hermes API server: do both children
come back as delivery rows without a client turn (R1), and does a short
message sent while they run complete before the last child (R2)? Produces
the R1/R2 numbers for the plan S0 path-decision rule (spec §2.3).

    python3 scripts/measure_hermes/m2_delegation.py --profile-home <dir> \\
        --port <p> --model <m> --provider <p> --reps 5 --out <jsonl>

A delivery row is recognized by `display_kind == "async_delegation_complete"`
(`gateway/wake.py: persist_delegation_delivery`, verified against the
installed Hermes source, see `api_client.py`'s docstring). The brief's
ANNAHME of two `subagent.start` SSE events is not observable on
`/api/sessions/{id}/chat/stream` in this Hermes version -- the dispatch
signal used here instead is a `delegate_task` entry in `chat_stream()`'s
`tools` list.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.worker import WorkerClient  # noqa: E402
from scripts.measure_hermes import prompts as P  # noqa: E402
from scripts.measure_hermes.api_client import Api  # noqa: E402
from scripts.measure_hermes.common import (  # noqa: E402
    guard_memory, now, percentile, worker_interpreter, write_jsonl)


def _active_delegations(health: dict) -> int:
    try:
        return int(health["readiness"]["checks"]["background_queues"]["active_delegations"])
    except (KeyError, TypeError, ValueError):
        return 0


def _is_delivery_row(row: dict) -> bool:
    return row.get("display_kind") == "async_delegation_complete"


def _dispatch_signal(tools: list[dict]) -> bool:
    return any(t.get("name") == "delegate_task" for t in tools)


_TASK_MARKER_RE = re.compile(r"TASK (\d+)/\d+")


def _dispatched_task_count(rows: list[dict]) -> int:
    """Number of subagents the dispatch turn's own ``delegate_task`` tool response reported
    (its ``count`` field). NOT the number of delivery rows to expect: a multi-task
    ``delegate_task`` call groups its tasks into completion UNITS (one async slot each --
    ``tools/async_delegation.py``'s ``dispatch_async_delegation_batch`` docstring: "a whole
    batch, or one group of a delegate_task call... produces a SINGLE completion event"), and a
    live check on the production server (2026-10-23) showed a 2-task call's own tool response say so explicitly:
    "2 subagents are running in parallel ... as 1 completion unit(s)". One
    ``async_delegation_complete`` row can therefore cover more than one task."""
    for r in rows:
        if r.get("role") == "tool" and r.get("tool_name") == "delegate_task":
            try:
                return int((json.loads(r.get("content") or "{}") or {}).get("count") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    return 0


def _delivered_task_indexes(rows: list[dict]) -> set[int]:
    """Task indexes ("TASK n/N") mentioned across every delivery row's content so far -- a
    single row may report more than one task (see ``_dispatched_task_count``)."""
    indexes: set[int] = set()
    for r in rows:
        if not _is_delivery_row(r):
            continue
        for m in _TASK_MARKER_RE.finditer(str(r.get("content") or "")):
            indexes.add(int(m.group(1)))
    return indexes


def _one_rep_path_a(api, *, poll_interval: float, health_timeout: float,
                    delivery_timeout: float) -> dict:
    sid = api.create_session()
    t0 = now()
    dispatch = api.chat_stream(sid, P.DELEGATE)
    t_dispatch = now() - t0
    dispatch_signal = _dispatch_signal(dispatch.get("tools") or [])

    active_seen = False
    deadline = now() + health_timeout
    while now() < deadline:
        if _active_delegations(api.health_detailed()) >= 1:
            active_seen = True
            break
        time.sleep(poll_interval)

    dispatched_n = _dispatched_task_count(api.messages(sid))
    required = dispatched_n or 1  # unknown count: fall back to "at least one delivery row"

    t_short_start = now()
    short = api.chat_stream(sid, P.SHORT)
    t_short_end = now()
    t_short = short["t_total"]

    baseline_non_delivery_assistant = sum(
        1 for r in api.messages(sid)
        if r.get("role") == "assistant" and not _is_delivery_row(r))

    full_delivery_at: float | None = None
    deadline = now() + delivery_timeout
    while now() < deadline:
        guard_memory()
        rows = api.messages(sid)
        if len(_delivered_task_indexes(rows)) >= required:
            full_delivery_at = now() - t0
            break
        time.sleep(poll_interval)

    final_rows = api.messages(sid)
    non_delivery_assistant = sum(
        1 for r in final_rows if r.get("role") == "assistant" and not _is_delivery_row(r))

    return {
        "dispatch_signal": dispatch_signal,
        "dispatch_ok": dispatch.get("ok", True),
        "dispatch_status": dispatch.get("status"),
        "active_seen": active_seen,
        "t_dispatch": t_dispatch,
        "t_short": t_short,
        "short_ok": short.get("ok", True),
        "short_status": short.get("status"),
        "delivered_both": full_delivery_at is not None,
        # "waits while children run" (R2): the short turn must finish strictly before every
        # dispatched task's result has been observed (one row can cover several tasks).
        "short_before_children": (
            (t_short_end - t0) < full_delivery_at if full_delivery_at is not None else False),
        # A genuinely unsolicited extra turn, not the normal 1-2 assistant rows a dispatch/short
        # turn's own tool-calling trace already produces: compare against the pre-wait baseline.
        "unsolicited_turn": non_delivery_assistant > baseline_non_delivery_assistant,
    }


def _one_rep_path_b(worker: WorkerClient) -> dict:
    t0 = now()
    result = worker.run(P.DELEGATE)
    return {"t_dispatch": now() - t0, "ok": bool(result.get("ok"))}


def run(api, worker_cmd: list[str], *, reps: int = 5, out: Path | None = None,
        baseline_short_p50: float | None = None, poll_interval: float = 1.0,
        health_timeout: float = 30.0, delivery_timeout: float = 600.0,
        session_id: str | None = None) -> dict:
    session_id = session_id or f"measure-m2-{int(time.time())}"
    worker = WorkerClient(command=list(worker_cmd), session_id=session_id, log=lambda *a: None)
    if not worker.start():
        raise RuntimeError("m2_delegation: worker did not start")
    try:
        r1_hits = 0
        short_before_hits = 0
        t_short_values: list[float] = []
        t_dispatch_a_values: list[float] = []
        t_dispatch_b_values: list[float] = []
        for i in range(reps):
            guard_memory()
            rep_a = _one_rep_path_a(api, poll_interval=poll_interval,
                                    health_timeout=health_timeout,
                                    delivery_timeout=delivery_timeout)
            if rep_a["delivered_both"] and not rep_a["unsolicited_turn"]:
                r1_hits += 1
            if rep_a["short_before_children"]:
                short_before_hits += 1
            t_short_values.append(rep_a["t_short"])
            t_dispatch_a_values.append(rep_a["t_dispatch"])

            rep_b = _one_rep_path_b(worker)
            t_dispatch_b_values.append(rep_b["t_dispatch"])

            if out is not None:
                write_jsonl(out, {"kind": "m2", "i": i, "path": "A",
                                  "t_dispatch": rep_a["t_dispatch"], "t_short": rep_a["t_short"],
                                  "delivered_both": rep_a["delivered_both"],
                                  "short_before_children": bool(rep_a["short_before_children"]),
                                  "unsolicited_turn": rep_a["unsolicited_turn"]})
                write_jsonl(out, {"kind": "m2", "i": i, "path": "B",
                                  "t_dispatch": rep_b["t_dispatch"], "ok": rep_b["ok"]})
    finally:
        worker.stop()

    t_short_p50 = percentile(t_short_values, 50)
    dispatch_a_p50 = percentile(t_dispatch_a_values, 50)
    dispatch_b_p50 = percentile(t_dispatch_b_values, 50)
    limit = (1.5 * baseline_short_p50) if baseline_short_p50 is not None else None

    summary = {
        "R1": f"{r1_hits}/{reps}",
        "R2_short_before_children": f"{short_before_hits}/{reps}",
        "t_short_p50": t_short_p50,
        "limit": limit,
        "dispatch_A_p50": dispatch_a_p50,
        "dispatch_B_p50": dispatch_b_p50,
    }
    limit_str = f"{limit:.3f}" if limit is not None else "pending (needs M1 SHORT p50)"
    print(f"M2 R1={summary['R1']} R2 t_short_p50={t_short_p50:.3f} (limit={limit_str}) "
          f"short_before_children={summary['R2_short_before_children']} "
          f"dispatch_A_p50={dispatch_a_p50:.3f} dispatch_B_p50={dispatch_b_p50:.3f}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-home", default="")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--key-env", default="API_SERVER_KEY")
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline-short-p50", type=float, default=None,
                    help="M1 SHORT p50 (seconds), for the R2 1.5x limit")
    a = ap.parse_args()

    key = os.environ.get(a.key_env, "")
    if not key:
        raise SystemExit(f"m2_delegation: {a.key_env} is not set")
    api = Api(f"http://127.0.0.1:{a.port}", key)
    worker_cmd = [worker_interpreter(), str(ROOT / "gateway" / "worker.py"),
                 "--hermes-home", a.profile_home, "--model", a.model, "--provider", a.provider]
    run(api, worker_cmd, reps=a.reps, out=Path(a.out), baseline_short_p50=a.baseline_short_p50)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
