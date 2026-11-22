#!/usr/bin/env python3
# CoThinker — the agent worker client, real processes, never a real agent
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""`gateway.worker.WorkerClient`, `retry_import` and `profile_disabled_toolsets`.

    python3 -m pytest -q tests/test_worker.py

Seventeen predecessor checks are not ported here (a tool budget and a
turn-timeout-default feature this worker does not have): the rest ports
1:1, same assertion, pytest-shaped.

Every modal scenario below (die, deaf, slow, ...) runs against a private
mode-capable stand-in generated into a temp file at test time -- never
against the committed `tests/fake_worker.py` (that one is the consumer
test harness's own fixture, a single fixed behaviour, owned by a
different card).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from gateway.worker import WorkerClient, profile_disabled_toolsets, retry_import

ROOT = Path(__file__).resolve().parent.parent
WORKER = str(ROOT / "gateway" / "worker.py")

# ---------------------------------------------------------------------------
# A private, mode-capable worker stand-in -- NOT tests/fake_worker.py.
# ---------------------------------------------------------------------------

FAKE_SRC = '''\
#!/usr/bin/env python3
"""A worker stand-in. Speaks the protocol, never imports Hermes.

    python3 fake_worker_modes.py --session-id X [--mode MODE]

Modes: ok | fail | partial | apierror | httperror | silent | die |
       die-once | deaf | slow | slow-once | garbage

The "-once"-ish modes (die-once, deaf, slow-once) behave differently in
the process that follows a restart. That cannot be kept in memory -- the
process is gone -- so it is kept in a marker file named after the
session id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--session-id", required=True)
ap.add_argument("--mode", default="ok")
ap.add_argument("--fail-starts", type=int, default=0)
ap.add_argument("--sleep", type=float, default=0.0)
ap.add_argument("--final", default="")
ap.add_argument("--hermes-home", default="")
ap.add_argument("--no-history", action="store_true")
ap.add_argument("--prompt-log", default="")
ap.add_argument("--model", default="")
ap.add_argument("--provider", default="")
ap.add_argument("--usage-in", type=int, default=0)
ap.add_argument("--usage-out", type=int, default=0)
a = ap.parse_args()


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
    sys.stdout.flush()


def marker(tag: str) -> Path:
    return Path(tempfile.gettempdir()) / f"cothinker-fake-{tag}-{a.session_id}"


def first_process(tag: str) -> bool:
    try:
        marker(tag).touch(exist_ok=False)   # O_EXCL: only the first one wins
        return True
    except FileExistsError:
        return False


def launch_count(tag: str) -> int:
    p = marker(tag)
    try:
        n = int(p.read_text())
    except (OSError, ValueError, FileNotFoundError):
        n = 0
    p.write_text(str(n + 1))
    return n + 1


if a.fail_starts and launch_count("fail-starts") <= a.fail_starts:
    sys.exit(1)                            # never sends "ready"

send({"type": "ready", "session_id": a.session_id})

if a.mode == "deaf" and first_process("deaf"):
    sys.stdout.flush()
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)
    for _ in sys.stdin:                    # hangs on until the client lets go
        pass
    sys.exit(0)

slow_once = a.mode == "slow-once" and first_process("slow-once")

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("type") != "turn":
        continue
    tid = msg.get("id")
    if a.prompt_log:
        with open(a.prompt_log, "a", encoding="utf-8") as fh:
            fh.write(str(msg.get("prompt", "")) + "\\n\\x1e\\n")
    if a.mode == "die":
        sys.exit(3)
    if a.mode == "die-once" and first_process("die-once"):
        sys.exit(3)                        # only the first process dies
    if a.mode == "slow":
        time.sleep(30)
    if slow_once:
        time.sleep(30)                     # only the first process is slow
    if a.sleep:
        time.sleep(a.sleep)
    if a.mode == "garbage":
        sys.stdout.write("not json at all\\n")
        sys.stdout.flush()
        continue
    result = {"type": "result", "id": tid, "ok": True, "failed": False,
              "partial": False,
              "final": a.final or "OK \\u2014 mitgeschrieben", "error": None,
              "usage": {"input_tokens": a.usage_in, "output_tokens": a.usage_out}}
    if a.mode == "fail":
        result |= {"ok": False, "failed": True, "final": "",
                   "error": "provider refused"}
    elif a.mode == "partial":
        result |= {"ok": False, "partial": True, "final": "halb fertig"}
    elif a.mode == "apierror":
        result |= {"final": "API call failed after 5 retries: 429"}
    elif a.mode == "httperror":
        result |= {"final": "Antwort verworfen: HTTP 429 Too Many Requests"}
    elif a.mode == "silent":
        result |= {"final": ""}
    send(result)
'''


