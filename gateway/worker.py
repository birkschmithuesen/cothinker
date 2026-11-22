#!/usr/bin/env python3
# CoThinker — the agent worker process
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""One long-lived Hermes agent per meeting, behind a JSON-line protocol.

Why a process and not an import: the consumer's first rule is that the
SSE reader must never block and never die. Pulling the agent runtime into
that process pulls its threads, memory and import errors into the
transcript, and lost raw material is unrecoverable. It also keeps the
test suite standard-library only -- the worker is exercised against
tests/fake_worker.py, never against a real agent.

Why not the interactive CLI: feeding it on stdin and reading its screen
output is documented as unreliable in the very file this replaces (frames
and control characters), and it has no failure signal at all.

Protocol, one JSON object per line:
    in    {"type":"turn","id":"t17","prompt":"..."}
    out   {"type":"ready","session_id":"..."}
    out   {"type":"result","id":"t17","ok":true,"failed":false,
           "partial":false,"final":"...","error":null}
    out   {"type":"log","level":"warn","text":"..."}

Profile: Hermes picks its profile from the environment variable
HERMES_HOME, and it resolves that variable while it is being imported.
Anyone running a non-default profile -- what `hermes -p max` used to
select -- MUST therefore pass `--hermes-home /path/to/profile`, which is
set into the environment before the first Hermes import. Without it the
worker writes into the default profile: sessions, credentials and history
all land beside the ones the meeting is supposed to continue.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)


def profile_disabled_toolsets(hermes_home: str | None) -> list[str]:
    """`agent.disabled_toolsets` from <hermes_home>/config.yaml, [] if absent.

    Read with plain YAML so it does not depend on Hermes' import state.
    """
    if not hermes_home:
        return []
    path = os.path.join(hermes_home, "config.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except (OSError, ValueError, ImportError):
        return []
    except Exception:                     # noqa: BLE001 -- yaml.YAMLError
        return []
    raw = (cfg.get("agent") or {}).get("disabled_toolsets") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()]

# Kept from AGENTS.md as a regression net, pinned by a test, never remove.
# The structured `failed` flag is the primary signal now, but this check
# catches a reply whose text betrays an upstream failure the signal
# missed. One shared definition, not a second copy (ADR 0018 migration
# rule) -- blocks may not import gateway/ (ADR 0008), so the constant
# lives in shared/agent.py and this is only an import of it.
from shared.agent import FAILURE_MARKERS  # noqa: E402

# Live check, 20.10.2026: measured directly against the
# live start path (`$HERMES_PY gateway/worker.py --hermes-home
# ~/.hermes/profiles/cothinker --model claude-sonnet-5`), repeatedly, a
# few minutes apart: the Hermes import sometimes raises
# ModuleNotFoundError("No module named 'ruamel'") and sometimes succeeds,
# with no code change in between -- hermes-agent's dependency manager
# (`pm`) commits a shared "generation" of site-packages at PROCESS BOOT
# (before this module's own code runs at all), and a concurrent
# install/update by some other hermes process on this shared vServer can
# leave an inconsistent one selected for a given boot. That is a bug in
# hermes-agent (external repo, not fixed here, see
# docs/messungen/2026-10-20-ab-live-config.md).
#
# Because the bad selection is decided at process boot, retrying the SAME
# import inside the SAME already-booted process never recovers (verified:
# 8/8 identical retries all failed when the boot was bad) -- only a FRESH
# process gets a new chance. IMPORT_RETRY_ATTEMPTS stays small (catches a
# genuinely transient hiccup, e.g. file-lock contention inside pm's own
# runtime_lock); WorkerClient.start()'s own START_RETRY_ATTEMPTS, a fresh
# subprocess per attempt, is what actually mitigates this bug.
IMPORT_RETRY_ATTEMPTS = 3
IMPORT_RETRY_DELAY_S = 1.5

# A fresh subprocess per attempt -- each one reboots the interpreter and
# gets an independent chance at a consistent `pm` generation selection.
START_RETRY_ATTEMPTS = 4
START_RETRY_DELAY_S = 2.0


