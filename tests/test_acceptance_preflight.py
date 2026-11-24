import importlib.util
import json
import sys
from pathlib import Path

import pytest

ACC = Path(__file__).resolve().parent.parent / "demo" / "acceptance"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ACC / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def pf():
    return _load("preflight")


# --------------------------------------------------------------- lane_profile

def _write_environ(proc_root: Path, pid: int, env: dict) -> None:
    entry = "".join(f"{k}={v}\0" for k, v in env.items())
    pid_dir = proc_root / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "environ").write_bytes(entry.encode("utf-8"))


def _write_profile(profiles_dir: Path, name: str, toolsets: list) -> Path:
    profile = profiles_dir / name
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "config.yaml").write_text(
        "agent:\n  enabled_toolsets: [%s]\n" % ", ".join(toolsets),
        encoding="utf-8")
    return profile


def test_lane_profile_pass(pf, tmp_path):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", ["file"])
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"])
    assert rc == 0


def test_lane_profile_fails_without_env_var(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    _write_environ(proc_root, 123, {"SOME_OTHER_VAR": "x"})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out
    assert "HERMES_PROFILE_DIR_CALL" in out


def test_lane_profile_fails_without_toolset(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", ["web"])
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out
    assert str(profile) in out
    assert "web" in out


def test_lane_profile_fails_on_forbidden_glob(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call-coach", ["file"])
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out
    assert str(profile) in out


def test_lane_profile_fails_without_config_yaml(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = tmp_path / "profiles" / "call"
    profile.mkdir(parents=True)
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out


def test_lane_profile_fails_without_environ_file(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "999"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out


def test_resolve_consumer_pid_via_unit(pf):
    calls = []

    class FakeResult:
        returncode = 0
        stdout = "4242\n"
        stderr = ""

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return FakeResult()

    pid, err = pf.resolve_consumer_pid(
        consumer_pid=None, consumer_unit="cothinker-demo-consumer",
        runner=fake_runner)
    assert err is None
    assert pid == 4242
    assert calls[0][:4] == ["systemctl", "--user", "show", "-p"]


def test_resolve_consumer_pid_via_unit_not_running(pf):
    class FakeResult:
        returncode = 0
        stdout = "0\n"
        stderr = ""

    pid, err = pf.resolve_consumer_pid(
        consumer_pid=None, consumer_unit="cothinker-demo-consumer",
        runner=lambda cmd, **kw: FakeResult())
    assert pid is None
    assert "not running" in err


# ----------------------------------------------------------------- eou_silence

def test_eou_silence_pass(pf, tmp_path):
    log = tmp_path / "stt.log"
    log.write_text(
        "line one\n"
        "[sm-rt] turn end eou_silence=1.8 max_delay=0.3\n",
        encoding="utf-8")
    rc = pf.main(["--proc-root", str(tmp_path / "noproc"),
                  "--consumer-pid", "1", "--stt-log", str(log)])
    assert rc == 1  # lane_profile still fails, no environ -- isolate below


def test_eou_silence_check_alone(pf, tmp_path, capsys):
    log = tmp_path / "stt.log"
    log.write_text("[sm-rt] turn end eou_silence=1.8 max_delay=0.3\n",
                    encoding="utf-8")
    check = pf.check_eou_silence(stt_log=log, expect_eou=1.8)
    assert check.status == "PASS"
    assert "eou_silence=1.8" in check.text
    assert "max_delay=0.3" in check.text


def test_eou_silence_wrong_value_fails(pf, tmp_path):
    log = tmp_path / "stt.log"
    log.write_text("[sm-rt] turn end eou_silence=2.5\n", encoding="utf-8")
    check = pf.check_eou_silence(stt_log=log, expect_eou=1.8)
    assert check.status == "FAIL"
    assert "2.5" in check.text


def test_eou_silence_missing_line_fails(pf, tmp_path):
    log = tmp_path / "stt.log"
    log.write_text("no eou line here\n", encoding="utf-8")
    check = pf.check_eou_silence(stt_log=log, expect_eou=1.8)
    assert check.status == "FAIL"


def test_eou_silence_uses_last_matching_line(pf, tmp_path):
    log = tmp_path / "stt.log"
    log.write_text(
        "[sm-rt] turn end eou_silence=2.5\n"
        "[sm-rt] turn end eou_silence=1.8\n",
        encoding="utf-8")
    check = pf.check_eou_silence(stt_log=log, expect_eou=1.8)
    assert check.status == "PASS"


def test_eou_silence_missing_file_fails(pf, tmp_path):
    check = pf.check_eou_silence(stt_log=tmp_path / "nope.log", expect_eou=1.8)
    assert check.status == "FAIL"


# ------------------------------------------------------------ blocks_registered

def test_blocks_registered_pass(pf, tmp_path):
    log = tmp_path / "consumer.log"
    log.write_text(
        "[10:00:00] agent session: s1\n"
        "[10:00:01] block #1 (pause, 10 chars)\n",
        encoding="utf-8")
    check = pf.check_blocks_registered(consumer_log=log, start_marker="agent session:")
    assert check.status == "PASS"


def test_blocks_registered_fails_on_missing_block_after_start(pf, tmp_path):
    log = tmp_path / "consumer.log"
    log.write_text(
        "[10:00:00] block host: no block registered for 'old-block'\n"
        "[10:00:01] agent session: s1\n"
        "[10:00:02] block host: no block registered for 'topics'\n",
        encoding="utf-8")
    check = pf.check_blocks_registered(consumer_log=log, start_marker="agent session:")
    assert check.status == "FAIL"
    assert "topics" in check.text


def test_blocks_registered_ignores_missing_block_before_start(pf, tmp_path):
    log = tmp_path / "consumer.log"
    log.write_text(
        "[10:00:00] block host: no block registered for 'old-block'\n"
        "[10:00:01] agent session: s1\n",
        encoding="utf-8")
    check = pf.check_blocks_registered(consumer_log=log, start_marker="agent session:")
    assert check.status == "PASS"


def test_blocks_registered_fails_without_start_marker(pf, tmp_path):
    log = tmp_path / "consumer.log"
    log.write_text("nothing relevant here\n", encoding="utf-8")
    check = pf.check_blocks_registered(consumer_log=log, start_marker="agent session:")
    assert check.status == "FAIL"


def test_blocks_registered_missing_log_fails(pf, tmp_path):
    check = pf.check_blocks_registered(consumer_log=tmp_path / "nope.log",
                                       start_marker="agent session:")
    assert check.status == "FAIL"


def test_template_blocks_lists_ids_for_reflect_template(pf):
    reflect = (Path(__file__).resolve().parent.parent
               / "templates" / "reflect" / "template.toml")
    check = pf.check_template_blocks(reflect)
    assert check is not None
    assert check.status == "PASS"
    assert "capture-bullets" in check.text
    assert "voice-attribution" in check.text


def test_template_blocks_none_without_template_path(pf):
    assert pf.check_template_blocks(None) is None


def test_template_blocks_skipped_when_format_required(pf, tmp_path):
    tpl = tmp_path / "template.toml"
    tpl.write_text(
        '[template]\n'
        'name = "x"\n'
        'api_version = 1\n'
        'rules = ["grounding", "add-dont-restructure"]\n'
        '\n'
        '[formats.a]\n'
        '[formats.b]\n'
        '\n'
        '[[blocks]]\n'
        'id = "x"\n'
        'format = "a"\n'
        '\n'
        '[[phases]]\n'
        'id = "p"\n'
        'format = "a"\n'
        'blocks = ["x"]\n',
        encoding="utf-8")
    check = pf.check_template_blocks(tpl)
    assert check is not None
    assert check.status == "SKIPPED"


# -------------------------------------------------------------- contract_fields

def test_contract_fields_pass_with_optional_absent(pf):
    snapshot = {"voice": {"speaking": None}, "share": {}, "queue": {}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=["listen", "transcript_recent"])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields"].status == "PASS"
    assert by_name["contract_fields:listen"].status == "SKIPPED"
    assert "feature not merged yet" in by_name["contract_fields:listen"].text
    assert by_name["contract_fields:transcript_recent"].status == "SKIPPED"


def test_contract_fields_fail_on_missing_required(pf):
    snapshot = {"voice": {}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=[])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields"].status == "FAIL"
    assert "share" in by_name["contract_fields"].text
    assert "queue" in by_name["contract_fields"].text


def test_contract_fields_listen_present_pass(pf):
    snapshot = {"voice": {}, "share": {}, "queue": {},
                "listen": {"state": "idle", "since": None, "opened_by": None,
                          "closes_in_s": None, "last_close": None}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=["listen"])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields:listen"].status == "PASS"


def test_contract_fields_listen_without_state_fails(pf):
    snapshot = {"voice": {}, "share": {}, "queue": {}, "listen": {}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=["listen"])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields:listen"].status == "FAIL"


def test_contract_fields_listen_last_close_without_reason_fails(pf):
    snapshot = {"voice": {}, "share": {}, "queue": {},
                "listen": {"state": "idle", "last_close": {"at": 1.0}}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=["listen"])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields:listen"].status == "FAIL"
    assert "reason" in by_name["contract_fields:listen"].text


def test_contract_fields_listen_last_close_with_reason_pass(pf):
    snapshot = {"voice": {}, "share": {}, "queue": {},
                "listen": {"state": "idle",
                          "last_close": {"reason": "end", "at": 1.0}}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=["listen"])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields:listen"].status == "PASS"


def test_load_snapshot_from_file(pf, tmp_path):
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps({"voice": {}}), encoding="utf-8")
    snapshot, err = pf.load_snapshot(snapshot_url=None, token_file=None,
                                     snapshot_file=str(snap_path))
    assert err is None
    assert snapshot == {"voice": {}}


def test_load_snapshot_from_url_never_prints_token(pf, tmp_path, capsys):
    token_file = tmp_path / "token"
    token_file.write_text("super-secret-token\n", encoding="utf-8")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"voice": {}}).encode("utf-8")

    def fake_opener(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return FakeResponse()

    snapshot, err = pf.load_snapshot(
        snapshot_url="https://example.invalid/snapshot",
        token_file=str(token_file), snapshot_file=None, opener=fake_opener)
    assert err is None
    assert snapshot == {"voice": {}}
    assert captured["headers"]["Authorization"] == "Bearer super-secret-token"
    out = capsys.readouterr().out
    assert "super-secret-token" not in out


# --------------------------------------------------------------------- end to end

def test_end_to_end_all_pass(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", ["file"])
    _write_environ(proc_root, 1, {"HERMES_PROFILE_DIR_CALL": str(profile)})

    stt_log = tmp_path / "stt.log"
    stt_log.write_text("[sm-rt] eou_silence=1.8\n", encoding="utf-8")

    consumer_log = tmp_path / "consumer.log"
    consumer_log.write_text("[10:00:00] agent session: s1\n", encoding="utf-8")

    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(
        json.dumps({"voice": {}, "share": {}, "queue": {}}), encoding="utf-8")

    rc = pf.main([
        "--proc-root", str(proc_root), "--consumer-pid", "1",
        "--stt-log", str(stt_log), "--expect-eou", "1.8",
        "--consumer-log", str(consumer_log),
        "--snapshot-file", str(snapshot_file),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    for check_name in ("lane_profile", "eou_silence", "blocks_registered",
                       "contract_fields"):
        assert f"PASS {check_name}:" in out


def test_end_to_end_one_fail_exits_1(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", ["file"])
    _write_environ(proc_root, 1, {"HERMES_PROFILE_DIR_CALL": str(profile)})

    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(json.dumps({"voice": {}}), encoding="utf-8")

    rc = pf.main([
        "--proc-root", str(proc_root), "--consumer-pid", "1",
        "--snapshot-file", str(snapshot_file),
    ])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL contract_fields:" in out


def test_requires_pid_or_unit(pf):
    with pytest.raises(SystemExit) as e:
        pf.main(["--proc-root", "/proc"])
    assert e.value.code == 2