@pytest.fixture(scope="module")
def fake(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("fake_worker_modes") / "fake_worker_modes.py"
    path.write_text(FAKE_SRC, encoding="utf-8")
    return str(path)


def forget(session_id: str) -> None:
    """Drops the fake's cross-process markers for this session."""
    for p in Path(tempfile.gettempdir()).glob(f"cothinker-fake-*-{session_id}"):
        try:
            p.unlink()
        except OSError:
            pass


def client(fake: str, mode: str, session_id: str = "cothinker-test-20260908",
           extra: list | None = None) -> WorkerClient:
    forget(session_id)                    # no leftovers from an earlier run
    return WorkerClient(
        command=[sys.executable, fake, "--mode", mode, *(extra or [])],
        session_id=session_id, log=lambda *a: None)


def wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ---------------------------------------------------------------------------
# a good run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def good_run(fake):
    w = client(fake, "ok")
    captured = {"start_ok": w.start(), "session_id": w.session_id}
    captured["result"] = w.run("Neu gesprochen: Test.")
    captured["alive_after_run"] = w.alive()
    w.stop()
    captured["alive_after_stop"] = w.alive()
    return captured


def test_starts(good_run):
    assert good_run["start_ok"] is True


def test_reports_the_session_id(good_run):
    assert good_run["session_id"] == "cothinker-test-20260908"


def test_ok(good_run):
    assert good_run["result"]["ok"] is True


def test_carries_the_final_text(good_run):
    assert "mitgeschrieben" in good_run["result"]["final"]


def test_is_alive_afterwards(good_run):
    assert good_run["alive_after_run"] is True


def test_stopped(good_run):
    assert good_run["alive_after_stop"] is False


# ---------------------------------------------------------------------------
# failure signals come from the result, not from the text
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fail_run(fake):
    w = client(fake, "fail")
    w.start()
    r = w.run("x")
    w.stop()
    return r


def test_failed_arrow_not_ok(fail_run):
    assert fail_run["ok"] is False


def test_the_error_is_carried(fail_run):
    assert "provider refused" in fail_run["error"]


def test_partial_arrow_not_ok(fake):
    w = client(fake, "partial")
    w.start()
    r = w.run("x")
    w.stop()
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# the regression net stays: API call failed / HTTP 4
# ---------------------------------------------------------------------------
# Both markers are pinned, one test each: a failed API call that still
# exits 0 has happened here before, and half a pinned check is none.

def test_api_call_failed_in_the_final_text_counts_as_failure(fake):
    w = client(fake, "apierror")
    w.start()
    r = w.run("x")
    w.stop()
    assert r["ok"] is False


@pytest.fixture(scope="module")
def httperror_run(fake):
    w = client(fake, "httperror")
    w.start()
    r = w.run("x")
    w.stop()
    return r


def test_http_4_in_the_final_text_counts_as_failure(httperror_run):
    assert httperror_run["ok"] is False


def test_even_though_the_result_claims_success(httperror_run):
    assert "HTTP 4" in httperror_run["final"]


def test_silent_run_counts_as_success(fake):
    w = client(fake, "silent")
    w.start()
    r = w.run("x")
    w.stop()
    assert r["ok"] is True


# ---------------------------------------------------------------------------
# a dead worker is restarted with the SAME session id
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def die_run(fake):
    w = client(fake, "die")
    w.start()
    first = w.session_id
    captured = {"r1": w.run("x")}
    captured["alive_after_r1"] = w.alive()
    captured["r2"] = w.run("y")           # this call must restart it first
    captured["session_id_after"] = w.session_id
    captured["first_session_id"] = first
    captured["restarts"] = w.restarts
    w.stop()
    return captured


def test_the_run_that_killed_it_is_reported_as_failed(die_run):
    assert die_run["r1"]["ok"] is False


def test_the_worker_is_really_gone(die_run):
    assert die_run["alive_after_r1"] is False


def test_the_session_id_did_not_change(die_run):
    assert die_run["session_id_after"] == die_run["first_session_id"]


def test_a_live_worker_stood_again_after_the_restart(die_run):
    # This fake dies on every turn, so it is dead again by the time run()
    # returns -- the proof that a live worker stood again is the restart
    # the call had to perform before it could send the prompt at all.
    assert die_run["restarts"] == 1


def test_the_second_run_also_reports_failure(die_run):
    # This fake dies every turn.
    assert die_run["r2"]["ok"] is False


# ---------------------------------------------------------------------------
# and the turn after the restart really works again
# ---------------------------------------------------------------------------
# The headline behaviour: this fake dies on the FIRST turn only, so the
# second turn is answered by the restarted process -- also the only proof
# that the restarted worker gets a fresh queue (a shared one would let the
# late "gone" of the dead process swallow this answer).

@pytest.fixture(scope="module")
def die_once_run(fake):
    sid = "cothinker-test-restart-20260908"
    w = client(fake, "die-once", session_id=sid)
    w.start()
    captured = {"r1": w.run("x")}
    captured["r2"] = w.run("y")
    captured["session_id"] = w.session_id
    captured["sid"] = sid
    captured["restarts"] = w.restarts
    captured["alive"] = w.alive()
    w.stop()
    forget(sid)
    return captured


def test_the_turn_that_killed_the_worker_fails(die_once_run):
    assert die_once_run["r1"]["ok"] is False


def test_the_turn_after_the_restart_succeeds(die_once_run):
    assert die_once_run["r2"]["ok"] is True


def test_and_carries_its_result_text(die_once_run):
    assert "mitgeschrieben" in die_once_run["r2"]["final"]


def test_the_session_id_survived_the_restart(die_once_run):
    assert die_once_run["session_id"] == die_once_run["sid"]


def test_exactly_one_restart(die_once_run):
    assert die_once_run["restarts"] == 1


def test_and_the_worker_is_still_alive(die_once_run):
    assert die_once_run["alive"] is True


# ---------------------------------------------------------------------------
# a worker that closes stdout but lives on is restarted, not awaited
# ---------------------------------------------------------------------------
# Without this the client is deaf for good: the reader thread ends at EOF
# and never runs again, while the process keeps alive() happy.

@pytest.fixture(scope="module")
def deaf_run(fake):
    sid = "cothinker-test-deaf-20260908"
    w = client(fake, "deaf", session_id=sid)
    w.start()
    captured = {"closed_counts_as_death": wait_until(lambda: not w.alive())}
    t0 = time.monotonic()
    captured["result"] = w.run("x", timeout=3)
    captured["dauer"] = time.monotonic() - t0
    captured["restarts"] = w.restarts
    w.stop()
    forget(sid)
    return captured


def test_the_closed_stdout_counts_as_death(deaf_run):
    assert deaf_run["closed_counts_as_death"] is True


def test_the_next_turn_is_answered_by_a_restarted_worker(deaf_run):
    assert deaf_run["result"]["ok"] is True


def test_one_restart_was_counted(deaf_run):
    assert deaf_run["restarts"] == 1


def test_no_timeout_was_burnt_on_the_deaf_process(deaf_run):
    assert deaf_run["dauer"] < 3.0


# ---------------------------------------------------------------------------
# a client that was never started is not a worker that died
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def never_started_run(fake):
    sagt = []
    w = WorkerClient(command=[sys.executable, fake, "--mode", "ok"],
                      session_id="cothinker-test-first-20260908",
                      log=lambda *a: sagt.append(" ".join(str(x) for x in a)))
    captured = {"result": w.run("x"), "restarts": w.restarts, "log": sagt}
    w.stop()
    return captured


def test_the_first_turn_simply_works(never_started_run):
    assert never_started_run["result"]["ok"] is True


def test_no_restart_is_counted_for_the_first_start(never_started_run):
    assert never_started_run["restarts"] == 0


def test_and_nothing_claims_the_worker_was_gone(never_started_run):
    assert not any("gone" in z for z in never_started_run["log"])


# ---------------------------------------------------------------------------
# garbage on stdout does not hang the client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def garbage_run(fake):
    w = client(fake, "garbage")
    w.start()
    r = w.run("x", timeout=4)
    w.stop()
    return r


def test_times_out_cleanly_instead_of_hanging(garbage_run):
    assert garbage_run["ok"] is False


def test_garbage_says_so(garbage_run):
    assert "timeout" in (garbage_run["error"] or "").lower()


# ---------------------------------------------------------------------------
# a slow worker hits the timeout
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def slow_run(fake):
    w = client(fake, "slow")
    w.start()
    captured = {"result": w.run("x", timeout=2)}
    captured["alive_after_timeout"] = w.alive()
    # A small grace: this fake sleeps through its stdin EOF, so the
    # default five seconds would simply be five seconds of test runtime.
    t0 = time.monotonic()
    w.stop(grace=0.3)
    captured["alive_after_small_grace"] = w.alive()
    captured["dauer"] = time.monotonic() - t0
    return captured


def test_timeout_reported(slow_run):
    assert slow_run["result"]["ok"] is False
    assert "timeout" in (slow_run["result"]["error"] or "").lower()


def test_a_timed_out_worker_is_stopped_not_left_busy_for_the_next_turn(slow_run):
    assert slow_run["alive_after_timeout"] is False


def test_a_small_grace_ends_it_quickly_anyway(slow_run):
    assert slow_run["alive_after_small_grace"] is False
    assert slow_run["dauer"] < 2.0


# ---------------------------------------------------------------------------
# the worker forces UTF-8 on its own stdin and stdout
# ---------------------------------------------------------------------------
# The client sets the encoding on ITS side of the pipes; the child
# inherits the environment. Under a non-UTF-8 locale stdout came up as
# ASCII, and the first turn died on the umlauts in the prompt right after
# `ready` -- with stderr on DEVNULL there was no diagnostic at all. This
# repo has a documented incident where a missing UTF-8 locale cost a
# whole meeting's transcription.
#
# Run for real under `LC_ALL=C`, with both of Python's own UTF-8 rescues
# switched off. NO HERMES anywhere: three stub modules stand in for it on
# PYTHONPATH. They import nothing, they run no agent, and they answer the
# one question that matters -- did the German prompt arrive as text, or
# as the surrogate garbage an ASCII stdin makes of it?

UTF8_STUBS = {
    "hermes_cli/__init__.py": "",
    "hermes_cli/runtime_provider.py":
        "def resolve_runtime_provider(requested=None, target_model=None):\n"
        "    return {}\n",
    "hermes_state.py":
        "class SessionDB:\n"
        "    def get_messages_as_conversation(self, session_id):\n"
        "        return None\n",
    "run_agent.py":
        "ERWARTET = 'Gr\\u00fc\\u00dfe \\U0001f3a7 zw\\u00f6lf \\u20ac'\n"
        "\n"
        "class AIAgent:\n"
        "    def __init__(self, **kw):\n"
        "        pass\n"
        "\n"
        "    def run_conversation(self, prompt, conversation_history=None):\n"
        "        try:\n"
        "            prompt.encode('utf-8')\n"
        "        except UnicodeEncodeError:\n"
        "            zustand = 'surrogates'\n"
        "        else:\n"
        "            zustand = 'sauber' if ERWARTET in prompt else 'verstuemmelt'\n"
        "        return {'final_response': 'OK \\u2014 ' + zustand,\n"
        "                'failed': False, 'partial': False, 'error': None}\n",
}


def _write_stubs(base: Path, stubs: dict[str, str]) -> None:
    for name, source in stubs.items():
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


@pytest.fixture(scope="module")
def utf8_run(tmp_path_factory):
    stubdir = tmp_path_factory.mktemp("utf8-stubs")
    _write_stubs(stubdir, UTF8_STUBS)
    prompt = "Neu gesprochen: Gr\u00fc\u00dfe \U0001f3a7 zw\u00f6lf \u20ac"
    r = subprocess.run(
        [sys.executable, WORKER, "--session-id", "cothinker-test-utf8"],
        input=json.dumps({"type": "turn", "id": "t1", "prompt": prompt},
                          ensure_ascii=False).encode("utf-8") + b"\n",
        capture_output=True, timeout=60,
        env=dict(os.environ, LC_ALL="C", LANG="C", LC_CTYPE="C",
                  PYTHONCOERCECLOCALE="0", PYTHONUTF8="0", PYTHONPATH=str(stubdir)))
    lines = [json.loads(z) for z in r.stdout.decode("utf-8").splitlines() if z.strip()]
    results = [z for z in lines if z.get("type") == "result"]
    return {"stdout": r.stdout, "stderr": r.stderr, "results": results}


def test_the_worker_answers_a_turn_under_the_c_locale(utf8_run):
    assert utf8_run["results"]


def test_the_german_prompt_reaches_the_agent_as_text_not_as_surrogates(utf8_run):
    assert utf8_run["results"] and utf8_run["results"][0]["final"] == "OK \u2014 sauber"


def test_and_the_answer_comes_back_as_utf8(utf8_run):
    assert "\u2014" in utf8_run["stdout"].decode("utf-8")


def test_no_traceback_from_the_encoding(utf8_run):
    assert b"Traceback" not in utf8_run["stderr"]


def test_both_streams_are_forced_not_just_stdout():
    quelle = Path(WORKER).read_text(encoding="utf-8")
    assert "for stream in (sys.stdin, sys.stdout)" in quelle
    assert 'reconfigure(encoding="utf-8"' in quelle


# ---------------------------------------------------------------------------
# the history is re-read before EVERY turn, not only the first
# ---------------------------------------------------------------------------
# The bug this pins, measured against the real runtime: the worker passed
# the stored conversation into the FIRST turn and then set it to None,
# assuming `AIAgent` carries it forward itself. It does not. The store
# was never the problem -- it held user/assistant/user/assistant after
# two turns; nobody read it back. Invisible from outside, so it gets a
# test: the stub agent reports how many history messages reached it, and
# the stub store grows by one exchange per turn, exactly as the real one
# does. NO HERMES -- three stub modules on PYTHONPATH again.

HIST_STUBS = {
    "hermes_cli/__init__.py": "",
    "hermes_cli/runtime_provider.py":
        "def resolve_runtime_provider(requested=None, target_model=None):\n"
        "    return {}\n",
    "hermes_state.py":
        "GESPEICHERT = []\n"
        "\n"
        "class SessionDB:\n"
        "    def get_messages_as_conversation(self, session_id):\n"
        "        return list(GESPEICHERT)\n",
    "run_agent.py":
        "import hermes_state\n"
        "\n"
        "class AIAgent:\n"
        "    def __init__(self, **kw):\n"
        "        pass\n"
        "\n"
        "    def run_conversation(self, prompt, conversation_history=None):\n"
        "        gesehen = len(conversation_history or [])\n"
        "        hermes_state.GESPEICHERT.append({'role': 'user',\n"
        "                                         'content': prompt})\n"
        "        hermes_state.GESPEICHERT.append({'role': 'assistant',\n"
        "                                         'content': 'ok'})\n"
        "        return {'final_response': 'gesehen=%d' % gesehen,\n"
        "                'failed': False, 'partial': False, 'error': None}\n",
}


@pytest.fixture(scope="module")
def histdir(tmp_path_factory):
    d = tmp_path_factory.mktemp("hist-stubs")
    _write_stubs(d, HIST_STUBS)
    return d


@pytest.fixture(scope="module")
def history_run(histdir):
    eingabe = "".join(
        json.dumps({"type": "turn", "id": f"t{n}", "prompt": f"Block {n}."},
                   ensure_ascii=False) + "\n"
        for n in (1, 2, 3))
    r = subprocess.run(
        [sys.executable, WORKER, "--session-id", "cothinker-test-history"],
        input=eingabe.encode("utf-8"), capture_output=True, timeout=60,
        env=dict(os.environ, PYTHONPATH=str(histdir)))
    ergebnisse = [json.loads(z) for z in r.stdout.decode("utf-8").splitlines()
                  if z.strip() and json.loads(z).get("type") == "result"]
    return [e["final"] for e in ergebnisse]


def test_all_three_turns_are_answered(history_run):
    assert len(history_run) == 3


def test_the_first_turn_starts_from_an_empty_store(history_run):
    assert history_run[:1] == ["gesehen=0"]


def test_the_second_turn_sees_the_first_exchange(history_run):
    assert history_run[1:2] == ["gesehen=2"]


def test_and_the_third_sees_both_the_history_keeps_growing(history_run):
    assert history_run[2:3] == ["gesehen=4"]


def test_nothing_discards_the_history_after_a_turn():
    quelle = Path(WORKER).read_text(encoding="utf-8")
    assert "history = None" not in quelle


def test_the_store_is_read_per_turn_not_once_at_startup():
    quelle = Path(WORKER).read_text(encoding="utf-8")
    assert "conversation_history=load_history()" in quelle


# ---------------------------------------------------------------------------
# --no-history: a lane worker never replays the ever-growing per-session
# store
# ---------------------------------------------------------------------------
# gateway/lane_context.py's build_prompt() already puts the full bounded
# window + Stand summary INTO the prompt every turn, so replaying the
# SessionDB conversation on top of that (as the serial, non-lane worker
# above still does) would just re-grow without bound. Same stub store as
# the test above, fresh session id so the two runs never share state.

@pytest.fixture(scope="module")
def no_history_run(histdir):
    eingabe = "".join(
        json.dumps({"type": "turn", "id": f"t{n}", "prompt": f"Block {n}."},
                   ensure_ascii=False) + "\n"
        for n in (1, 2, 3))
    r = subprocess.run(
        [sys.executable, WORKER, "--session-id", "cothinker-test-no-history",
         "--no-history"],
        input=eingabe.encode("utf-8"), capture_output=True, timeout=60,
        env=dict(os.environ, PYTHONPATH=str(histdir)))
    ergebnisse = [json.loads(z) for z in r.stdout.decode("utf-8").splitlines()
                  if z.strip() and json.loads(z).get("type") == "result"]
    return [e["final"] for e in ergebnisse]


def test_all_three_turns_are_answered_under_no_history(no_history_run):
    assert len(no_history_run) == 3


def test_every_turn_sees_an_empty_history_even_though_the_store_kept_growing(no_history_run):
    assert no_history_run == ["gesehen=0", "gesehen=0", "gesehen=0"]


# ---------------------------------------------------------------------------
# the regression net's source stays wired to the one shared definition
# ---------------------------------------------------------------------------
# The tuple itself used to be pinned here; now it is an import, and THAT
# is what stays pinned -- the worker must keep reading FAILURE_MARKERS
# from shared.agent, never grow a second copy of it.

def test_the_worker_imports_failure_markers_from_shared_agent_and_applies_it():
    quelle = Path(WORKER).read_text(encoding="utf-8")
    assert "from shared.agent import FAILURE_MARKERS" in quelle
    assert "any(marker in final for marker in FAILURE_MARKERS)" in quelle


# ---------------------------------------------------------------------------
# no test may drag Hermes in
# ---------------------------------------------------------------------------
# The property is "no import", not "the word never occurs".

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(?:hermes\w*|run_agent)\b",
                       re.MULTILINE | re.IGNORECASE)


