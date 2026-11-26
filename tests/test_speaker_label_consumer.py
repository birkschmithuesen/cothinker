# CoThinker — end-to-end test of the consumer's --speaker-labels wiring
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_speaker_label_consumer.py

Self-contained: the real gateway/consumer.py runs as a subprocess against
  - a fake SSE server (stdlib http.server, ephemeral port) that streams
    finals with a diarization label (`recognizer_id`),
  - a fake model endpoint (same, ephemeral port) standing in for the EU text
    client's API, reached through COTHINKER_INFOMANIAK_BASE,
  - the fake agent worker command tests/fake_worker.py.
Only invented placeholder names and speech; nothing leaves the machine.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# The module-under-test is exercised through the consumer's own wiring; this
# is the ONE place the speaker-label module path appears in this file.
CONSUMER = REPO / "gateway" / "consumer.py"
FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"

PEOPLE = "Ada Vance, Ben Ortiz, Cora"


# -------------------------------------------------------------- fake servers
class _SSE(BaseHTTPRequestHandler):
    events: list = []
    stop: threading.Event

    def do_GET(self):  # noqa: N802
        if self.path != "/events":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i, (label, text) in enumerate(self.events):
                ev = {"type": "final", "text": text, "recognizer_id": label,
                      "t_start": float(i * 4), "t_end": float(i * 4 + 3)}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            while not self.stop.wait(0.5):
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except OSError:
            pass

    def log_message(self, *args):
        pass


class _Model(BaseHTTPRequestHandler):
    """Fake EU text-model endpoint: records requests, answers `verdict`."""
    verdict: dict | None = None
    status: int = 200
    requests: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.requests.append(body)
        if self.status != 200:
            self.send_response(self.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        out = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.verdict)}}], "usage": {}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ----------------------------------------------------------------- the run
