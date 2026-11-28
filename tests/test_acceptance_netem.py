"""Optional rootless network impairment for synthetic participants."""
import subprocess
import sys

import pytest

from demo.acceptance import netem


def test_parse_spec_happy_path():
    assert netem.parse_spec("loss=2%,delay=80ms") == {"loss": "2%", "delay": "80ms"}
    assert netem.parse_spec("delay=10.5ms") == {"delay": "10.5ms"}


@pytest.mark.parametrize("bad", [
    "jitter=5ms", "loss=2%,rate=1mbit", "loss=2", "delay=80", "loss=%",
    "loss=2%;id", "delay=80ms$(id)", "", "loss", "loss=2%,loss=3%",
])
def test_parse_spec_rejects(bad):
    with pytest.raises(ValueError):
        netem.parse_spec(bad)


def test_available_reports_missing_tools():
    ok, why = netem.available(which=lambda _n: None, probe=lambda: (True, ""))
    assert ok is False
    assert "slirp4netns" in why or "unshare" in why


def test_available_missing_one_tool():
    have = {"unshare": "/x/unshare", "tc": "/x/tc"}
    ok, why = netem.available(which=have.get, probe=lambda: (True, ""))
    assert not ok and "slirp4netns" in why


def test_available_probe_failure_is_reported():
    ok, why = netem.available(which=lambda n: "/x/" + n,
                              probe=lambda: (False, "user namespaces disabled"))
    assert not ok and "user namespaces disabled" in why


def test_available_ok():
    assert netem.available(which=lambda n: "/x/" + n,
                           probe=lambda: (True, "")) == (True, "")


def test_wrap_shape_and_no_sudo():
    cmd = ["python3", "p.py", "--name", "Alex"]
    out = netem.wrap(cmd, {"loss": "2%", "delay": "80ms"})
    assert out[:3] == [sys.executable, "-m", "demo.acceptance.netem"]
    assert out[3] == "--spec" and "loss=2%" in out[4] and "delay=80ms" in out[4]
    assert out[5] == "--"
    assert out[-len(cmd):] == cmd
    assert "sudo" not in " ".join(out)


def test_inner_command_never_uses_sudo_and_passes_values_as_args():
    argv = netem.inner_argv({"loss": "2%", "delay": "80ms"}, ["true"])
    assert "sudo" not in " ".join(argv)
    assert argv[:6] == ["unshare", "--user", "--map-root-user", "--net", "--fork", "--"]
    script = argv[argv.index("-c") + 1]
    assert "2%" not in script and "80ms" not in script  # values are positional
    assert argv[-1] == "true"


def test_probe_timeout_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("unshare", 1)
    monkeypatch.setattr(netem.subprocess, "run", boom)
    ok, why = netem._probe()
    assert not ok and "timed out" in why


# ---- wiring into the scenario runner and the participant starter ----------

def test_netem_setup_skipped_when_unavailable():
    import argparse
    from demo.acceptance import scenarios
    args = argparse.Namespace(netem="loss=2%,delay=80ms")
    row = scenarios.netem_setup(args, avail=lambda: (False, "missing tool(s): slirp4netns"))
    assert (row.id, row.status) == ("audio.netem", "SKIPPED")
    assert "slirp4netns" in row.evidence
    assert args.netem_wrapper is None


def test_netem_setup_pass_wraps_commands():
    import argparse
    from demo.acceptance import scenarios
    args = argparse.Namespace(netem="loss=2%,delay=80ms")
    row = scenarios.netem_setup(args, avail=lambda: (True, ""))
    assert (row.id, row.status, row.evidence) == ("audio.netem", "PASS", "loss=2%,delay=80ms")
    assert args.netem_wrapper(["x", "y"])[-2:] == ["x", "y"]


def test_netem_setup_absent_flag_adds_no_row():
    import argparse
    from demo.acceptance import scenarios
    args = argparse.Namespace(netem=None)
    assert scenarios.netem_setup(args) is None
    assert args.netem_wrapper is None


def test_parse_args_rejects_bad_netem(capsys):
    from demo.acceptance import scenarios
    with pytest.raises(SystemExit):
        scenarios.parse_args(["--netem", "jitter=3ms"])
    assert "netem" in capsys.readouterr().err


def test_start_participant_applies_wrapper(monkeypatch, tmp_path):
    from demo.acceptance import e2e
    seen = {}
    monkeypatch.setattr(e2e.subprocess, "Popen", lambda cmd, **k: seen.setdefault("cmd", cmd))
    e2e.start_participant("Alex", tmp_path / "a.wav", tmp_path / "u", tmp_path / "s",
                          tmp_path / "p", 9301, "chromium", 5.0,
                          wrapper=lambda c: ["WRAP", *c])
    assert seen["cmd"][0] == "WRAP"
    assert "--name" in seen["cmd"]


# ---- behavioural tests of run()/main() with fakes --------------------------

import signal as _signal


class FakeProc:
    def __init__(self, pid, polls=0, rc=0, subprocess_wait_timeouts=0):
        self.pid, self.rc, self._polls = pid, rc, polls
        self.returncode = None
        self.terminated = self.killed = False
        self.waits = []
        self._timeouts = subprocess_wait_timeouts

    def poll(self):
        if self._polls <= 0 and not self.killed:
            self.returncode = self.rc
        elif self._polls > 0:
            self._polls -= 1
        if self.killed:
            self.returncode = self.rc
        return self.returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self._timeouts > 0 and not self.killed:
            self._timeouts -= 1
            raise subprocess.TimeoutExpired("x", timeout)
        if self.returncode is None:
            self.returncode = self.rc
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _harness(child, slirp, ns=lambda pid: "new" if pid != 1 else "host", **kw):
    procs = [child, slirp]
    started = []

    def popen(argv, **k):
        started.append((argv, k))
        return procs[len(started) - 1]

    groups = []
    kw.setdefault("handoff_s", 1.0)
    rc = netem.run({"loss": "2%"}, ["x"], popen=popen, ns_of=lambda pid: "host" if pid == __import__("os").getpid() else "new",
                   sleep=lambda s: None, killpg=lambda pid, sig: groups.append((pid, sig)), **kw)
    return rc, started, groups


