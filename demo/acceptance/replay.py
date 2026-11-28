"""Offline replay of a catalogue scenario through the real consumer.

Renders a scenario's spoken steps to STT ``final`` events, serves them over
a local timed SSE ``/events`` endpoint and runs ``gateway/consumer.py
--lanes`` against it with a recording worker on every lane (no agent is ever
launched). The result is an ``Observation`` for the mechanical oracle.

Not a maintained general-purpose tool: it serves this repository's own STT
SSE contract and nothing else.

Limits: clip steps (``clip:``, voice ``audio-dir:<x>``) have no text and
cannot be rendered; ``render_finals`` raises ValueError for them. Topics are
only observed when the consumer files topic headings, which needs a decider
model; offline that is normally empty. The call worker receives the full
lane prompt, so ``lane_calls`` hold prompts, not bare order text.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from demo.acceptance.oracle import Observation
from demo.acceptance.score import parse_line, raw_transcript_lines
from demo.acceptance.scenarios import topic_titles

_WORKER = Path(__file__).resolve().parent / "recording_worker.py"
_GRACE_S = 2.5          # after the expected calls arrived: catch extra ones


def render_finals(s, words_per_s: float = 2.5) -> list[dict]:
    """One ``final`` per ``say`` step, in scenario-local seconds."""
    finals: list[dict] = []
    cursor = 0.0
    for i, step in enumerate(s.timeline):
        if "pause" in step:
            cursor += float(step["pause"])
            continue
        if "clip" in step or "say" not in step:
            raise ValueError(
                f"scenario {s.id} step {i} is a clip step; it has no text to replay")
        text = step["say"]
        start = max(0.0, cursor - float(step.get("overlap_prev_s", 0.0)))
        end = start + max(len(text.split()), 1) / words_per_s
        finals.append({"type": "final", "text": text, "recognizer_id": "0",
                       "t_start": start, "t_end": end,
                       "speaker": step["speaker"]})
        cursor = max(cursor, end)
    return finals


def _server(finals: list[dict], speed: float):
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/events":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            t0 = time.monotonic()
            try:
                for ev in finals:
                    due = t0 + ev["t_end"] / speed
                    if stop.wait(max(0.0, due - time.monotonic())):
                        return
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                while not stop.wait(0.5):
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except OSError:
                pass

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, stop


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _calls(path: Path) -> list[str]:
    out = []
    for line in _read(path).splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("lane") == "call":
            out.append(str(rec.get("text", "")))
    return out


def _session_dir(project: Path) -> Path | None:
    dirs = sorted(p for p in (project / "sessions").glob("*") if p.is_dir())
    return dirs[-1] if dirs else None


def run(s, *, repo: Path, workdir: Path, speed: float = 1.0,
        timeout_s: float = 120) -> Observation:
    finals = render_finals(s)
    workdir = Path(workdir)
    n = 0
    while (workdir / f"replay{n}").exists():
        n += 1
    root = workdir / f"replay{n}"
    instance = root / "instance"
    project = root / "vault" / "entities" / "example" / "projekte" / "demo"
    instance.mkdir(parents=True)
    project.mkdir(parents=True)
    (instance / "instance.toml").write_text(
        '[instance]\nname = "example"\ntimezone = "UTC"\n'
        'languages = ["en"]\napp_format = "1080x1920"\n'
        'poster_format = "1080x1350"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    worker_log = root / "worker-turns.jsonl"
    env = {k: v for k, v in os.environ.items() if not k.startswith("COTHINKER_")}
    env.update(COTHINKER_INSTANCE_DIR=str(instance),
               COTHINKER_ENTITIES=str(root / "vault" / "entities"),
               COTHINKER_PROJECT_DIR=str(project),
               RECORDING_WORKER_LOG=str(worker_log),
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test",
               GIT_COMMITTER_EMAIL="test@example.invalid")
    worker = f"{shlex.quote(sys.executable)} {shlex.quote(str(_WORKER))}"
    expected = s.expect.get("lane_calls", {}).get("count")
    last_words = [f["text"].split()[-1].strip(".,!?") for f in finals if f["text"].split()]
    srv, stop = _server(finals, speed)
    log_path = root / "consumer.log"
    proc = None
    try:
        with open(log_path, "w") as logf:
            cmd = [sys.executable, str(Path(repo) / "gateway" / "consumer.py"),
                   "--lanes", "--stt", f"http://127.0.0.1:{srv.server_address[1]}",
                   "--project", "demo", "--title", "Replay",
                   "--pause", "0.5", "--check-every", "0",
                   "--worker-command", f"{worker} --lane main",
                   "--lane-call-worker-command", f"{worker} --lane call"]
            proc = subprocess.Popen(cmd, env=env, stdout=logf,
                                    stderr=subprocess.STDOUT, cwd=root)
            deadline = time.monotonic() + timeout_s
            done_at = None
            while time.monotonic() < deadline and proc.poll() is None:
                sd = _session_dir(project)
                text = _read(sd / "protokoll.md") if sd else ""
                if sd and all(w in text for w in last_words) and (
                        expected is None or len(_calls(worker_log)) >= expected):
                    done_at = done_at or time.monotonic()
                    if time.monotonic() - done_at >= _GRACE_S:
                        break
                time.sleep(0.3)
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)    # the consumer's clean shutdown
                try:
                    proc.wait(timeout=max(5.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        stop.set()
        srv.shutdown()
        srv.server_close()
    sd = _session_dir(project)
    protocol = _read(sd / "protokoll.md") if sd else ""
    lines = [p for p in (parse_line(ln) for ln in raw_transcript_lines(protocol)) if p]
    return Observation(lane_calls=_calls(worker_log), topics=topic_titles(protocol),
                       transcript_lines=lines, mode="transcript")
