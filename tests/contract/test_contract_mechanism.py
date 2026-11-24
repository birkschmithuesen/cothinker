"""The contract-test machinery itself (tests/contract/conftest.py): the
loopback-only guard, and an outside subject named by environment variable
running the same contract test as the built-in ones (ADR 0022 rule 3).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

OUTSIDE_AGENT = '''\
import contextlib

from shared.agent import Event, FakeAgentPort

SCRIPTS = {
    "tool": [Event(kind="tool_started", tool_name="t"), Event(kind="tool_finished", tool_name="t"),
             Event(kind="text_delta", text="ok"), Event(kind="done")],
    "failed": [Event(kind="failed", reason="run.failed")],
    "failure_marker": [Event(kind="text_delta", text="HTTP 403"), Event(kind="done")],
    "cancelled": [Event(kind="failed", reason="run.cancelled")],
}


@contextlib.contextmanager
def make(scenario):
    port = FakeAgentPort()
    if scenario in SCRIPTS:
        port.script("contract turn", SCRIPTS[scenario])
    yield port
'''

BROKEN_INSTANCE = """\
[instance]
name = "outside-broken"
timezone = "UTC"
languages = ["en"]
app_format = "example-app/1"
poster_format = "example-poster/1"

[secrets]
hub_secret = "literal-value"
"""


def _run_outside(test_file: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", "outside", test_file],
        cwd=REPO, env={**os.environ, **env}, capture_output=True, text=True, timeout=300)


def test_a_connection_beyond_loopback_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        with pytest.raises(RuntimeError, match="must not reach the network"):
            s.connect(("192.0.2.1", 9))


def test_a_loopback_connection_is_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass


def test_an_outside_agent_adapter_runs_the_same_contract(tmp_path):
    (tmp_path / "outside_agent.py").write_text(OUTSIDE_AGENT, encoding="utf-8")
    proc = _run_outside("tests/contract/agent/test_agent_contract.py", {
        "COTHINKER_CONTRACT_AGENT": "outside_agent:make",
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(REPO)]),
    })
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "7 passed" in proc.stdout, proc.stdout


def test_a_broken_outside_instance_is_red_for_the_same_contract(tmp_path):
    (tmp_path / "instance.toml").write_text(BROKEN_INSTANCE, encoding="utf-8")
    proc = _run_outside("tests/contract/instance/test_instance_contract.py",
                        {"COTHINKER_CONTRACT_INSTANCE": str(tmp_path)})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "3 failed, 2 passed" in proc.stdout, proc.stdout
