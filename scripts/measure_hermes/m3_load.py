#!/usr/bin/env python3
"""M3 -- N concurrent sessions, each running the M2 pattern once plus 3
MEDIUM turns, while a sampler thread tracks RSS/CPU. Produces the R4
numbers (and their extrapolation to 10 sessions) for the plan S0
path-decision rule (spec §2.3).

    python3 scripts/measure_hermes/m3_load.py --profile-home <dir> \\
        --port <p> --model <m> --provider <p> --path a --sessions 1,2,3

Path A: one `hermes gateway` process serves every session; its PID is
found via `pgrep -f "hermes -p <profile> gateway"` (first match, profile
name from `COTHINKER_MEASURE_PROFILE`).
Path B: one `gateway/worker.py` process per session; RSS is the sum over
all of them. Either way, delegated children are threads inside their
parent process (`delegation.max_concurrent_children`'s docstring), so no
separate child-process accounting is needed on top of each session's own
PID -- `common.rss_mb`'s `include_children` only matters for a session
that itself shells out to a subprocess tool.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.worker import WorkerClient  # noqa: E402
from scripts.measure_hermes import prompts as P  # noqa: E402
from scripts.measure_hermes.api_client import Api  # noqa: E402
from scripts.measure_hermes.common import (  # noqa: E402
    cpu_seconds, guard_memory, mem_available_mb, now, percentile, rss_mb, worker_interpreter, write_jsonl)
from scripts.measure_hermes.m2_delegation import _one_rep_path_a  # noqa: E402


def fit_line(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares a + b*x. Returns (a, b)."""
    n = len(points)
    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xy = sum(x * y for x, y in points)
    sum_xx = sum(x * x for x, _ in points)
    denom = n * sum_xx - sum_x ** 2
    b = (n * sum_xy - sum_x * sum_y) / denom
    a = (sum_y - b * sum_x) / n
    return a, b


