#!/usr/bin/env python3
"""M1 -- turn duration, Hermes API server (path A) vs gateway/worker.py
(path B), same prompt, same model. Produces the R3 numbers for the
plan S0 path-decision rule (spec §2.3).

    python3 scripts/measure_hermes/m1_turns.py --profile-home <dir> \\
        --port <p> --model <m> --provider <p> --n 20 --out <jsonl>
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.worker import FAILURE_MARKERS, WorkerClient  # noqa: E402
from scripts.measure_hermes import prompts as P  # noqa: E402
from scripts.measure_hermes.api_client import Api  # noqa: E402
from scripts.measure_hermes.common import (  # noqa: E402
    guard_memory, now, percentile, worker_interpreter, write_jsonl)

CLASS_PROMPTS = {"SHORT": P.SHORT, "MEDIUM": P.MEDIUM, "TOOL": P.TOOL}


def _worker_text_failed(final: str) -> bool:
    return any(marker in final for marker in FAILURE_MARKERS)


def _api_turn(api, sid: str, text: str) -> dict:
    r = api.chat_stream(sid, text)
    ok = bool(r.get("ok")) and not _worker_text_failed(str(r.get("final") or ""))
    return {"ok": ok, "t_total": r["t_total"], "t_first_delta": r.get("t_first_delta"),
            "tools": r.get("tools") or [], "usage": r.get("usage") or {}}


def _worker_turn(worker: WorkerClient, text: str) -> dict:
    t0 = now()
    result = worker.run(text)
    t_total = now() - t0
    ok = bool(result.get("ok")) and not _worker_text_failed(str(result.get("final") or ""))
    return {"ok": ok, "t_total": t_total, "t_first_delta": None, "tools": [],
            "usage": result.get("usage") or {}}


def run(api, worker_cmd: list[str], *, n: int = 20,
        classes: tuple[str, ...] = ("SHORT", "MEDIUM", "TOOL"),
        out: Path | None = None, cold_start_repeats: int = 3,
        session_id: str | None = None) -> dict:
    session_id = session_id or f"measure-{int(time.time())}"
    worker = WorkerClient(command=list(worker_cmd), session_id=session_id, log=lambda *a: None)
    if not worker.start():
        raise RuntimeError("m1_turns: worker did not start")
    try:
        api_sid = api.create_session()
        # Warm-up: one untimed turn on each path, outside every measurement.
        api.chat_stream(api_sid, CLASS_PROMPTS["SHORT"])
        worker.run(CLASS_PROMPTS["SHORT"])

        for i in range(cold_start_repeats):
            guard_memory()
            worker.stop()
            t0 = now()
            worker.start()
            if out is not None:
                write_jsonl(out, {"kind": "cold_start", "path": "B", "i": i,
                                  "t_total": now() - t0})
        for i in range(cold_start_repeats):
            guard_memory()
            t0 = now()
            fresh_sid = api.create_session()
            api.chat_stream(fresh_sid, CLASS_PROMPTS["SHORT"])
            if out is not None:
                write_jsonl(out, {"kind": "cold_start", "path": "A", "i": i,
                                  "t_total": now() - t0})

        totals = {cls: {"A": [], "B": []} for cls in classes}
        fails = {cls: {"A": 0, "B": 0} for cls in classes}
        for cls in classes:
            text = CLASS_PROMPTS[cls]
            for i in range(n):
                guard_memory()
                for path, turn in (("A", _api_turn(api, api_sid, text)),
                                   ("B", _worker_turn(worker, text))):
                    totals[cls][path].append(turn["t_total"])
                    if not turn["ok"]:
                        fails[cls][path] += 1
                    record = {"kind": "warm", "path": path, "cls": cls, "i": i,
                             "t_total": turn["t_total"], "ok": turn["ok"]}
                    if turn.get("t_first_delta") is not None:
                        record["t_first_delta"] = turn["t_first_delta"]
                    if turn.get("tools"):
                        record["tool_count"] = len(turn["tools"])
                        record["tool_total_s"] = sum(
                            float(t.get("duration_s") or 0.0) for t in turn["tools"])
                    usage = turn.get("usage") or {}
                    for tok_key in ("input_tokens", "output_tokens"):
                        if tok_key in usage:
                            record[tok_key] = usage[tok_key]
                    if out is not None:
                        write_jsonl(out, record)
    finally:
        worker.stop()

    summary: dict = {"A": {}, "B": {}, "overhead": {}}
    for cls in classes:
        for path in ("A", "B"):
            values = totals[cls][path]
            summary[path][cls] = {"p50": percentile(values, 50), "p90": percentile(values, 90),
                                  "fail": fails[cls][path]}
        summary["overhead"][cls] = {
            "p50": summary["A"][cls]["p50"] - summary["B"][cls]["p50"],
            "p90": summary["A"][cls]["p90"] - summary["B"][cls]["p90"]}
        s = summary
        print(f"M1 cls={cls} A p50={s['A'][cls]['p50']:.3f} p90={s['A'][cls]['p90']:.3f} "
              f"B p50={s['B'][cls]['p50']:.3f} p90={s['B'][cls]['p90']:.3f} "
              f"overhead_p50={s['overhead'][cls]['p50']:.3f} "
              f"overhead_p90={s['overhead'][cls]['p90']:.3f} "
              f"fail_A={fails[cls]['A']} fail_B={fails[cls]['B']}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-home", default="")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--key-env", default="API_SERVER_KEY")
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--classes", default="SHORT,MEDIUM,TOOL")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    key = os.environ.get(a.key_env, "")
    if not key:
        raise SystemExit(f"m1_turns: {a.key_env} is not set")
    api = Api(f"http://127.0.0.1:{a.port}", key)
    worker_cmd = [worker_interpreter(), str(ROOT / "gateway" / "worker.py"),
                 "--hermes-home", a.profile_home, "--model", a.model, "--provider", a.provider]
    classes = tuple(c.strip() for c in a.classes.split(",") if c.strip())
    run(api, worker_cmd, n=a.n, classes=classes, out=Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