def test_the_fake_worker_imports_nothing_from_hermes(fake):
    assert IMPORT_RE.search(Path(fake).read_text(encoding="utf-8")) is None


def test_no_test_file_imports_hermes_at_all():
    here = Path(__file__).resolve().parent
    guilty = [p.name for p in sorted(here.glob("*.py"))
              if IMPORT_RE.search(p.read_text(encoding="utf-8"))]
    assert guilty == []


def test_importing_the_worker_pulls_in_no_agent_runtime_module():
    # Checked on the loaded modules, not on the source: moving the
    # imports up to module scope would pass every text check on a
    # machine where the runtime is importable.
    laden = re.compile(r"(^|\.)(hermes\w*|run_agent)(\.|$)", re.IGNORECASE)
    gezogen = sorted(m for m in list(sys.modules) if laden.search(m))
    assert gezogen == []


# ---------------------------------------------------------------------------
# WorkerClient.start() retries with a FRESH process on startup failure
# ---------------------------------------------------------------------------
# Evidence: the live start path's Hermes import failed intermittently
# across separate process launches -- a fresh process gets an
# independent chance, so start() must retry by re-spawning, not by
# looping inside one dead process.

def test_start_succeeds_once_a_fresh_process_finally_reports_ready(fake):
    c = client(fake, "ok", session_id="cothinker-test-startretry-1")
    c.command = [sys.executable, fake, "--mode", "ok", "--fail-starts", "2"]
    ok = c.start(attempts=4, delay_s=0.05, sleep=lambda s: None)
    try:
        assert ok and c.alive()
    finally:
        c.stop()