def _find_gateway_pid(port: int | None = None) -> int | None:
    """The host's `hermes gateway` process, for path A's RSS sampling.

    Resolved via the TCP port it listens on (`ss -ltnp`) when `port` is
    given, not by matching "hermes" in its argv: a live check on the
    production server showed a fresh `pm`-managed install's gateway process
    has no literal "hermes" token in its command line at all (the
    shebang-wrapper chain execs an inline `python3 -I -c '...'` script,
    with "-p <profile> gateway run" appended as plain argv at the
    very end, never preceded by a "hermes" word), so the pgrep pattern
    below never matches there.
    Falls back to that old pgrep pattern when no port is given or
    nothing is listening on it, for hosts where it still applies.
    """
    if port is not None:
        try:
            out = subprocess.run(
                ["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
                continue
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
    profile = os.environ.get("COTHINKER_MEASURE_PROFILE", "")
    pattern = f"hermes -p {profile} gateway" if profile else "hermes.*gateway"
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def _classify_status(status) -> str:
    if status == 429:
        return "http_429"
    if isinstance(status, int) and 500 <= status < 600:
        return "http_5xx"
    return "http_error"


def _bump(fail_reasons: dict, reason: str) -> None:
    fail_reasons[reason] = fail_reasons.get(reason, 0) + 1


def _run_one_session_a(api, lock, turn_times: list, fails: list, http429: list,
                       fail_reasons: dict, sessions_failed: list, poll_interval: float,
                       health_timeout: float, delivery_timeout: float) -> None:
    session_failed = False
    try:
        rep = _one_rep_path_a(api, poll_interval=poll_interval, health_timeout=health_timeout,
                              delivery_timeout=delivery_timeout)
        with lock:
            turn_times.extend([rep["t_dispatch"], rep["t_short"]])
            if not rep.get("dispatch_ok", True):
                fails[0] += 1
                session_failed = True
                reason = _classify_status(rep.get("dispatch_status"))
                _bump(fail_reasons, f"dispatch_{reason}")
                if reason == "http_429":
                    http429[0] += 1
            elif not rep.get("short_ok", True):
                fails[0] += 1
                session_failed = True
                reason = _classify_status(rep.get("short_status"))
                _bump(fail_reasons, f"short_{reason}")
                if reason == "http_429":
                    http429[0] += 1
            elif not rep["delivered_both"]:
                fails[0] += 1
                session_failed = True
                _bump(fail_reasons, "delivery_timeout")
        sid = api.create_session()
        for _ in range(3):
            r = api.chat_stream(sid, P.MEDIUM)
            with lock:
                if r.get("status") == 429:
                    http429[0] += 1
                    session_failed = True
                    _bump(fail_reasons, "medium_http_429")
                elif not r.get("ok"):
                    fails[0] += 1
                    session_failed = True
                    _bump(fail_reasons, f"medium_{_classify_status(r.get('status'))}")
                turn_times.append(r["t_total"])
    except Exception as exc:       # noqa: BLE001 - one session's failure, not the sampler's
        with lock:
            fails[0] += 1
            session_failed = True
            _bump(fail_reasons, f"exception_{type(exc).__name__}")
    if session_failed:
        with lock:
            sessions_failed[0] += 1


def _run_one_session_b(worker_cmd: list[str], session_id: str, lock, turn_times: list,
                       fails: list, worker_pids: list) -> None:
    worker = WorkerClient(command=list(worker_cmd), session_id=session_id, log=lambda *a: None)
    try:
        if not worker.start():
            with lock:
                fails[0] += 1
            return
        with lock:
            if worker.proc is not None:
                worker_pids.append(worker.proc.pid)
        t0 = now()
        result = worker.run(P.DELEGATE)
        with lock:
            turn_times.append(now() - t0)
            if not result.get("ok"):
                fails[0] += 1
        for _ in range(3):
            t0 = now()
            result = worker.run(P.MEDIUM)
            with lock:
                turn_times.append(now() - t0)
                if not result.get("ok"):
                    fails[0] += 1
    finally:
        worker.stop()


def _sample_once(path: str, gateway_pid: int | None, worker_pids: list, lock,
                 rss_peak: list, cpu_prev: list, cpu_peak_pct: list, interval: float) -> None:
    guard_memory()
    if path == "a":
        pid = gateway_pid if gateway_pid is not None else os.getpid()
        pids = [pid]
    else:
        with lock:
            pids = list(worker_pids)
    total_rss = sum(rss_mb(p) for p in pids)
    total_cpu = sum(cpu_seconds(p) for p in pids)
    with lock:
        rss_peak[0] = max(rss_peak[0], total_rss)
        if cpu_prev[0] is not None:
            delta = total_cpu - cpu_prev[0]
            pct = (delta / interval) * 100 if interval > 0 else 0.0
            cpu_peak_pct[0] = max(cpu_peak_pct[0], pct)
        cpu_prev[0] = total_cpu


def run(api, worker_cmd: list[str], *, sessions: tuple[int, ...] = (1, 2, 3),
        path: str = "a", out: Path | None = None, server_ram_gb: float | None = None,
        gateway_pid: int | None = None, gateway_port: int | None = None,
        sample_interval: float = 1.0,
        poll_interval: float = 1.0, health_timeout: float = 30.0,
        delivery_timeout: float = 600.0, stagger_s: float = 0.0) -> list[dict]:
    if path == "a" and gateway_pid is None:
        gateway_pid = _find_gateway_pid(gateway_port)

    summaries: list[dict] = []
    for n in sessions:
        lock = threading.Lock()
        turn_times: list[float] = []
        fails = [0]
        http429 = [0]
        fail_reasons: dict = {}
        sessions_failed = [0]
        worker_pids: list[int] = []
        rss_peak = [0.0]
        cpu_prev = [None]
        cpu_peak_pct = [0.0]
        stop_sampling = threading.Event()

        def sampler() -> None:
            while not stop_sampling.is_set():
                _sample_once(path, gateway_pid, worker_pids, lock, rss_peak, cpu_prev,
                             cpu_peak_pct, sample_interval)
                stop_sampling.wait(sample_interval)

        sampler_thread = threading.Thread(target=sampler, daemon=True)
        sampler_thread.start()

        threads = []
        for i in range(n):
            if i > 0 and stagger_s > 0:
                time.sleep(stagger_s)
            if path == "a":
                t = threading.Thread(target=_run_one_session_a,
                                     args=(api, lock, turn_times, fails, http429, fail_reasons,
                                           sessions_failed, poll_interval, health_timeout,
                                           delivery_timeout))
            else:
                t = threading.Thread(target=_run_one_session_b,
                                     args=(worker_cmd, f"measure-m3-{n}-{i}-{int(time.time())}",
                                           lock, turn_times, fails, worker_pids))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        stop_sampling.set()
        sampler_thread.join(timeout=sample_interval + 5)

        rss_per_session = rss_peak[0] / n if n else 0.0
        turn_p50 = percentile(turn_times, 50) if turn_times else 0.0
        turn_p90 = percentile(turn_times, 90) if turn_times else 0.0
        record = {"kind": "m3", "path": path.upper(), "n": n,
                  "rss_peak_mb": rss_peak[0], "rss_per_session_mb": rss_per_session,
                  "cpu_peak_pct": cpu_peak_pct[0], "turn_p50": turn_p50, "turn_p90": turn_p90,
                  "fail": fails[0], "http429": http429[0], "sessions_failed": sessions_failed[0],
                  "fail_reasons": dict(fail_reasons)}
        if out is not None:
            write_jsonl(out, record)
        print(f"M3 path={record['path']} N={n} rss_peak_mb={rss_peak[0]:.1f} "
              f"rss_per_session_mb={rss_per_session:.1f} cpu_peak_pct={cpu_peak_pct[0]:.1f} "
              f"turn_p50={turn_p50:.3f} turn_p90={turn_p90:.3f} fail={fails[0]} "
              f"http429={http429[0]} sessions_failed={sessions_failed[0]}/{n} "
              f"fail_reasons={fail_reasons}")
        summaries.append(record)

    if len(summaries) >= 2:
        points = [(float(s["n"]), s["rss_peak_mb"]) for s in summaries]
        a, b = fit_line(points)
        extrapolated_10 = a + b * 10
        r4_line = (f"R4 pending server size" if server_ram_gb is None else
                  f"R4 {'PASS' if extrapolated_10 <= 0.5 * server_ram_gb * 1024 else 'FAIL'} "
                  f"({extrapolated_10:.1f} MB vs 50% of {server_ram_gb} GB)")
        print(f"M3 extrapolated N=10: rss_mb={extrapolated_10:.1f} (a={a:.1f}, b={b:.1f}) {r4_line}")

    return summaries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-home", default="")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--key-env", default="API_SERVER_KEY")
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", required=True)
    ap.add_argument("--path", choices=("a", "b"), default="a")
    ap.add_argument("--sessions", default="1,2,3")
    ap.add_argument("--server-ram-gb", type=float, default=None)
    ap.add_argument("--stagger-s", type=float, default=0.0,
                    help="Delay between starting each session's thread, to spread the initial "
                         "dispatch burst instead of firing all N at once (Infomaniak "
                         "rate-limits a synchronized N-session burst)")
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "cothinker-measure" / "m3.jsonl"))
    a = ap.parse_args()

    key = os.environ.get(a.key_env, "")
    if not key:
        raise SystemExit(f"m3_load: {a.key_env} is not set")
    api = Api(f"http://127.0.0.1:{a.port}", key)
    worker_cmd = [worker_interpreter(), str(ROOT / "gateway" / "worker.py"),
                 "--hermes-home", a.profile_home, "--model", a.model, "--provider", a.provider]
    sessions = tuple(int(x) for x in a.sessions.split(",") if x.strip())
    run(api, worker_cmd, sessions=sessions, path=a.path, out=Path(a.out),
        server_ram_gb=a.server_ram_gb, gateway_port=a.port, stagger_s=a.stagger_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
