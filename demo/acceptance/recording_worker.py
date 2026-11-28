#!/usr/bin/env python3
"""Recording stand-in for a lane worker (offline replay only).

Speaks the gateway worker wire protocol (a `ready` line, then one `result`
line per `turn` line on stdin) without ever launching an agent. Every turn's
prompt is appended as one JSON line ``{"lane": ..., "text": ...}`` to the
file named by env ``RECORDING_WORKER_LOG``; the reply is env
``RECORDING_WORKER_REPLY`` (default ``ok``).

Unknown flags the gateway appends (``--hermes-home``, ``--no-history``,
``--model``, ...) are accepted and ignored. ``--lane`` is optional (the
gateway never passes it; the replay sets it per lane command).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--lane", default="unknown")
    args, _ignored = ap.parse_known_args(argv)
    log_path = os.environ.get("RECORDING_WORKER_LOG", "")
    reply = os.environ.get("RECORDING_WORKER_REPLY", "ok")

    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    send({"type": "ready", "session_id": args.session_id})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("type") != "turn":
            continue
        if log_path:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"lane": args.lane,
                                     "text": str(msg.get("prompt", ""))},
                                    ensure_ascii=False) + "\n")
        send({"type": "result", "id": msg.get("id"), "ok": True,
              "failed": False, "partial": False, "final": reply,
              "error": None, "usage": {"input_tokens": 0, "output_tokens": 0}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