def test_start_gives_up_and_returns_false_once_every_attempt_failed(fake):
    c = client(fake, "ok", session_id="cothinker-test-startretry-2")
    c.command = [sys.executable, fake, "--mode", "ok", "--fail-starts", "99"]
    ok = c.start(attempts=3, delay_s=0.0, sleep=lambda s: None)
    try:
        assert ok is False and not c.alive()
    finally:
        c.stop()


# ---------------------------------------------------------------------------
# retry_import() -- transient hermes-agent pm import races
# ---------------------------------------------------------------------------
# Evidence: on the shared vServer, the exact same
# "from hermes_cli.runtime_provider import ..." import failed with
# ModuleNotFoundError in some process launches and succeeded in others
# seconds apart, with no code change in between -- a race in
# hermes-agent's own dependency-generation selection (pm), external repo,
# not fixed here. retry_import() is the in-process mitigation.

def make_flaky(fail_times: int):
    calls = {"n": 0}

    def attempt():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise ModuleNotFoundError(f"No module named 'ruamel' (call {calls['n']})")
    return attempt, calls


@pytest.fixture
def flaky_success_import():
    logged, slept = [], []
    attempt, calls = make_flaky(2)
    retry_import(attempt, log=logged.append, attempts=5, delay_s=0.01, sleep=slept.append)
    return {"calls": calls, "logged": logged, "slept": slept}


