"""Fake agent worker for consumer tests.

Speaks the line protocol of gateway/worker.py (`WorkerClient`): one JSON
object per line. It prints `ready`, then answers every `turn` message on
stdin with a `result` that carries an empty final answer (a run without a
change is a good run). Unknown arguments (`--session-id`, `--model`, ...)
are accepted and ignored. With FAKE_WORKER_JSON=1 the final answer is a
fenced JSON reply instead (see below). Turns are appended to the file named by
FAKE_WORKER_LOG, when set.

FAKE_WORKER_ORDER_ACTIONS (a JSON object, `{needle: action}`) answers the
level-2 orders a prompt carries: for every `AUFTRAG <id>: ...` line, the
first needle found in that line contributes its action with `"order": <id>`
added. The ids depend on how the decider's results split across turns, so
a fixed reply could not name them.
"""
import json
import os
import re
import sys

ORDER_LINE = re.compile(r"AUFTRAG (A\d+):([^\n]*)")


JSON_REPLY = "```json\n" + json.dumps({
    "verlauf": ["Die Runde bespricht den Zeitplan."],
    "entscheidungen": [],
    "aufgaben": [],
    "offene_fragen": [],
    "buehne": [],
}, ensure_ascii=False) + "\n```"


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def order_reply(prompt: str, actions: dict, verlauf=()) -> str:
    """The fenced JSON reply that answers the prompt's level-2 orders;
    `verlauf` are minutes items the reply carries besides."""
    buehne = []
    for oid, line in ORDER_LINE.findall(prompt):
        for needle, action in actions.items():
            if needle in line:
                buehne.append({**action, "order": oid})
                break
    return "```json\n" + json.dumps({
        "verlauf": list(verlauf), "entscheidungen": [], "aufgaben": [],
        "offene_fragen": [], "buehne": buehne,
    }, ensure_ascii=False) + "\n```"


def main() -> int:
    log_path = os.environ.get("FAKE_WORKER_LOG", "")
    order_actions = json.loads(os.environ.get("FAKE_WORKER_ORDER_ACTIONS")
                               or "null")
    order_verlauf = json.loads(os.environ.get("FAKE_WORKER_ORDER_VERLAUF")
                               or "[]")
    # JSON-reply mode (FAKE_WORKER_JSON=1): every turn answers with one
    # fenced JSON reply of the capture-bullets shape. FAKE_WORKER_REPLY
    # replaces the default reply text.
    final = ""
    if os.environ.get("FAKE_WORKER_JSON") == "1":
        final = os.environ.get("FAKE_WORKER_REPLY") or JSON_REPLY
    send({"type": "ready", "session_id": "fake"})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("type") != "turn":
            continue
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        reply = (order_reply(str(msg.get("prompt") or ""), order_actions,
                             order_verlauf)
                 if isinstance(order_actions, dict) else final)
        send({"type": "result", "id": msg.get("id"), "ok": True,
              "failed": False, "partial": False, "final": reply,
              "usage": {"input_tokens": 0, "output_tokens": 0},
              "error": None})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
