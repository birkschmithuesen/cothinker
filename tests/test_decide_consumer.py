# CoThinker — tests of the level-1 decider wiring in the consumer
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""blocks/decisions_tasks_questions/decider.py and its hook in gateway/consumer.py.

Two halves. First the background worker in-process, with injected
backends and decide functions (fast, deterministic): one line per
submitted block, text-free, a failing decider costs nothing, a full queue
drops instead of blocking. Then the real consumer as a subprocess against
a fake STT server, with `COTHINKER_DECIDER` unset (nothing changes, no
file), `=fake` (one line per finished block, canary absent everywhere
except the raw transcript) and `=jev` without a key (decider off, the
protocol complete). No real network anywhere.

Fixture text is synthetic German, invented for this file.

    python3 -m pytest -q tests/test_decide_consumer.py
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO / "gateway"))

import pytest  # noqa: E402
from script_checks import check, collected, done  # noqa: E402
import blocks.decisions_tasks_questions.decide as d  # noqa: E402
import blocks.decisions_tasks_questions.decider as dz  # noqa: E402
import blocks.decisions_tasks_questions.policy as pol  # noqa: E402
import blocks.decisions_tasks_questions.questions as q  # noqa: E402
from shared.events import EventLog, is_token  # noqa: E402

KANARI = "KANARIENVOGEL"
IDS = [f.id for f in q.QUESTIONS]


def lines(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(z) for z in path.read_text(encoding="utf-8").splitlines()
            if z.strip()]


