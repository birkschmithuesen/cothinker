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
    # "-fixtures.service" sorts before ".service" ('-' < '.'), so the
    # longer stem comes first among the two contract units.
    assert [u.stem for u in units] == [
        "cothinker-demo-" + n
        for n in ("chrome", "consumer", "contract-fixtures", "contract",
                   "operator", "session", "stage", "stt", "tap")]
    # contract-fixtures and operator run forever (the design partner's
    # fixture scenario, and the operator console surviving `stop`),
    # unlike every other unit, which only restarts on failure.
    restart_want = {
        "cothinker-demo-contract-fixtures": "Restart=always",
        "cothinker-demo-operator": "Restart=always",
    }
    for u in units:
        text = u.read_text()
        for needle in ("RestartSec=3", "KillMode=control-group"):
            assert needle in text, (u.name, needle)
        assert restart_want.get(u.stem, "Restart=on-failure") in text, u.name
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
    # The coach lane has no CLI flag of its own (gateway/consumer.py reads
    # its worker command from the mirroring-coach block's config, not a
    # flag -- see tests/test_demo_unit_flags.py); it must not be passed one.
    assert "--lane-coach-worker-command" not in cons


def test_start_keeps_run_env_while_stt_runs():
    text = (ROOT / "demo" / "demo.sh").read_text()
    assert 'unit_active stt && [ -s "$RUN_DIR/run.env" ]' in text


def test_demo_units_default_has_contract_after_consumer_without_fixtures():
    text = (ROOT / "demo" / "demo.sh").read_text()
    m = re.search(r'DEMO_UNITS=\$\{DEMO_UNITS:-"([^"]+)"\}', text)
    assert m, "DEMO_UNITS default not found"
    units = m.group(1).split()
    assert units.index("contract") > units.index("consumer")
    assert "contract-fixtures" not in units


def test_port_re_matches_contract_but_not_contract_fixtures():
    text = (ROOT / "demo" / "demo.sh").read_text()
    m = re.search(r"PORT_RE='([^']+)'", text)
    assert m, "PORT_RE not found"
    port_re = m.group(1)
    assert re.search(port_re, ":8092")
    assert not re.search(port_re, ":8093")


def test_start_all_never_guesses_the_newest_session_folder():
    """issue #161: the consumer creates its run's folder only after it
    starts, so resolving "the newest folder under sessions/" before any
    unit starts can pick a prepared future folder or an older one instead
    -- the contract unit then finds no stage state and crash-loops into a
    502. demo.sh must not do this guess at all; the contract follows the
    consumer's own pointer file instead (gateway/consumer.py,
    surfaces/contract --live-pointer)."""
    text = (ROOT / "demo" / "demo.sh").read_text()
    assert "-printf '%T@" not in text
    assert "sort -rn" not in text
    assert "DEMO_SESSION_POINTER" in text


def test_contract_service_follows_the_consumers_session_pointer():
    text = (ROOT / "demo" / "systemd" / "cothinker-demo-contract.service").read_text()
    assert "--live-pointer ${DEMO_SESSION_POINTER}" in text
    assert "DEMO_SESSION_DIR" not in text


def test_consumer_service_points_at_the_same_pointer_file_contract_env_names():
    demo_sh = (ROOT / "demo" / "demo.sh").read_text()
    m = re.search(r"printf 'DEMO_SESSION_POINTER=%s\\n' \"([^\"]+)\"", demo_sh)
    assert m, "demo.sh does not write DEMO_SESSION_POINTER into contract.env"
    run_dir_pointer = m.group(1)
    assert run_dir_pointer == "$RUN_DIR/session.path"

    consumer = (ROOT / "demo" / "systemd" / "cothinker-demo-consumer.service").read_text()
    assert "COTHINKER_SESSION_POINTER=%t/cothinker-demo/session.path" in consumer