def test_run_propagates_exit_code_and_cleans_up_slirp():
    child, slirp = FakeProc(100, polls=2, rc=7), FakeProc(200, polls=10**6)
    rc, started, groups = _harness(child, slirp)
    assert rc == 7
    assert started[0][1].get("start_new_session") is True
    assert started[1][0][:4] == ["slirp4netns", "--configure", "--mtu=65520", "100"]
    assert slirp.terminated
    assert (100, _signal.SIGTERM) in groups


def test_run_cleans_up_slirp_when_child_fails():
    child, slirp = FakeProc(100, polls=0, rc=1), FakeProc(200, polls=10**6)
    rc, _, _ = _harness(child, slirp)
    assert rc == 1 and slirp.terminated


def test_run_signal_death_becomes_128_plus_signum():
    child, slirp = FakeProc(100, polls=0, rc=-9), FakeProc(200, polls=10**6)
    rc, _, _ = _harness(child, slirp)
    assert rc == 137


def test_run_slirp_dying_early_stops_group(capsys):
    child, slirp = FakeProc(100, polls=10**6), FakeProc(200, polls=0, rc=3)
    rc, _, groups = _harness(child, slirp)
    assert rc == netem.EXIT_SLIRP_FAILED
    assert "slirp4netns exited early" in capsys.readouterr().err
    assert (100, _signal.SIGTERM) in groups


def test_run_handoff_timeout_is_bounded(capsys):
    child = FakeProc(100, polls=10**6)
    import os
    ticks = iter(range(0, 1000))
    groups = []
    rc = netem.run({}, ["x"], popen=lambda a, **k: child, ns_of=lambda pid: "host",
                   sleep=lambda s: None, killpg=lambda p, s: groups.append((p, s)),
                   clock=lambda: next(ticks), handoff_s=5)
    assert rc == netem.EXIT_HANDOFF_TIMEOUT
    assert "handoff timed out" in capsys.readouterr().err
    assert (100, _signal.SIGTERM) in groups and child.waits  # bounded wait


def test_run_kills_group_when_terminate_is_ignored():
    child = FakeProc(100, polls=10**6, subprocess_wait_timeouts=1)
    groups = []
    ticks = iter(range(0, 1000))
    netem.run({}, ["x"], popen=lambda a, **k: child, ns_of=lambda pid: "host",
              sleep=lambda s: None, killpg=lambda p, s: groups.append((p, s)),
              clock=lambda: next(ticks), handoff_s=3)
    assert (100, _signal.SIGKILL) in groups


def test_run_interrupt_cleans_up_and_returns_143():
    child, slirp = FakeProc(100, polls=10**6), FakeProc(200, polls=10**6)
    calls = {"n": 0}

    def sleep(_s):
        calls["n"] += 1
        raise KeyboardInterrupt

    groups = []
    procs = [child, slirp]
    n = []
    rc = netem.run({}, ["x"], popen=lambda a, **k: (n.append(1), procs[len(n) - 1])[1],
                   ns_of=lambda pid: "host" if pid == __import__("os").getpid() else "new",
                   sleep=sleep, killpg=lambda p, s: groups.append((p, s)), handoff_s=1)
    assert rc == 143 and slirp.terminated and (100, _signal.SIGTERM) in groups


def test_main_installs_sigterm_handler_and_restores(monkeypatch):
    seen = {}

    def fake_run(spec, cmd):
        seen["handler"] = _signal.getsignal(_signal.SIGTERM)
        return 5

    before = _signal.getsignal(_signal.SIGTERM)
    monkeypatch.setattr(netem, "run", fake_run)
    assert netem.main(["--spec", "loss=1%", "--", "true"]) == 5
    assert seen["handler"] is netem._sigterm_to_interrupt
    assert _signal.getsignal(_signal.SIGTERM) == before


def test_main_rejects_bad_usage():
    assert netem.main(["--spec", "loss=1%"]) == 2
    assert netem.main(["--spec", "x=1", "--", "true"]) == 2
    assert netem.main(["--spec", "loss=1%", "--"]) == 2


def test_failure_reason_maps_only_reserved_codes():
    assert netem.failure_reason(97) and netem.failure_reason(98)
    assert netem.failure_reason(96) and netem.failure_reason(95)
    assert netem.failure_reason(0) is None and netem.failure_reason(1) is None


def test_netem_failure_becomes_skipped_not_fail():
    import argparse
    from demo.acceptance import scenarios
    args = argparse.Namespace(netem="loss=2%")
    row = scenarios.netem_setup(args, avail=lambda: (True, ""))
    procs = {"Alex": {"proc": argparse.Namespace(returncode=97)},
             "Sam": {"proc": argparse.Namespace(returncode=0)}}
    scenarios.note_netem_failures(args, procs)
    final = scenarios.netem_final_row(args, row)
    assert final.status == "SKIPPED" and "tap0" in final.evidence


def test_netem_with_transcript_is_rejected(capsys):
    from demo.acceptance import scenarios
    with pytest.raises(SystemExit):
        scenarios.parse_args(["--catalogue", "c", "--via", "transcript", "--out", "o",
                              "--netem", "loss=2%"])
    assert "--netem" in capsys.readouterr().err