def wait_for(pred, timeout: float = 5.0) -> bool:
    ende = time.monotonic() + timeout
    while time.monotonic() < ende:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def walk_strings(obj):
    """Every string value (and key) in a parsed JSON line."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)


tmp = Path(tempfile.mkdtemp(prefix="cothinker-decider-"))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)

# ================================================ 1. the worker, in-process
print("DeciderWorker: one text-free line per block")
pfad = tmp / "decisions-2026-09-26.jsonl"
spur = tmp / "stage-events.jsonl"
protokoll: list = []
w = dz.DeciderWorker(d.FakeBackend(), pfad, events=EventLog(spur),
                     log=lambda *a: protokoll.append(" ".join(map(str, a))))
w.start()
bloecke = [f"Block {i}: wir reden ueber den {KANARI} und den Teich Nummer {i}."
           for i in range(1, 4)]
for i, text in enumerate(bloecke):
    check(f"submit {i + 1} is accepted",
          w.submit(text, reason="pause", chars=len(text),
                   heading=f"Ueberschrift {KANARI}",
                   topics=[f"Thema {KANARI}"], hints=[f"Frage {KANARI}?"],
                   at=datetime.now().astimezone()) is True)
check("three lines arrive", wait_for(lambda: len(lines(pfad)) == 3),
      str(len(lines(pfad))))
w.finish(timeout=5)
zeilen = lines(pfad)
check("block indices are 1..3 in order",
      [z.get("block") for z in zeilen] == [1, 2, 3], str(zeilen)[:200])
z0 = zeilen[0] if zeilen else {}
check("line carries questions_version", z0.get("questions_version") == q.QUESTIONS_VERSION)
check("line carries questions_hash", z0.get("questions_hash") == q.QUESTIONS_HASH)
check("line carries backend token", z0.get("backend") == "fake")
check("line carries the block's reason and chars",
      z0.get("reason") == "pause" and z0.get("chars") == len(bloecke[0]), str(z0)[:200])
check("line carries an ISO timestamp", isinstance(z0.get("at"), str)
      and len(z0.get("at", "")) >= 19 and z0["at"][4] == "-", str(z0.get("at")))
for key in ("tokens_in", "tokens_out", "tokens_est", "cost_usd", "ms",
            "wait_ms", "errors", "dropped_blocks", "trimmed_chars", "drops"):
    check(f"line carries numeric {key}",
          isinstance(z0.get(key), (int, float)) and not isinstance(z0.get(key), bool),
          repr(z0.get(key)))
check("line carries boolean truncated", isinstance(z0.get("truncated"), bool))
check("line schema version is 1", z0.get("v") == 1)
check("no call-level error on a good call", "error" not in z0)
antworten = z0.get("answers") or []
check("one answer per question, in set order",
      [a.get("question_id") for a in antworten] == IDS, str(antworten)[:200])
check("every answer has p or choice, confidence, ms",
      all((("p" in a) != ("choice" in a)) and "confidence" in a and "ms" in a
          for a in antworten), str(antworten)[:300])
check("the phase answer is one of its choices",
      any(a.get("choice") in q.PHASES for a in antworten))
alle = "\n".join(pfad.read_text(encoding="utf-8").splitlines())
check("the canary is nowhere in the decisions file", KANARI not in alle)
check("no block text in the decisions file", "Teich" not in alle and "Ueberschrift" not in alle)
check("every string in every line is a token or the timestamp",
      all(is_token(s) or s == z.get("at") for z in zeilen for s in walk_strings(z)),
      str([s for z in zeilen for s in walk_strings(z)
           if not is_token(s) and s != z.get("at")])[:200])
ev = lines(spur)
start = [e for e in ev if e.get("event") == "decider_started"]
check("decider_started emitted exactly once", len(start) == 1, str(ev)[:300])
if start:
    check("decider_started carries questions_hash",
          start[0].get("questions_hash") == q.QUESTIONS_HASH, str(start[0]))
    check("decider_started carries questions_version",
          start[0].get("questions_version") == q.QUESTIONS_VERSION, str(start[0]))
    check("decider_started carries backend", start[0].get("backend") == "fake",
          str(start[0]))
ende = [e for e in ev if e.get("event") == "decider_finished"]
check("decider_finished emitted with the count",
      len(ende) == 1 and ende[0].get("count") == 3, str(ende))
check("the canary is nowhere in the event trace",
      KANARI not in spur.read_text(encoding="utf-8"))
check("the canary is nowhere in the log", KANARI not in "\n".join(protokoll))

print("\ndecision_line: bools are not numbers, choices are the question's own")
from types import SimpleNamespace  # noqa: E402

boolig = SimpleNamespace(
    backend="fake", answers=[
        {"question_id": "phase", "choice": "einstieg", "ms": True},
        {"question_id": "phase", "choice": "erfunden"},
        {"question_id": "neues_thema", "choice": "einstieg", "p": 0.4},
        {"question_id": "unbekannt", "choice": "einstieg"}],
    errors=True, tokens_in=True, tokens_out=True, tokens_est=True,
    dropped_blocks=True, trimmed_chars=True, cost_usd=True, ms=True,
    truncated=False)
zb = dz.decision_line(block=1, at=datetime.now().astimezone(), reason="pause",
                      chars=True, result=boolig, wait_ms=True, drops=True)
for key in ("tokens_in", "tokens_out", "tokens_est", "cost_usd", "ms",
            "wait_ms", "errors", "dropped_blocks", "trimmed_chars", "drops",
            "chars"):
    check(f"decision_line: a bool in {key} becomes 0, not True",
          zb.get(key) == 0 and not isinstance(zb.get(key), bool),
          repr(zb.get(key)))
ab = zb.get("answers") or []
check("decision_line: an answer's bool ms becomes 0",
      ab and ab[0].get("ms") == 0 and not isinstance(ab[0].get("ms"), bool),
      str(ab)[:200])
check("decision_line: a choice among the question's own choices is kept",
      len(ab) == 4 and ab[0].get("choice") == "einstieg", str(ab)[:300])
check("decision_line: a token-shaped choice the question does not have is "
      "dropped", len(ab) == 4 and "choice" not in ab[1], str(ab)[:300])
check("decision_line: a choice on a noul question is dropped, its p kept",
      len(ab) == 4 and "choice" not in ab[2] and ab[2].get("p") == 0.4,
      str(ab)[:300])
check("decision_line: a choice on an unknown question id is dropped",
      len(ab) == 4 and "choice" not in ab[3], str(ab)[:300])

print("\nDeciderWorker: context window and stage context reach decide()")
gesehen: list = []


def mitschreiben(state, **kw):
    gesehen.append((state.block, list(state.window()), state.heading,
                    list(state.topics), list(state.hints)))
    return d.decide(state, **kw)


pfad2 = tmp / "decisions-ctx.jsonl"
w2 = dz.DeciderWorker(d.FakeBackend(), pfad2, context_blocks=2,
                      decide_fn=mitschreiben, log=lambda *a: None)
w2.start()
for t in ("eins eins", "zwei zwei", "drei drei", "vier vier"):
    w2.submit(t, heading="H", topics=["a", "b", "c", "d"], hints=["f"])
w2.finish(timeout=5)
check("four calls", len(gesehen) == 4, str(len(gesehen)))
if len(gesehen) == 4:
    check("current block is the submitted one", gesehen[3][0] == "vier vier")
    check("window holds the last N=2 previous blocks, oldest first",
          gesehen[3][1] == ["zwei zwei", "drei drei"], str(gesehen[3][1]))
    check("first call has an empty window", gesehen[0][1] == [])
    check("heading / topics / hints are handed through",
          gesehen[3][2] == "H" and gesehen[3][3][-3:] == ["b", "c", "d"]
          and gesehen[3][4] == ["f"], str(gesehen[3]))

print("\nDeciderWorker: a failing decider costs nothing")


def kaputt(state, **kw):
    raise RuntimeError(f"boom {KANARI} {state.block}")


def decide_error(state, **kw):
    raise d.DecideError(f"http failed {KANARI}", "http_500")


for name, fn, token in (("RuntimeError", kaputt, "RuntimeError"),
                        ("DecideError", decide_error, "http_500")):
    pf = tmp / f"decisions-{name}.jsonl"
    log3: list = []
    w3 = dz.DeciderWorker(d.FakeBackend(), pf, decide_fn=fn,
                          log=lambda *a: log3.append(" ".join(map(str, a))))
    w3.start()
    ok = w3.submit(f"Ein Block mit {KANARI}.", reason="wake", chars=20)
    ok2 = w3.submit(f"Noch einer mit {KANARI}.", reason="pause", chars=20)
    w3.finish(timeout=5)
    zz = lines(pf)
    check(f"{name}: submit still accepted", ok and ok2)
    check(f"{name}: one line per block even on failure", len(zz) == 2, str(zz)[:200])
    check(f"{name}: the line carries the error token",
          all(z.get("error") == token for z in zz), str(zz)[:200])
    check(f"{name}: no answers on a failed call",
          all(z.get("answers") == [] for z in zz), str(zz)[:200])
    check(f"{name}: canary not in file or log",
          KANARI not in pf.read_text(encoding="utf-8")
          and KANARI not in "\n".join(log3), "\n".join(log3)[:200])
    check(f"{name}: failures counted", w3.failed == 2, str(w3.failed))

print("\nDeciderWorker: a full queue drops, never blocks")
halt = threading.Event()


def langsam(state, **kw):
    halt.wait(5)
    return d.decide(state, **kw)


pf4 = tmp / "decisions-voll.jsonl"
w4 = dz.DeciderWorker(d.FakeBackend(), pf4, decide_fn=langsam, queue_max=2,
                      log=lambda *a: None)
w4.start()
t0 = time.monotonic()
angenommen = [w4.submit(f"Block {i} {KANARI}") for i in range(8)]
dauer = time.monotonic() - t0
check("eight submits return at once", dauer < 0.5, f"{dauer:.3f}s")
check("some submits are dropped", angenommen.count(False) >= 4, str(angenommen))
check("drops are counted", w4.drops == angenommen.count(False), str(w4.drops))
halt.set()
w4.finish(timeout=5)
zz = lines(pf4)
check("still one line per block: dropped blocks get a 'dropped' line",
      sorted(z.get("block") for z in zz) == list(range(1, 9)),
      str(sorted(z.get("block") for z in zz)))
check("dropped lines carry error=dropped and no answers",
      sum(1 for z in zz if z.get("error") == "dropped" and z.get("answers") == [])
      == w4.drops, str(zz)[:300])
check("canary not in the file", KANARI not in pf4.read_text(encoding="utf-8"))

print("\nDeciderWorker: submit after finish, and nonsense input")
check("submit after finish is refused", w4.submit("spaet") is False)
w5 = dz.DeciderWorker(d.FakeBackend(), tmp / "decisions-x.jsonl", log=lambda *a: None)
w5.start()
try:
    r = w5.submit(None, reason="Ein ganzer Satz als Grund", chars="viele",
                  heading=object(), topics=None, hints=5, at="gestern")
    check("submit with nonsense arguments does not raise", True)
except Exception as e:  # noqa: BLE001
    check("submit with nonsense arguments does not raise", False, repr(e))
w5.finish(timeout=5)
zz = lines(tmp / "decisions-x.jsonl")
check("a non-token reason is not written", all("Satz" not in json.dumps(z) for z in zz),
      str(zz)[:200])
check("a non-timestamp 'at' is not written",
      all("gestern" not in json.dumps(z) for z in zz), str(zz)[:200])

print("\nfrom_env: off by default, never raises")
log6: list = []
check("unset -> None", dz.from_env(tmp / "a.jsonl", env={}, log=log6.append) is None)
check("empty -> None", dz.from_env(tmp / "a.jsonl", env={"COTHINKER_DECIDER": ""},
                                   log=log6.append) is None)
check("unset logs nothing", log6 == [], str(log6))
check("unset creates no file", not (tmp / "a.jsonl").exists())
for wert in ("jev", "laya", "Unsinn"):
    lg: list = []
    try:
        r = dz.from_env(tmp / "b.jsonl", env={"COTHINKER_DECIDER": wert},
                        log=lambda *a: lg.append(" ".join(map(str, a))))
        check(f"{wert} without means -> None, no raise", r is None, repr(r))
        check(f"{wert}: logged that the decider is off", any("decider off" in z for z in lg),
              str(lg))
    except Exception as e:  # noqa: BLE001
        check(f"{wert} without means -> None, no raise", False, repr(e))
w6 = dz.from_env(tmp / "c.jsonl", env={"COTHINKER_DECIDER": "fake"}, log=lambda *a: None)
check("fake -> a worker", isinstance(w6, dz.DeciderWorker), repr(w6))

print("\nstage_context: cheap, never raises")


class _M:
    def __init__(self, t):
        self.title = t


class _Q:
    text = "Wer bestellt?"
    state = "open"


class _S:
    headline = "Nistkaesten"
    history = [_M("a"), _M(""), _M("b"), _M("c"), _M("d")]
    question = _Q()


ctx = dz.stage_context(_S())
check("heading", ctx.get("heading") == "Nistkaesten", str(ctx))
check("last three topic titles", ctx.get("topics") == ["b", "c", "d"], str(ctx))
check("open question as hint", ctx.get("hints") == ["Wer bestellt?"], str(ctx))
check("broken state -> empty context, no raise", dz.stage_context(object()) == {}
      or all(not v for v in dz.stage_context(object()).values()))
check("None -> empty context", dz.stage_context(None) == {}
      or all(not v for v in dz.stage_context(None).values()))

# =========================================== 2. the real consumer, subprocess
SAETZE = [f"Erster Satz ueber den {KANARI}, lang genug zum Schneiden.",
          f"Zweiter Satz ueber den {KANARI}, ebenfalls lang genug.",
          f"Dritter Satz ueber den {KANARI}, damit es weitergeht."]


def stt_server(saetze):
    class _STT(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path != "/events":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for satz in saetze:
                    ev = {"recognizer_id": "left", "type": "final", "text": satz}
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(1.0)
                while True:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(0.3)
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _STT)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def run_consumer(prefix: str, decider: str | None,
                 flags: tuple = ("--no-agent",)) -> tuple:
    """One consumer run; returns (run_dir, project_dir, stdout)."""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    projekt = root / "entities" / "testentity" / "projekte" / "testprojekt"
    projekt.mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True)
    srv, port = stt_server(SAETZE)
    env = dict(os.environ, COTHINKER_ENTITIES=str(root / "entities"),
               PYTHONUNBUFFERED="1")
    env.pop("COTHINKER_DECIDER", None)
    env.pop("OPENROUTER_API_KEY", None)
    if decider is not None:
        env["COTHINKER_DECIDER"] = decider
    lauf = projekt / "sessions" / f"{datetime.now().date().isoformat()}-testprojekt"
    p = subprocess.Popen(
        [sys.executable, str(REPO / "gateway" / "consumer.py"),
         "--stt", f"http://127.0.0.1:{port}", "--project", "testprojekt",
         "--pause", "0.4", "--retry-delay", "0.2", "--commit-every", "100000",
         "--check-every", "0", *flags],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env)
    prot = lauf / "protokoll.md"
    wait_for(lambda: prot.exists() and SAETZE[-1] in prot.read_text(encoding="utf-8"),
             timeout=30)
    time.sleep(1.2)                 # let the last pause cut its block
    p.send_signal(signal.SIGINT)
    try:
        out = p.communicate(timeout=60)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        out = p.communicate()[0] + "\nTIMEOUT"
    srv.shutdown()
    return lauf, projekt, out, p.returncode


heute = datetime.now().date().isoformat()

print("\nconsumer, COTHINKER_DECIDER unset: nothing changes")
lauf, projekt, out, rc = run_consumer("cothinker-dec-off-", None)
check("exits cleanly", rc == 0, out[-400:])
check("no decisions file anywhere",
      not list(projekt.rglob("decisions-*.jsonl")),
      str(list(projekt.rglob("decisions-*.jsonl"))))
spur_off = (lauf / "stage-events.jsonl").read_text(encoding="utf-8") \
    if (lauf / "stage-events.jsonl").exists() else ""
check("no decider event in the trace", "decider_" not in spur_off)
check("no decider line in the log", "decider" not in out.lower(), out[-400:])
check("raw transcript complete",
      all(s in (lauf / "protokoll.md").read_text(encoding="utf-8") for s in SAETZE))

print("\nconsumer, COTHINKER_DECIDER=fake: one line per finished block, text-free")
lauf, projekt, out, rc = run_consumer("cothinker-dec-fake-", "fake")
check("exits cleanly", rc == 0, out[-400:])
check("no traceback", "Traceback" not in out, out[-600:])
entsch = lauf / f"decisions-{heute}.jsonl"
check("decisions file lies in the session folder, next to stage-events.jsonl",
      entsch.exists() and (lauf / "stage-events.jsonl").exists(),
      str(sorted(x.name for x in lauf.iterdir())) if lauf.is_dir() else "")
ev = lines(lauf / "stage-events.jsonl")
eingereiht = [e for e in ev if e.get("event") == "block_enqueued"]
zz = lines(entsch)
check("at least two blocks were cut", len(eingereiht) >= 2, str(len(eingereiht)))
check("one decisions line per finished block", len(zz) == len(eingereiht),
      f"{len(zz)} lines, {len(eingereiht)} blocks")
check("block indices 1..n", sorted(z.get("block") for z in zz)
      == list(range(1, len(zz) + 1)), str([z.get("block") for z in zz]))
check("every line answered all questions",
      all([a.get("question_id") for a in z.get("answers", [])] == IDS for z in zz))
start = [e for e in ev if e.get("event") == "decider_started"]
check("decider_started once, with hash, version and backend",
      len(start) == 1 and start[0].get("questions_hash") == q.QUESTIONS_HASH
      and start[0].get("questions_version") == q.QUESTIONS_VERSION
      and start[0].get("backend") == "fake", str(start))
namen = [e.get("event") for e in ev]
check("decider_started comes after meeting_started",
      "meeting_started" in namen and "decider_started" in namen
      and namen.index("meeting_started") < namen.index("decider_started"), str(namen))
check("decider_finished before meeting_ended",
      "decider_finished" in namen and "meeting_ended" in namen
      and namen.index("decider_finished") < namen.index("meeting_ended"), str(namen))
check("CANARY: not in the decisions file",
      KANARI not in entsch.read_text(encoding="utf-8") if entsch.exists() else False)
check("CANARY: not in the event trace",
      KANARI not in (lauf / "stage-events.jsonl").read_text(encoding="utf-8"))
check("CANARY: but in the raw transcript (the canary was really spoken)",
      KANARI in (lauf / "protokoll.md").read_text(encoding="utf-8"))
check("no decisions file in the project folder (session layout)",
      not list(projekt.glob("decisions-*.jsonl")))

print("\nconsumer, COTHINKER_DECIDER=jev without a key: decider off, protocol whole")
lauf, projekt, out, rc = run_consumer("cothinker-dec-nokey-", "jev")
check("exits cleanly", rc == 0, out[-400:])
check("no traceback", "Traceback" not in out, out[-600:])
check("the log says the decider is off", "decider off" in out, out[-600:])
check("no decisions file", not list(projekt.rglob("decisions-*.jsonl")))
check("raw transcript complete",
      all(s in (lauf / "protokoll.md").read_text(encoding="utf-8") for s in SAETZE))

print("\nconsumer --policy: on only where every part it needs is there")
lauf, projekt, out, rc = run_consumer("cothinker-pol-noagent-", "fake",
                                      ("--no-agent", "--policy"))
check("--policy with --no-agent: exits cleanly, one log line, off",
      rc == 0 and "policy off: nothing would formulate" in out
      and "policy on" not in out and "Traceback" not in out, out[-600:])
lauf, projekt, out, rc = run_consumer("cothinker-pol-nodec-", None,
                                      ("--dry-agent", "--policy"))
check("--policy without COTHINKER_DECIDER: off, says why",
      rc == 0 and "policy off: needs COTHINKER_DECIDER" in out
      and "Traceback" not in out, out[-600:])
lauf, projekt, out, rc = run_consumer("cothinker-pol-on-", "fake",
                                      ("--dry-agent", "--policy"))
check("--policy with the decider and a (dry) agent: on, with the file's mode",
      rc == 0 and "policy on: mode sitzung" in out, out[-600:])
check("turns run through the wiring without a traceback",
      "Traceback" not in out and "policy end_turn failed" not in out
      and "policy hook failed" not in out, out[-800:])
check("raw transcript complete with --policy",
      all(s in (lauf / "protokoll.md").read_text(encoding="utf-8") for s in SAETZE))
check("CANARY: not in the event trace with --policy",
      KANARI not in (lauf / "stage-events.jsonl").read_text(encoding="utf-8"))

print("\nsession_paths knows the decisions file in both layouts")
sys.path.insert(0, str(REPO))
from gateway import consumer as k  # noqa: E402
sp = Path(tempfile.mkdtemp(prefix="cothinker-dec-sp-"))
atexit.register(shutil.rmtree, sp, ignore_errors=True)
orte = k.session_paths(sp, "2026-09-26", "Runde")
check("session layout: in the session folder, dated",
      orte["decisions"] == orte["folder"] / "decisions-2026-09-26.jsonl",
      str(orte.get("decisions")))
(sp / "protokoll-2026-09-26.md").write_text("x", encoding="utf-8")
flach = k.session_paths(sp, "2026-09-26", "Runde")
check("flat layout: in the project folder, like timing.jsonl",
      flach["flat"] and flach["decisions"] == sp / "decisions-2026-09-26.jsonl",
      str(flach.get("decisions")))


# ================================================ level 2 hook (MM-B)
print("\nDeciderWorker(on_result=...): level 2 hears every answered block")
pf_h = tmp / "decisions-hook.jsonl"
gehoert: list = []
w_h = dz.DeciderWorker(d.FakeBackend(), pf_h, log=lambda *a: None,
                       on_result=lambda r, s, g: gehoert.append((r, s, g)))
w_h.start()
w_h.submit("Wir prüfen den Termin.", reason="pause")
w_h.submit("Robo, fass das zusammen.", reason="wake")
w_h.finish(timeout=5)
check("the hook got one call per block", len(gehoert) == 2, str(len(gehoert)))
check("with a Result each", all(isinstance(r, d.Result) for r, _, _ in gehoert))
check("and the decide.State whose block is the submitted text",
      [s.block for _, s, _ in gehoert]
      == ["Wir prüfen den Termin.", "Robo, fass das zusammen."]
      and all(isinstance(s, d.State) for _, s, _ in gehoert))
check("and the submit reason", [g for _, _, g in gehoert] == ["pause", "wake"],
      str([g for _, _, g in gehoert]))
check("the lines are written as before", len(lines(pf_h)) == 2)


def _kaputter_hook(r, s, g):
    raise RuntimeError(f"hook kaputt {KANARI}")


pf_k = tmp / "decisions-hook-kaputt.jsonl"
said_k: list = []
w_k = dz.DeciderWorker(d.FakeBackend(), pf_k, on_result=_kaputter_hook,
                       log=lambda *a: said_k.append(" ".join(map(str, a))))
w_k.start()
check_ok = w_k.submit("Erster Block.") and w_k.submit("Zweiter Block.")
w_k.finish(timeout=5)
check("a raising hook costs no line", check_ok and len(lines(pf_k)) == 2,
      str(len(lines(pf_k))))
check("it is logged by type name, never by message",
      any("policy hook failed" in z and "RuntimeError" in z for z in said_k)
      and not any(KANARI in z for z in said_k), str(said_k))
w_n = dz.DeciderWorker(d.FakeBackend(), tmp / "decisions-nohook.jsonl",
                       log=lambda *a: None)
check("the hook is off by default", w_n.on_result is None)


def _decide_kaputt(snap, backend=None):
    raise d.DecideError("down", "timeout")


pf_f = tmp / "decisions-hook-failed.jsonl"
gehoert_f: list = []
w_f = dz.DeciderWorker(d.FakeBackend(), pf_f, decide_fn=_decide_kaputt,
                       log=lambda *a: None,
                       on_result=lambda r, s, g: gehoert_f.append(r))
w_f.start()
w_f.submit("Ein Block, dessen Entscheidung scheitert.", reason="pause")
w_f.finish(timeout=5)
check("a failed decide (result None) writes its line but never calls the hook",
      len(lines(pf_f)) == 1 and gehoert_f == [], f"{len(lines(pf_f))} {gehoert_f}")


# ============================ level 2 through the real consumer (MM-B)
print("\nconsumer --policy with a recording worker: the orders reach the prompt")
# A worker stand-in that answers every turn and keeps the prompts it got,
# so the test can read what the consumer really sent. Mode `fail` answers
# every turn with a failed run.
AUFZEICHNER = tmp / "recording_worker.py"
AUFZEICHNER.write_text('''\
import argparse, json, sys
ap = argparse.ArgumentParser()
ap.add_argument("--session-id", required=True)
ap.add_argument("--log", required=True)
ap.add_argument("--mode", default="ok")
a = ap.parse_args()
def send(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\\n")
    sys.stdout.flush()
send({"type": "ready", "session_id": a.session_id})
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("type") != "turn":
        continue
    with open(a.log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg.get("prompt", "")) + "\\n")
    ok = a.mode == "ok"
    send({"type": "result", "id": msg.get("id"), "ok": ok, "failed": not ok,
          "partial": False, "final": '{"buehne": []}' if ok else "",
          "error": None if ok else "provider refused"})
''', encoding="utf-8")

# SAETZE[0] crosses thresholds under the fake decider (sha256-based, so
# fixed): in `sitzung` it routes to autonomous orders -- among them a
# tag_entry `aufgabe`. The order rides along in a following turn.
_route0 = pol.route(d.decide(d.State(block=SAETZE[0]), q.QUESTIONS,
                             backend=d.FakeBackend()),
                    "sitzung", pol.PolicyState())
check("fixture: the fake decider orders something for the first sentence",
      any(x.kind == "tag_entry" for x in _route0),
      str([x.kind for x in _route0]))


def run_recorded(prefix: str, mode: str) -> tuple:
    log_pfad = tmp / f"prompts-{prefix}.jsonl"
    befehl = (f'"{sys.executable}" "{AUFZEICHNER}" --log "{log_pfad}" '
              f'--mode {mode}')
    lauf_r, _, out_r, rc_r = run_consumer(
        prefix, "fake", ("--policy", "--worker-command", befehl))
    prompts = lines(log_pfad)
    return lauf_r, out_r, rc_r, prompts


lauf_ok, out_ok, rc_ok, prompts_ok = run_recorded("cothinker-pol-rec-ok-", "ok")
check("exits cleanly, policy on, no traceback",
      rc_ok == 0 and "policy on: mode sitzung" in out_ok
      and "Traceback" not in out_ok, out_ok[-800:])
check("the worker got at least two turns", len(prompts_ok) >= 2,
      str(len(prompts_ok)))
check("a prompt the worker really received carries a level-2 order",
      any("AUFTRAG A" in p for p in prompts_ok),
      "\n---\n".join(p[-400:] for p in prompts_ok))
ev_ok = lines(lauf_ok / "stage-events.jsonl")
aktionen = [e for e in ev_ok if e.get("event") == "policy_action"]
ends_ok = [e for e in ev_ok if e.get("event") == "pending_end"
           and e.get("kind") in ("entry", "topic", "decision", "main")]
check("the policy acted", bool(aktionen), str(aktionen))
check("an ok run with no proposal settles its orders `unanswered`, never "
      "`failed`",
      any(e.get("outcome") == "unanswered" for e in ends_ok)
      and not any(e.get("outcome") == "failed" for e in ends_ok),
      str(ends_ok))
buehne_ok = json.loads((lauf_ok / "buehne.json").read_text(encoding="utf-8"))
check("nothing of level 2 is left standing in the final stage file",
      not buehne_ok.get("pending"), str(buehne_ok.get("pending")))
check("CANARY: not in the event trace with the recording worker",
      KANARI not in (lauf_ok / "stage-events.jsonl").read_text(encoding="utf-8"))

lauf_f, out_f, rc_f, prompts_f = run_recorded("cothinker-pol-rec-fail-", "fail")
ev_f = lines(lauf_f / "stage-events.jsonl")
ends_f = [e for e in ev_f if e.get("event") == "pending_end"
          and e.get("kind") in ("entry", "topic", "decision", "main")]
check("a failed run settles the orders it carried `failed`",
      rc_f == 0 and any("AUFTRAG A" in p for p in prompts_f)
      and any(e.get("outcome") == "failed" for e in ends_f)
      and not any(e.get("outcome") == "unanswered" for e in ends_f),
      f"{ends_f} {out_f[-600:]}")

done()


@pytest.mark.parametrize("name,ok,info", collected())
def test_check(name, ok, info):
    assert ok, info
