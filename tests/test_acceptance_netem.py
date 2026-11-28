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
