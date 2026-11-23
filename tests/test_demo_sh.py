import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SH = str(ROOT / "demo" / "demo.sh")


def _run(*args):
    return subprocess.run(["bash", SH, *args], capture_output=True, text=True)


def test_syntax():
    assert subprocess.run(["bash", "-n", SH]).returncode == 0


def test_help_exits_zero():
    r = _run("--help")
    assert r.returncode == 0 and "usage" in r.stdout


def test_unknown_command_exits_two():
    assert _run("frobnicate").returncode == 2
    assert _run().returncode == 2
    assert _run("start", "--bogus").returncode == 2


def test_units_have_required_directives():
    units = sorted((ROOT / "demo" / "systemd").glob("cothinker-demo-*.service"))
    assert [u.stem for u in units] == [
        "cothinker-demo-" + n
        for n in ("chrome", "consumer", "session", "stage", "stt", "tap")]
    for u in units:
        text = u.read_text()
        for needle in ("Restart=on-failure", "RestartSec=3",
                       "KillMode=control-group"):
            assert needle in text, (u.name, needle)
        # the consumer flushes the protocol on SIGTERM and needs longer
        want = "TimeoutStopSec=90" if u.stem.endswith("consumer") else "TimeoutStopSec=15"
        assert want in text, (u.name, want)
        assert not re.search(r"/home/\w+/", text), u.name


def test_stt_and_tap_share_run_env_and_consumer_has_lane_commands():
    d = ROOT / "demo" / "systemd"
    stt = (d / "cothinker-demo-stt.service").read_text()
    tap = (d / "cothinker-demo-tap.service").read_text()
    assert "run.env" in stt and "run.env" in tap
    assert "--per-track-sidecar ${DEMO_SIDECAR}" in stt
    assert "--record-time" in tap and "DEMO_RUN_TS" in tap
    cons = (d / "cothinker-demo-consumer.service").read_text()
    assert "--lane-call-worker-command" in cons
    assert "--lane-coach-worker-command" in cons


def test_start_keeps_run_env_while_stt_runs():
    text = (ROOT / "demo" / "demo.sh").read_text()
    assert 'unit_active stt && [ -s "$RUN_DIR/run.env" ]' in text