def test_retry_import_succeeds_once_the_flaky_import_stops_failing(flaky_success_import):
    assert flaky_success_import["calls"]["n"] == 3


def test_retry_import_logs_one_warning_per_failed_attempt(flaky_success_import):
    assert len(flaky_success_import["logged"]) == 2


def test_every_logged_warning_is_a_well_formed_log_line(flaky_success_import):
    assert all(m["type"] == "log" and m["level"] == "warn"
               for m in flaky_success_import["logged"])


def test_retry_import_sleeps_between_failed_attempts(flaky_success_import):
    assert flaky_success_import["slept"] == [0.01, 0.01]


@pytest.fixture
def flaky_exhausted_import():
    logged = []
    attempt, calls = make_flaky(99)           # never succeeds
    raised = None
    try:
        retry_import(attempt, log=logged.append, attempts=3, delay_s=0.0, sleep=lambda s: None)
    except ModuleNotFoundError as e:
        raised = e
    return {"raised": raised, "calls": calls, "logged": logged}


def test_retry_import_reraises_the_last_exception(flaky_exhausted_import):
    raised = flaky_exhausted_import["raised"]
    assert raised is not None and "call 3" in str(raised)


def test_retry_import_made_exactly_attempts_tries(flaky_exhausted_import):
    assert flaky_exhausted_import["calls"]["n"] == 3