def retry_import(attempt_import: Callable[[], None], *,
                  log: Callable[[dict], None],
                  attempts: int = IMPORT_RETRY_ATTEMPTS,
                  delay_s: float = IMPORT_RETRY_DELAY_S,
                  sleep: Callable[[float], None] = time.sleep) -> None:
    """Calls `attempt_import()`, retrying on any exception.

    Raises the LAST exception once every attempt has failed -- the caller
    then reports startup failure exactly as it did before this retry
    existed. A successful attempt returns normally and tries nothing
    further.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            attempt_import()
            return
        except Exception as e:                # noqa: BLE001
            last_exc = e
            if attempt < attempts:
                log({"type": "log", "level": "warn",
                     "text": f"Hermes import attempt {attempt}/{attempts} "
                              f"failed ({e!r}), retrying"})
                sleep(delay_s)
    assert last_exc is not None
    raise last_exc


class WorkerClient:
    """Starts the worker, sends turns, restarts it when it dies."""

    def __init__(self, command: list[str], session_id: str,
                 log: Callable[..., None] = print) -> None:
        self.command = list(command)
        self.session_id = session_id
        self.log = log
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self._lines: queue.Queue = queue.Queue()
        self._reader_done = threading.Event()
        self._counter = 0
        self._lock = threading.Lock()

    # ---------------------------------------------------------- lifecycle
    def start(self, *, attempts: int = START_RETRY_ATTEMPTS,
              delay_s: float = START_RETRY_DELAY_S,
              sleep: Callable[[float], None] = time.sleep) -> bool:
        """Spawns the worker, retrying with a FRESH subprocess on failure.

        A fresh process each attempt matters: the hermes-agent import
        failure this retries past (IMPORT_RETRY_ATTEMPTS's docstring) is
        decided once per process boot, so re-trying inside one dead
        process cannot help -- only a newly booted one gets another
        chance at a consistent dependency generation.
        """
        for attempt in range(1, attempts + 1):
            if self._start_once():
                return True
            if attempt < attempts:
                self.log(f"worker start attempt {attempt}/{attempts} "
                         f"failed, retrying with a fresh process")
                sleep(delay_s)
        return False

    def _start_once(self) -> bool:
        # A FRESH queue per process: a reader thread of an earlier process
        # keeps writing into its own queue (its final "gone" arrives late),
        # so nothing left over from the dead worker can be mistaken for an
        # answer of the new one. Same for the "reader finished" flag.
        lines: queue.Queue = queue.Queue()
        done = threading.Event()
        self._lines = lines
        self._reader_done = done
        try:
            self.proc = subprocess.Popen(
                [*self.command, "--session-id", self.session_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                encoding="utf-8", errors="replace")
        except OSError as e:
            self.log("worker did not start:", e)
            self.proc = None
            return False
        threading.Thread(target=self._reader, args=(self.proc, lines, done),
                         daemon=True, name="worker-reader").start()
        ready = self._await("ready", timeout=60)
        if ready is None:
            self.log("worker did not report ready")
            self.stop()
            return False
        return True

    def alive(self) -> bool:
        # A finished reader counts as death even while the process lives: a
        # worker that closed its stdout can never answer again, and nothing
        # restarts the reader thread. Without this, every later turn would
        # burn its full timeout in silence.
        return (self.proc is not None and self.proc.poll() is None
                and not self._reader_done.is_set())

    def stop(self, grace: float = 5.0) -> None:
        """Ends the worker. Never raises, never leaves a zombie behind."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()          # EOF on stdin is the exit signal
        except (OSError, ValueError, RuntimeError):
            pass
        try:
            for signal_it in (None, proc.terminate, proc.kill):
                try:
                    if signal_it is not None:
                        signal_it()
                    proc.wait(timeout=grace)
                    return
                except subprocess.TimeoutExpired:
                    continue
                except OSError:             # already gone
                    return
        finally:
            # Close the pipes here instead of leaving them to the garbage
            # collector: over a four-hour meeting with restarts that would
            # otherwise leak file descriptors, and a still-open stdout keeps
            # the reader thread of a dead worker parked forever.
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except (OSError, ValueError, RuntimeError):
                    pass

    # ------------------------------------------------------------ reading
    def _reader(self, proc: subprocess.Popen, lines: queue.Queue,
                done: threading.Event) -> None:
        try:
            for raw in proc.stdout:            # type: ignore[union-attr]
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue                   # foreign noise, never fatal
                if isinstance(msg, dict):
                    lines.put(msg)
        except (OSError, ValueError, RuntimeError):
            pass                               # incl. stop() closing the pipe
                                               # under the running read
        finally:
            # First the flag, then the reaping: from here on this client is
            # deaf to that process, whether it is dead already or merely
            # mute, and alive() has to say so immediately.
            done.set()
            # Reap before announcing the death: end of stdout and end of
            # process are microseconds apart, and without the wait() a
            # caller that checks alive() right after the failed run can
            # still see the not-yet-collected child as running.
            try:
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
            lines.put({"type": "gone"})

    def _await(self, want: str, timeout: float, turn_id: str = "") -> dict | None:
        lines = self._lines
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                msg = lines.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            kind = msg.get("type")
            if kind == "gone":
                return None
            if kind == "log":
                self.log("worker:", msg.get("text"))
                continue
            if kind != want:
                continue
            if turn_id and msg.get("id") != turn_id:
                continue                       # a late answer to an old turn
            return msg

    # -------------------------------------------------------------- turns
    def run(self, prompt: str, timeout: float = 900) -> dict:
        """One turn. Restarts a dead worker with the SAME session id first."""
        with self._lock:
            if not self.alive():
                # A client that was never started is not a worker that
                # died: no alarming log line, and no restart in the count
                # that the operating log reports.
                restarting = self.proc is not None
                if restarting:
                    self.log("worker is gone — restarting on the same session")
                self.stop()
                if not self.start():
                    return {"ok": False, "final": "",
                            "error": "worker could not be restarted"
                                     if restarting else
                                     "worker could not be started"}
                if restarting:
                    self.restarts += 1
            self._counter += 1
            turn_id = f"t{self._counter}"
            proc = self.proc
            if proc is None or proc.stdin is None:
                return {"ok": False, "final": "", "error": "no worker"}
            try:
                proc.stdin.write(json.dumps(
                    {"type": "turn", "id": turn_id, "prompt": prompt},
                    ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                return {"ok": False, "final": "", "error": f"write failed: {e}"}

            msg = self._await("result", timeout=timeout, turn_id=turn_id)
            if msg is None:
                # A worker still busy with the timed-out turn would answer
                # it during the NEXT turn; stop it so the next run() starts
                # a fresh one on the same session (live stall 2026-10-20).
                if self.alive():
                    self.log(f"turn timeout after {timeout:.0f}s — stopping the worker")
                    # Short grace: a turn stuck mid-computation never
                    # notices stdin closing; waiting 5 s is pure stall.
                    self.stop(grace=0.5)
                return {"ok": False, "final": "",
                        "error": f"timeout after {timeout:.0f}s or worker gone"}

            final = str(msg.get("final") or "")
            ok = bool(msg.get("ok"))
            if msg.get("failed") or msg.get("partial"):
                ok = False
            if any(marker in final for marker in FAILURE_MARKERS):
                ok = False
            # A run with NO output is no longer a failure: the structured
            # result is the success signal now, and "a run without a change
            # is a good run".
            return {"ok": ok, "final": final, "usage": msg.get("usage") or {},
                    "error": msg.get("error") or ("" if ok else "run failed")}


def _main() -> int:                       # pragma: no cover - needs Hermes
    """The worker itself. The ONLY place that imports Hermes."""
    import argparse
    import os
    import sys

    # UTF-8 ON BOTH STREAMS, BEFORE ANYTHING IS READ OR WRITTEN. The client
    # sets the encoding on ITS side of the pipes; this process inherits the
    # environment, and under a non-UTF-8 locale (`LC_ALL=C`, a systemd unit
    # without one, a cron shell) stdin and stdout come up as ASCII. The
    # prompt carries umlauts and emoji, so the first turn would die on
    # `UnicodeEncodeError` right after `ready` -- with stderr on DEVNULL,
    # leaving no diagnostic at all. A missing UTF-8 locale has already cost
    # this project a whole meeting's transcription once.
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass                          # not a text stream we can retune

    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--provider", default="custom:anthropic_plan")
    ap.add_argument("--hermes-home", default="",
                    help="profile directory; becomes HERMES_HOME before the "
                         "first Hermes import (what `hermes -p NAME` did)")
    ap.add_argument("--no-history", action="store_true",
                    help="never read the per-session SessionDB conversation "
                         "back: a lane worker's prompt "
                         "(gateway/lane_context.py's build_prompt()) already "
                         "carries the full bounded window + Stand summary "
                         "every turn, so replaying the ever-growing stored "
                         "conversation on top of it would defeat the whole "
                         "point of the window. The serial (non-lane) worker "
                         "does not pass this and keeps load_history() as is.")
    a = ap.parse_args()

    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def startup_failure(text: str) -> int:
        # A clean, structured failure signal instead of a traceback: the
        # client's start() reads no "ready" within its timeout and reports
        # failure, and this "log" line is what tells the consumer why.
        # Everything up to "ready" runs through here, because stderr goes
        # to DEVNULL: what is not sent as a line is lost for good.
        send({"type": "log", "level": "error", "text": text})
        return 1

    # Before the first Hermes import: the profile is resolved at import
    # time, so setting it afterwards would silently have no effect.
    if a.hermes_home:
        os.environ["HERMES_HOME"] = a.hermes_home
    # Named out loud, so the operating log of every meeting shows which
    # profile the worker attached to instead of leaving it to be guessed.
    home = os.environ.get("HERMES_HOME")
    send({"type": "log", "level": "info",
          "text": f"HERMES_HOME={home}" if home else
                  "HERMES_HOME is not set — the inherited default profile "
                  "applies (pass --hermes-home for any other profile)"})

    hermes_modules: dict = {}

    def _import_hermes() -> None:
        # ORDER MEASURED, NOT INCIDENTAL (live-path re-check
        # 20.10.2026 08:5x): on the live vServer, with several other
        # hermes-agent processes running concurrently, importing
        # `hermes_cli.runtime_provider` BEFORE `run_agent` failed
        # `ModuleNotFoundError("No module named 'ruamel'")` 0/30+ times in a
        # row for several minutes straight, while each import ALONE (or
        # `run_agent` first, then the others) succeeded 20/20 every time
        # tried, in the same minute, under the same load. `ruamel.yaml`
        # itself imports fine the whole time (`import ruamel.yaml` alone
        # never failed) -- something in hermes_cli.runtime_provider's own
        # import chain leaves `utils`/`hermes_yaml` in a state that breaks
        # run_agent's later re-import of the same two modules, but only in
        # that order. `run_agent` first sidesteps it; no change to
        # hermes-agent itself (external repo) was needed or made.
        from run_agent import AIAgent
        from hermes_state import SessionDB
        from hermes_cli.runtime_provider import resolve_runtime_provider
        hermes_modules["resolve_runtime_provider"] = resolve_runtime_provider
        hermes_modules["SessionDB"] = SessionDB
        hermes_modules["AIAgent"] = AIAgent

    try:
        retry_import(_import_hermes, log=send)
    except Exception as e:                # noqa: BLE001
        return startup_failure(f"worker could not import Hermes: {e!r}")
    resolve_runtime_provider = hermes_modules["resolve_runtime_provider"]
    SessionDB = hermes_modules["SessionDB"]
    AIAgent = hermes_modules["AIAgent"]

    try:
        # Resolved the same way Hermes' own one-shot entry point does
        # (hermes_cli/oneshot.py): without an explicit provider the call
        # can land on the wrong endpoint despite a configured default and
        # fail there for lack of credit -- 25 runs were lost that way on
        # 2026-09-07. An empty --provider means "use the profile default",
        # so it is passed through as None rather than as an empty string.
        runtime = resolve_runtime_provider(requested=a.provider or None,
                                           target_model=a.model or None)
    except Exception as e:                # noqa: BLE001
        return startup_failure(
            f"worker could not resolve the runtime provider: {e!r}")

    try:
        # SQLite in WAL mode: a second process next to the one Hermes may
        # be running itself is expected usage (many readers, one writer).
        db = SessionDB()
    except Exception as e:                # noqa: BLE001
        return startup_failure(
            f"worker could not open the session store: {e!r}")

    reported: set[str] = set()

    def load_history() -> list | None:
        """The conversation so far, re-read from the store before EVERY turn.

        Not just after a resume. `AIAgent` does NOT carry the conversation
        forward across calls by itself: measured on 2026-09-08 against the
        real runtime, a second turn asked what it had just answered replied
        „Gar keins — das hier ist die erste Nachricht der Session", while
        the same agent with the history passed in answered correctly. The
        store was never the problem -- it held user/assistant/user/assistant
        after two turns; nobody read it back.

        That failure is invisible from the outside: the protocol still
        grows and the stage still fills, but the agent sees only the
        current block, so everything the methodology needs across turns
        stops working -- value conflicts against an earlier figure,
        breached decisions, resolving an open notice, the 120 s follow-up
        instruction. F4, F7 and F12 all over again.

        Reading costs one local SQLite query per turn against an agent run
        of 40-180 s. Re-attaching after a worker death (6.4) is no longer a
        special path; it is what happens on every turn anyway.

        Skipped entirely under `--no-history` and returns None without
        touching the store at all -- see that flag's help text.
        """
        if a.no_history:
            return None
        try:
            return db.get_messages_as_conversation(a.session_id) or None
        except Exception as e:            # noqa: BLE001
            # Not fatal -- on the first start there is simply nothing to
            # resume -- but it is said out loud: a run that silently starts
            # from zero looks exactly like a working one. Reported once per
            # distinct error, not once per turn: this runs every time now,
            # and a broken store would otherwise fill the log for hours.
            text = f"no history for {a.session_id}: {e!r}"
            if text not in reported:
                reported.add(text)
                send({"type": "log", "level": "warn", "text": text})
            return None

    try:
        # COTHINKER_MAX_ITERATIONS caps tool-calling rounds per turn (live
        # 2026-10-20: one ORDER ran 11 tool rounds / 614 s and stalled the
        # whole meeting). Unset = Hermes default (unlimited), unchanged.
        _cap = os.environ.get("COTHINKER_MAX_ITERATIONS", "").strip()
        _extra: dict = {"max_iterations": int(_cap)} if _cap.isdigit() and int(_cap) > 0 else {}
        # The profile's agent.disabled_toolsets is applied by Hermes' CLI
        # layer, not by AIAgent itself -- so a bare AIAgent(...) offered every
        # toolset (dress rehearsal 2026-10-20: the call lane loaded 40k chars
        # of skills and ran terminal, 85 s for one call). Pass it explicitly.
        _disabled = profile_disabled_toolsets(os.environ.get("HERMES_HOME"))
        if _disabled:
            _extra["disabled_toolsets"] = _disabled
            send({"type": "log", "level": "info",
                  "text": f"disabled toolsets: {', '.join(_disabled)}"})
        agent = AIAgent(api_key=runtime.get("api_key"),
                        base_url=runtime.get("base_url"),
                        provider=runtime.get("provider"),
                        requested_provider=runtime.get("requested_provider"),
                        api_mode=runtime.get("api_mode"),
                        credential_pool=runtime.get("credential_pool"),
                        model=a.model, session_id=a.session_id, session_db=db,
                        platform="cli", quiet_mode=True, **_extra)
    except Exception as e:                # noqa: BLE001
        return startup_failure(f"worker could not build the agent: {e!r}")

    send({"type": "ready", "session_id": a.session_id})

    # `agent.run_conversation()`'s result carries `input_tokens`/`output_tokens` as
    # `agent.session_input_tokens`/`session_output_tokens` (agent/turn_finalizer.py) --
    # cumulative over this agent's whole process lifetime, not per turn, because this
    # one `agent` object (unlike the API server's `_create_agent()`, which builds a
    # fresh one per request) is built once above and answers every turn. Tracking the
    # previous cumulative value and reporting the delta turns it back into a per-turn
    # count, the same thing path A's fresh-agent-per-request usage already is.
    prev_usage = {"input_tokens": 0, "output_tokens": 0}

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
        try:
            result = agent.run_conversation(str(msg.get("prompt") or ""),
                                            conversation_history=load_history())
            cur_in = int(result.get("input_tokens") or 0)
            cur_out = int(result.get("output_tokens") or 0)
            usage = {"input_tokens": max(0, cur_in - prev_usage["input_tokens"]),
                      "output_tokens": max(0, cur_out - prev_usage["output_tokens"])}
            prev_usage = {"input_tokens": cur_in, "output_tokens": cur_out}
            send({"type": "result", "id": msg.get("id"),
                  "ok": not (result.get("failed") or result.get("partial")),
                  "failed": bool(result.get("failed")),
                  "partial": bool(result.get("partial")),
                  "final": result.get("final_response") or "",
                  "usage": usage,
                  "error": result.get("error")})
        except Exception as e:            # noqa: BLE001 - never die on one turn
            send({"type": "result", "id": msg.get("id"), "ok": False,
                  "failed": True, "partial": False, "final": "",
                  "error": repr(e)})
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
