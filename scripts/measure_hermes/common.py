"""Clock, /proc sampling, JSONL output and a memory guard for M1-M3.

Stdlib only, Linux /proc only -- the measurement scripts never import
psutil or any other dependency the installed Hermes does not already need.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
from pathlib import Path


def now() -> float:
    return time.monotonic()


def worker_interpreter() -> str:
    """The Python that can import Hermes, for the `gateway/worker.py`
    subprocess M1-M3 spawn (path B).

    `gateway/consumer.py: worker_interpreter()` assumes `hermes` on PATH
    sits directly inside the venv next to its own `python3` -- true for a
    bare venv install. This host's `hermes` (live check,
    2026-10-22) is a thin bash *wrapper* at `~/.local/bin/hermes` that
    unsets PYTHONPATH/PYTHONHOME and execs the real
    `.../hermes-agent/venv/bin/hermes`, which is itself a python script
    with the venv's `python3` on its shebang line -- consumer.py's
    sibling-lookup finds no `python3` next to the WRAPPER and silently
    falls back to `sys.executable` (a plain system python3 without
    hermes-agent/openai installed; confirmed: `import openai` fails
    there). Resolve one more hop: if `hermes` is a wrapper script
    (`#!/usr/bin/env bash` or similar, not an ELF/venv shebang), grep its
    `exec` target and recurse from there.

    `COTHINKER_WORKER_PYTHON`, if set, is returned unchanged and skips all
    of the above (live check on the production server, 2026-10-22):
    a freshly `curl | bash`-installed Hermes is `pm`-managed and its
    `~/.local/bin/hermes` is a CHAIN of `#!/bin/sh` wrappers with an
    UNQUOTED `exec` target (the regex above requires a quoted one) that
    ends in a bootstrap-only interpreter (no `openai`, no `run_agent` --
    the real venv is found dynamically at runtime by importing
    `hermes_bootstrap`, not by following a fixed sibling path). No fixed
    hop count reaches a working interpreter on that layout, so this host
    class needs the real venv's `python3` path supplied directly instead
    of guessed.
    """
    override = os.environ.get("COTHINKER_WORKER_PYTHON", "").strip()
    if override:
        return override
    exe = shutil.which("hermes")
    seen: set[str] = set()
    while exe and exe not in seen:
        seen.add(exe)
        path = Path(exe)
        try:
            head = path.read_bytes()[:4096]
        except OSError:
            break
        if head.startswith(b"#!/usr/bin/env bash") or head.startswith(b"#!/bin/bash"):
            text = head.decode("utf-8", "replace")
            m = __import__("re").search(r'exec\s+"([^"]+)"', text)
            if not m:
                break
            exe = m.group(1)
            continue
        candidate = path.resolve().parent / "python3"
        if candidate.exists():
            return str(candidate)
        break
    return sys.executable


def mem_available_mb() -> int:
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found in /proc/meminfo")


def guard_memory(min_mb: int = 1500) -> None:
    available = mem_available_mb()
    if available < min_mb:
        raise SystemExit(
            f"memory guard: {available} MB available < {min_mb} MB minimum")


def _vm_rss_kb(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _child_pids(pid: int) -> list[int]:
    task_dir = Path(f"/proc/{pid}/task")
    children: list[int] = []
    try:
        task_ids = list(task_dir.iterdir())
    except OSError:
        return children
    for task in task_ids:
        try:
            raw = (task / "children").read_text()
        except OSError:
            continue
        children.extend(int(x) for x in raw.split())
    return children


def rss_mb(pid: int, include_children: bool = True) -> float:
    seen: set[int] = set()
    stack = [pid]
    total_kb = 0
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        total_kb += _vm_rss_kb(p)
        if include_children:
            stack.extend(_child_pids(p))
    return total_kb / 1024


def cpu_seconds(pid: int) -> float:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return 0.0
    # The comm field between the last ')' and the pid can itself contain
    # spaces and parentheses, so fields are counted from there, not split().
    rest = raw[raw.rfind(")") + 2:]
    fields = rest.split()
    try:
        utime, stime = int(fields[11]), int(fields[12])
    except (IndexError, ValueError):
        return 0.0
    clk_tck = os.sysconf("SC_CLK_TCK")
    return (utime + stime) / clk_tck


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("percentile: empty values")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(p / 100 * len(ordered))))
    return ordered[rank - 1]


def _validate_jsonl_value(value) -> None:
    if isinstance(value, str):
        if len(value) > 32:
            raise ValueError(
                f"write_jsonl: string value too long ({len(value)} chars): "
                f"{value[:40]!r}")
    elif isinstance(value, dict):
        for v in value.values():
            _validate_jsonl_value(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _validate_jsonl_value(v)
    elif isinstance(value, (int, float, bool)) or value is None:
        pass
    else:
        raise ValueError(f"write_jsonl: unsupported value type {type(value)!r}")


def write_jsonl(path: Path, record: dict) -> None:
    for value in record.values():
        _validate_jsonl_value(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