def test_retry_import_logs_a_warning_for_every_failed_attempt_except_the_last(flaky_exhausted_import):
    assert len(flaky_exhausted_import["logged"]) == 2


def test_retry_import_on_an_immediate_success_never_sleeps_and_never_logs():
    logged = []
    attempt, calls = make_flaky(0)            # succeeds first try
    retry_import(attempt, log=logged.append, attempts=5, delay_s=0.01,
                 sleep=lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))
    assert calls["n"] == 1 and logged == []


# ---------------------------------------------------------------------------
# profile_disabled_toolsets
# ---------------------------------------------------------------------------

def test_profile_disabled_toolsets_reads_the_profiles_config(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "agent:\n  disabled_toolsets: [skills, terminal]\n")
    assert profile_disabled_toolsets(str(tmp_path)) == ["skills", "terminal"]


def test_profile_disabled_toolsets_without_config_yaml_is_empty(tmp_path):
    assert profile_disabled_toolsets(str(tmp_path)) == []


def test_profile_disabled_toolsets_none_is_empty():
    assert profile_disabled_toolsets(None) == []


def test_worker_passes_disabled_toolsets_into_aiagent():
    quelle = Path(WORKER).read_text(encoding="utf-8")
    assert '_extra["disabled_toolsets"] = _disabled' in quelle