class Run:
    def __init__(self, returncode, log, protocol, stage, model_requests,
                 timed_out):
        self.returncode, self.log, self.protocol = returncode, log, protocol
        self.stage, self.model_requests = stage, model_requests
        self.timed_out = timed_out

    @property
    def lines(self):
        """The raw-transcript lines only (the header lists participants)."""
        return [ln for ln in self.protocol.splitlines()
                if ln.startswith("- **")]

    def speaker(self, fragment):
        """The speaker shown on the raw line containing `fragment`."""
        m = re.search(r"\*([^*]+):\*", self.line_with(fragment))
        assert m, self.line_with(fragment)
        return m.group(1)

    def line_with(self, fragment):
        hits = [ln for ln in self.lines if fragment in ln]
        assert hits, f"no protocol line with {fragment!r} in:\n{self.protocol}"
        return hits[0]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def run_consumer(tmp_path: Path, events, *, args=(), api_key=None,
                 verdict=None, model_status=200, wait_for=None,
                 timeout_s=40.0) -> Run:
    """Run the real consumer against the fakes until every final is in the
    raw transcript (and, if given, `wait_for(run_dir_text, log)` holds)."""
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

    stop = threading.Event()
    sse_srv, stt_url = _serve(type("H", (_SSE,), {
        "events": list(events), "stop": stop}))
    model_requests: list = []
    model_srv, model_url = _serve(type("M", (_Model,), {
        "verdict": verdict, "status": model_status,
        "requests": model_requests}))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("COTHINKER_", "INFOMANIAK_", "SPEAKER_"))}
    env.update(COTHINKER_INSTANCE_DIR=str(instance),
               COTHINKER_ENTITIES=str(entities),
               COTHINKER_PROJECT_DIR=str(project),
               COTHINKER_INFOMANIAK_BASE=model_url,
               FAKE_WORKER_LOG=str(tmp_path / "worker.jsonl"),
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.invalid")
    if api_key:
        env["INFOMANIAK_API_KEY"] = api_key
    log_path = tmp_path / "consumer.log"
    cmd = [sys.executable, str(CONSUMER), "--stt", stt_url,
           "--project", "demo", "--title", "Label Test", "--pause", "0.5",
           "--check-every", "0", "--participants", PEOPLE,
           "--worker-command", f"{sys.executable} {FAKE_WORKER}", *args]
    timed_out = False
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.Popen(cmd, env=env, stdout=logf,
                                    stderr=subprocess.STDOUT, cwd=tmp_path)
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline and proc.poll() is None:
                sds = sorted(p for p in (project / "sessions").glob("*")
                             if p.is_dir())
                proto = _read(sds[-1] / "protokoll.md") if sds else ""
                tlines = [ln for ln in proto.splitlines()
                          if ln.startswith("- **")]
                done = all(any(t.rstrip(".") in ln for ln in tlines)
                           for _l, t in events if t.split())
                if done and (wait_for is None
                             or wait_for(_read(log_path), model_requests)):
                    break
                time.sleep(0.2)
            else:
                timed_out = proc.poll() is None
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
    finally:
        stop.set()
        for srv in (sse_srv, model_srv):
            srv.shutdown()
            srv.server_close()
    sds = sorted(p for p in (project / "sessions").glob("*") if p.is_dir())
    stage = None
    if sds and (sds[-1] / "buehne.json").exists():
        try:
            stage = json.loads(_read(sds[-1] / "buehne.json"))
        except ValueError:
            pass
    return Run(proc.returncode, _read(log_path),
               _read(sds[-1] / "protokoll.md") if sds else "", stage,
               model_requests, timed_out)


INTRO = [("S1", "Hallo zusammen, ich bin Ada."),
         ("S2", "Hallo, mein Name ist Ben."),
         ("S1", "Dann fangen wir an."),
         ("S3", "Ich habe eine Frage zum Ablauf."),
         ("S2", "Ben hier noch einmal kurz.")]


@pytest.fixture(scope="module")
def rule_run(tmp_path_factory):
    return run_consumer(tmp_path_factory.mktemp("rule"), INTRO,
                        args=("--speaker-labels", "--no-agent"))


@pytest.fixture(scope="module")
def off_run(tmp_path_factory):
    return run_consumer(tmp_path_factory.mktemp("off"), INTRO,
                        args=("--no-agent",))


# ------------------------------------------------------------------- tests
def test_run_exits_cleanly(rule_run):
    assert not rule_run.timed_out
    assert rule_run.returncode == 0


def test_log_announces_rule_stage_only_without_key(rule_run):
    assert "speaker labels on: rule stage only" in rule_run.log


def test_intro_line_is_labelled_with_canonical_participant(rule_run):
    assert rule_run.speaker("ich bin Ada") == "Ada Vance"


def test_second_speaker_named_from_first_person_intro(rule_run):
    assert rule_run.speaker("mein Name ist Ben") == "Ben Ortiz"


def test_name_sticks_to_the_label_for_later_lines(rule_run):
    assert rule_run.speaker("Dann fangen wir an") == "Ada Vance"


def test_third_mention_style_line_keeps_label_name(rule_run):
    assert rule_run.speaker("Ben hier noch einmal") == "Ben Ortiz"


def test_unintroduced_label_is_not_named(rule_run):
    assert rule_run.speaker("Frage zum Ablauf") == "S3"


def test_every_final_reaches_the_protocol(rule_run):
    for _label, text in INTRO:
        assert text.split()[-1].strip(".") in rule_run.protocol


def test_without_flag_nobody_is_named(off_run):
    assert not off_run.timed_out
    assert [off_run.speaker(t) for _l, t in INTRO] == [l for l, _t in INTRO]
    assert "speaker labels on" not in off_run.log


def test_name_outside_participant_list_is_rejected(tmp_path):
    r = run_consumer(tmp_path, [("S1", "Hallo, ich bin Dan."),
                                ("S1", "Das war meine Vorstellung.")],
                     args=("--speaker-labels", "--no-agent"))
    assert r.speaker("ich bin Dan") == "S1"
    assert r.speaker("meine Vorstellung") == "S1"


def test_llm_stage_announced_and_model_called_for_handover(tmp_path):
    events = [("S1", "Hallo zusammen, ich bin Ada."),
              ("S2", "Danke, Ben, das war hilfreich.")]
    verdict = {"label": "S2", "name": "Ben Ortiz",
               "evidence_quote_index": 1, "confidence": "medium"}
    r = run_consumer(
        tmp_path, events, args=("--speaker-labels", "--no-agent"),
        api_key="test-key", verdict=verdict,
        wait_for=lambda log, reqs: "speaker labels: suggestion" in log)
    assert "rule stage + async LLM candidate stage" in r.log
    assert r.model_requests, "the fake model endpoint was never asked"
    user = json.loads(r.model_requests[0]["messages"][1]["content"])
    assert user["teilnehmende"] == ["Ada Vance", "Ben Ortiz", "Cora"]
    assert any(e["label"] == "S2" for e in user["aeusserungen"])
    # a medium-confidence verdict is a suggestion: surfaced, never applied
    assert "speaker labels: suggestion for S2 -> Ben Ortiz (medium)" in r.log
    assert r.speaker("das war hilfreich") == "S2"
    assert "Vorschlag" in json.dumps(r.stage, ensure_ascii=False)


def test_llm_endpoint_failure_does_not_break_the_run(tmp_path):
    events = [("S1", "Hallo zusammen, ich bin Ada."),
              ("S2", "Danke, Ben, das war hilfreich."),
              ("S2", "Und noch ein Satz zum Schluss.")]
    r = run_consumer(
        tmp_path, events, args=("--speaker-labels", "--no-agent"),
        api_key="test-key", model_status=400,
        wait_for=lambda log, reqs: bool(reqs))
    assert not r.timed_out
    assert r.returncode == 0
    assert r.speaker("ich bin Ada") == "Ada Vance"
    assert r.speaker("zum Schluss") == "S2"
