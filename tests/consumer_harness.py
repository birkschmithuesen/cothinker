"""Harness that runs the real gateway/consumer.py against fakes.

- `fake_stt(finals)`: local SSE server; `GET /events` streams one `final`
  event per transcript, then stays open (like a live STT server).
- `build_vault(tmp_path)`: a minimal instance + project folder.
- `run_consumer(...)`: starts the consumer as a subprocess with the fake
  worker, waits until every final is in the raw transcript, ends it with
  SIGTERM (its clean shutdown path) and collects the outputs.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONSUMER = REPO / "gateway" / "consumer.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ fake STT
class _SSE(BaseHTTPRequestHandler):
    finals: list[str] = []
    stop: threading.Event

    def do_GET(self):  # noqa: N802
        if self.path != "/events":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for i, text in enumerate(self.finals):
                ev = {"type": "final", "text": text, "recognizer_id": "0",
                      "t_start": float(i * 4), "t_end": float(i * 4 + 3)}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            while not self.stop.wait(0.5):      # keep the stream open
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args):
        pass


@contextmanager
def fake_stt(finals):
    """Yields the base URL of a fake STT server serving `finals`."""
    stop = threading.Event()
    handler = type("Handler", (_SSE,), {"finals": list(finals), "stop": stop})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        stop.set()
        srv.shutdown()
        srv.server_close()


# --------------------------------------------------------------------- vault
def build_vault(tmp_path: Path) -> dict:
    """Minimal instance dir and project folder (its own git repo)."""
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "instance.toml").write_text(
        '[instance]\nname = "example"\ntimezone = "UTC"\n'
        'languages = ["en"]\napp_format = "1080x1920"\n'
        'poster_format = "1080x1350"\n', encoding="utf-8")
    entities = tmp_path / "vault" / "entities"
    project = entities / "example" / "projekte" / "demo"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"instance": instance, "entities": entities, "project": project}


# ------------------------------------------------------------------ consumer
@dataclass
class ConsumerRun:
    returncode: int
    log: str
    project: Path
    session_dir: Path | None
    protocol_text: str
    stage_json: dict | None
    timed_out: bool = False
    worker_log: Path | None = None     # fake worker's turn log (jsonl)


def _session_dir(project: Path) -> Path | None:
    dirs = sorted(p for p in (project / "sessions").glob("*") if p.is_dir())
    return dirs[-1] if dirs else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def run_consumer(tmp_path: Path, *, finals, extra_args=(),
                 timeout_s: float = 60,
                 wait_for_worker_turns: int = 0,
                 seed=None, env_extra=None) -> ConsumerRun:
    """`seed(project)` runs before the consumer starts (files it should
    find); `env_extra` is added to the consumer's environment (the fake
    worker reads FAKE_WORKER_REPLY from it)."""
    finals = list(finals)
    n = 0
    while (tmp_path / f"run{n}").exists():      # repeated runs: own subdir
        n += 1
    tmp_path = tmp_path / f"run{n}"
    tmp_path.mkdir()
    vault = build_vault(tmp_path)
    if seed is not None:
        seed(vault["project"])
    worker_log = tmp_path / "worker-turns.jsonl"
    base = {k: v for k, v in os.environ.items()
            if not k.startswith("COTHINKER_")}
    env = dict(base,
               COTHINKER_INSTANCE_DIR=str(vault["instance"]),
               COTHINKER_ENTITIES=str(vault["entities"]),
               COTHINKER_PROJECT_DIR=str(vault["project"]),
               FAKE_WORKER_LOG=str(worker_log),
               FAKE_WORKER_JSON="1",
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test",
               GIT_COMMITTER_EMAIL="test@example.invalid",
               **(env_extra or {}))
    log_path = tmp_path / "consumer.log"
    with fake_stt(finals) as stt_url, open(log_path, "w") as logf:
        cmd = [sys.executable, str(CONSUMER), "--stt", stt_url,
               "--project", "demo", "--title", "Smoke Test",
               "--pause", "0.5", "--check-every", "0",
               "--worker-command",
               f"{sys.executable} {FAKE_WORKER}", *extra_args]
        proc = subprocess.Popen(cmd, env=env, stdout=logf,
                                stderr=subprocess.STDOUT, cwd=tmp_path)
        deadline = time.monotonic() + timeout_s
        timed_out = False
        try:
            # Ready once every final's last words are in the raw transcript.
            while time.monotonic() < deadline and proc.poll() is None:
                sd = _session_dir(vault["project"])
                text = _read(sd / "protokoll.md") if sd else ""
                turns = len([ln for ln in _read(worker_log).splitlines()
                             if ln.strip()])
                if (sd and all(f.split()[-1].strip(".,!?") in text
                               for f in finals if f.split())
                        and (sd / "buehne.json").exists()
                        and turns >= wait_for_worker_turns):
                    break
                time.sleep(0.3)
            else:
                timed_out = proc.poll() is None
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)    # the clean shutdown path
            try:
                proc.wait(timeout=max(5.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
    sd = _session_dir(vault["project"])
    stage = None
    if sd and (sd / "buehne.json").exists():
        try:
            stage = json.loads(_read(sd / "buehne.json"))
        except ValueError:
            stage = None
    return ConsumerRun(returncode=proc.returncode, log=_read(log_path),
                       project=vault["project"], session_dir=sd,
                       protocol_text=_read(sd / "protokoll.md") if sd else "",
                       stage_json=stage, timed_out=timed_out,
                       worker_log=worker_log)