# ---------------------------------------------------------------------------
# turn timeout frees the worker for the next turn -- no zombie request
# keeps it busy
# ---------------------------------------------------------------------------
# "slow-once" sleeps 30s (never responds inside the test's small timeout)
# on the FIRST process only; a restarted process answers immediately --
# exactly the shape of "the stuck call never arrives, but a fresh worker
# is free to take the very next turn". A small real timeout stands in for
# a real deployment's much larger one, proving the behaviour needs no
# real long wait.

@pytest.fixture(scope="module")
def turn_timeout_run(fake):
    sid = "cothinker-test-turntimeout-20261025"
    w = client(fake, "slow-once", session_id=sid)
    w.start()
    t0 = time.monotonic()
    captured = {"r1": w.run("x", timeout=0.3)}
    captured["dauer"] = time.monotonic() - t0
    captured["alive_after_timeout"] = w.alive()
    captured["r2"] = w.run("y", timeout=5)
    captured["restarts"] = w.restarts
    w.stop()
    forget(sid)
    return captured


def test_the_stuck_turn_times_out_quickly(turn_timeout_run):
    assert turn_timeout_run["dauer"] < 2.0


def test_the_timed_out_turn_is_reported_as_a_failure(turn_timeout_run):
    assert turn_timeout_run["r1"]["ok"] is False


def test_the_hung_worker_process_was_actively_stopped(turn_timeout_run):
    assert turn_timeout_run["alive_after_timeout"] is False


def test_the_worker_is_free_again_for_the_very_next_turn(turn_timeout_run):
    assert turn_timeout_run["r2"]["ok"] is True


def test_the_active_timeout_kill_is_not_double_counted_as_a_restart(turn_timeout_run):
    # Not counted in `restarts`: the active kill's own stop() already set
    # self.proc to None, so the next run() sees a client that looks like
    # it was never started (same "first start is not a restart" rule the
    # "never started" test above pins) -- `restarts` only counts a
    # DEAD-but-still-referenced process being replaced.
    assert turn_timeout_run["restarts"] == 0
