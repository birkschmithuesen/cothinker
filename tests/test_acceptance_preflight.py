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


THR = ["--expect-eou", "1.0", "--expect-max-delay", "2.0"]


@pytest.fixture()
def pf():
    return _load("preflight")


# --------------------------------------------------------------- lane_profile

def _write_environ(proc_root: Path, pid: int, env: dict) -> None:
    entry = "".join(f"{k}={v}\0" for k, v in env.items())
    pid_dir = proc_root / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "environ").write_bytes(entry.encode("utf-8"))


def _write_profile(profiles_dir: Path, name: str, disabled: list) -> Path:
    profile = profiles_dir / name
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "config.yaml").write_text(
        "agent:\n  disabled_toolsets: [%s]\n" % ", ".join(disabled),
        encoding="utf-8")
    return profile


def _write_lanes(tmp_path: Path, proc_root: Path, pid: int) -> list:
    """A consumer command line whose three lanes satisfy the shipped
    invariants; the coach lane resolves to the dedicated profile under a
    fake home. Returns the extra CLI args pointing at that home."""
    home = tmp_path / "home"
    main = _write_profile(tmp_path, "lane-main", ["file"])
    call = _write_profile(tmp_path, "lane-call", [])
    _write_profile(home / ".hermes" / "profiles", "cothinker-coach", ["file"])
    argv = ["python3", "consumer.py",
            "--worker-command", f"py worker.py --hermes-home {main}",
            "--lane-call-worker-command", f"py worker.py --hermes-home {call}"]
    (proc_root / str(pid)).mkdir(parents=True, exist_ok=True)
    (proc_root / str(pid) / "cmdline").write_bytes(
        "".join(a + "\0" for a in argv).encode("utf-8"))
    return ["--home", str(home)]


def test_lane_profile_pass(pf, tmp_path):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", [])
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    extra = _write_lanes(tmp_path, proc_root, 123)
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"] + THR + extra)
    assert rc == 0


def test_lane_profile_fails_without_env_var(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    _write_environ(proc_root, 123, {"SOME_OTHER_VAR": "x"})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"] + THR)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out
    assert "HERMES_PROFILE_DIR_CALL" in out


def test_lane_profile_fails_without_toolset(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", ["file"])
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"] + THR)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out
    assert str(profile) in out
    assert "toolset file disabled" in out


def test_lane_profile_fails_on_forbidden_glob(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call-coach", [])
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"] + THR)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out
    assert str(profile) in out


def test_lane_profile_fails_without_config_yaml(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = tmp_path / "profiles" / "call"
    profile.mkdir(parents=True)
    _write_environ(proc_root, 123, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "123"] + THR)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL lane_profile:" in out


def test_lane_profile_fails_without_environ_file(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "999"] + THR)
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
    assert "topics" in check.evidence


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
    assert "capture-bullets" in check.evidence
    assert "voice-attribution" in check.evidence


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
    assert "feature not merged yet" in by_name["contract_fields:listen"].evidence
    assert by_name["contract_fields:transcript_recent"].status == "SKIPPED"


def test_contract_fields_fail_on_missing_required(pf):
    snapshot = {"voice": {}}
    checks = pf.check_contract_fields(
        snapshot=snapshot, expect_fields=["voice", "share", "queue"],
        optional_fields=[])
    by_name = {c.name: c for c in checks}
    assert by_name["contract_fields"].status == "FAIL"
    assert "share" in by_name["contract_fields"].evidence
    assert "queue" in by_name["contract_fields"].evidence


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
    assert "reason" in by_name["contract_fields:listen"].evidence


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
    profile = _write_profile(tmp_path, "call", [])
    _write_environ(proc_root, 1, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    extra = _write_lanes(tmp_path, proc_root, 1)

    stt_log = tmp_path / "stt.log"
    stt_log.write_text("[sm-rt] language=de max_delay=2.0 eou_silence=1.8 max_speakers=10\n", encoding="utf-8")

    consumer_log = tmp_path / "consumer.log"
    consumer_log.write_text("[10:00:00] agent session: s1\n", encoding="utf-8")

    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(
        json.dumps({"voice": {}, "share": {}, "queue": {}}), encoding="utf-8")

    rc = pf.main([
        "--proc-root", str(proc_root), "--consumer-pid", "1",
        "--stt-log", str(stt_log), "--expect-eou", "1.8",
        "--expect-max-delay", "2.0",
        "--consumer-log", str(consumer_log),
        "--snapshot-file", str(snapshot_file),
    ] + extra)
    out = capsys.readouterr().out
    assert rc == 0
    for check_name in ("lane_profile", "preflight.lane-profile.main",
                       "preflight.lane-profile.coach",
                       "preflight.lane-profile.call", "preflight.stt.eou",
                       "preflight.stt.max_delay", "blocks_registered",
                       "contract_fields"):
        assert f"PASS {check_name}:" in out


def test_end_to_end_one_fail_exits_1(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", [])
    _write_environ(proc_root, 1, {"HERMES_PROFILE_DIR_CALL": str(profile)})

    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(json.dumps({"voice": {}}), encoding="utf-8")

    rc = pf.main([
        "--proc-root", str(proc_root), "--consumer-pid", "1",
        "--snapshot-file", str(snapshot_file),
    ] + THR)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL contract_fields:" in out


def test_requires_pid_or_unit(pf):
    with pytest.raises(SystemExit) as e:
        pf.main(["--proc-root", "/proc"] + THR)
    assert e.value.code == 2


def test_thresholds_are_required_args(pf):
    with pytest.raises(SystemExit) as e:
        pf.main(["--consumer-pid", "1"])
    assert e.value.code == 2


# ----------------------------------------------------- lane-profile invariants

from demo.acceptance import preflight as lane_pf  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "acceptance" / "profiles"
INV = Path(__file__).resolve().parents[1] / "demo" / "acceptance" / "invariants.yaml"
RULES = lane_pf.load_invariants(INV)["lanes"]
ASSISTANT = "You are the assistant the room calls by name."


def _profile(root, name, disabled, soul):
    """A profile that breaks exactly one rule, built per test (rule isolation)."""
    p = root / name
    p.mkdir()
    (p / "config.yaml").write_text("agent: {disabled_toolsets: %s}\n" % disabled)
    (p / "SOUL.md").write_text(soul)
    return p


def test_rule_call_needs_file_toolset(tmp_path):
    p = _profile(tmp_path, "call-x", "[file]", ASSISTANT)
    c = lane_pf.check_lane("call", p, RULES["call"], {"call": p, "coach": FIX / "coach"})
    assert (c.id, c.status) == ("preflight.lane-profile.call", "FAIL")
    assert c.evidence.endswith(": toolset file disabled")


def test_rule_coach_must_not_have_file_toolset(tmp_path):
    p = _profile(tmp_path, "lane-x", "[]", "x")
    c = lane_pf.check_lane("coach", p, RULES["coach"], {"coach": p})
    assert (c.id, c.status) == ("preflight.lane-profile.coach", "FAIL") and "toolset file enabled" in c.evidence


def test_rule_main_must_not_have_file_toolset(tmp_path):
    p = _profile(tmp_path, "lane-y", "[]", "x")
    c = lane_pf.check_lane("main", p, RULES["main"], {"main": p})
    assert (c.id, c.status) == ("preflight.lane-profile.main", "FAIL") and "toolset file enabled" in c.evidence


def test_rule_call_soul_must_not_be_coach(tmp_path):
    p = _profile(tmp_path, "call-x", "[]", "You are the Coach lane.")
    c = lane_pf.check_lane("call", p, RULES["call"], {"call": p, "coach": FIX / "coach"})
    assert c.status == "FAIL" and c.evidence.endswith(r": SOUL matches (?i)\bcoach\b")


def test_rule_call_path_must_not_be_coach(tmp_path):
    p = _profile(tmp_path, "coach-named", "[]", ASSISTANT)
    c = lane_pf.check_lane("call", p, RULES["call"], {"call": p, "coach": FIX / "coach"})
    assert c.status == "FAIL" and c.evidence.endswith(": path matches *coach*")


def test_rule_call_distinct_from_coach(tmp_path):
    p = _profile(tmp_path, "call-x", "[]", ASSISTANT)
    c = lane_pf.check_lane("call", p, RULES["call"], {"call": p, "coach": p})
    assert c.status == "FAIL" and c.evidence.endswith(": same profile as coach")


def test_missing_profile_config_fails(tmp_path):
    c = lane_pf.check_lane("call", tmp_path, RULES["call"], {})
    assert c.status == "FAIL" and "no profile config found" in c.evidence


def test_profile_without_toolset_keys_counts_as_nothing_disabled(tmp_path):
    p = tmp_path / "bare"
    p.mkdir()
    (p / "config.yaml").write_text("agent: {enabled_toolsets: [file]}\n")
    c = lane_pf.check_lane("main", p, RULES["main"], {"main": p})
    assert c.status == "FAIL" and c.evidence.endswith(": toolset file enabled")


def _argv(main, call):
    return ["python3", "consumer.py", "--worker-command", f"env X=1 py worker.py --hermes-home {main} --model m",
            "--lane-call-worker-command", f"env X=1 py worker.py --hermes-home {call} --no-history"]


def test_lane_profiles_explicit_coach_command(tmp_path):
    lp = lane_pf.lane_profiles(_argv(FIX / "main", FIX / "call-ok"),
                               coach_command=f"py worker.py --hermes-home {FIX / 'coach'}",
                               home=tmp_path)
    assert lp["coach"] == FIX / "coach"


def test_lane_profiles_call_falls_back_to_dedicated_profile(tmp_path):
    argv = ["consumer.py", "--worker-command", f"py worker.py --hermes-home {FIX / 'main'}"]
    lp = lane_pf.lane_profiles(argv, coach_command="", home=tmp_path)
    assert lp["call"] == tmp_path / ".hermes" / "profiles" / "cothinker"
    assert lp["main"] == FIX / "main"


def test_lane_profiles_coach_falls_back_to_dedicated_profile(tmp_path):
    lp = lane_pf.lane_profiles(_argv(FIX / "main", FIX / "call-ok"), coach_command="", home=tmp_path)
    assert lp == {"main": FIX / "main", "call": FIX / "call-ok",
                  "coach": tmp_path / ".hermes" / "profiles" / "cothinker-coach"}


def test_call_lane_on_coach_profile_fails_with_found_path():      # failure 1, red
    lp = {"main": FIX / "main", "coach": FIX / "coach", "call": FIX / "call-coach"}
    c = lane_pf.check_lane("call", lp["call"], RULES["call"], lp)
    assert c.status == "FAIL" and "call-coach" in c.evidence and "file" in c.evidence


def test_lanes_ok():
    lp = {"main": FIX / "main", "coach": FIX / "coach", "call": FIX / "call-ok"}
    for lane in ("main", "call", "coach"):
        assert lane_pf.check_lane(lane, lp[lane], RULES[lane], lp).status == "PASS"


def test_call_equal_to_coach_path_fails():
    lp = {"main": FIX / "call-ok", "coach": FIX / "call-ok", "call": FIX / "call-ok"}
    c = lane_pf.check_lane("call", lp["call"], RULES["call"], lp)
    assert c.status == "FAIL" and "same profile as coach" in c.evidence


def test_main_reports_every_lane_and_fails_on_bad_call_lane(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    extra = _write_lanes(tmp_path, proc_root, 7)
    _write_profile(tmp_path, "lane-call", ["file"])   # call lane loses its file toolset
    _write_environ(proc_root, 7, {"HERMES_PROFILE_DIR_CALL": str(tmp_path / "lane-call")})
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "7"] + THR + extra)
    out = capsys.readouterr().out
    assert rc == 1
    assert "PASS preflight.lane-profile.main:" in out
    assert "PASS preflight.lane-profile.coach:" in out
    assert "FAIL preflight.lane-profile.call:" in out and "toolset file disabled" in out


# ------------------------------------------- stt, registry, pending unit changes

LOG = ("[sm-rt] language=de max_delay=2.0 eou_silence=0.5 max_speakers=10\n"
       "... restart ...\n"
       "[sm-rt] language=de max_delay=2.0 eou_silence=1.0 max_speakers=10\n")


def test_stt_values_take_last_start(pf):
    assert pf.stt_values(LOG) == {"eou": 1.0, "max_delay": 2.0}


def test_check_stt_fails_with_found_values(pf):
    eou, md = pf.check_stt({"eou": 0.5, "max_delay": 2.0}, expect_eou=1.0, expect_max_delay=2.0)
    assert (eou.status, md.status) == ("FAIL", "PASS")
    assert "found eou_silence=0.5" in eou.evidence and "expected 1.0" in eou.evidence


def test_check_stt_max_delay_rule(pf):
    eou, md = pf.check_stt({"eou": 1.0, "max_delay": 1.0}, expect_eou=1.0, expect_max_delay=2.0)
    assert (eou.status, md.id, md.status) == ("PASS", "preflight.stt.max_delay", "FAIL")
    assert "found max_delay=1.0" in md.evidence


def test_check_stt_without_log_line_fails(pf):
    eou, md = pf.check_stt(pf.stt_values("no start line"), expect_eou=1.0, expect_max_delay=2.0)
    assert eou.status == md.status == "FAIL" and "found eou_silence=None" in eou.evidence


TEMPLATE = ('[template]\nname = "X"\napi_version = 1\nrules = ["grounding", "add-dont-restructure"]\n'
            '[[blocks]]\nid = "timer"\n[[blocks]]\nid = "ghost"\n[[phases]]\nid = "p"\nblocks = ["timer"]\n')


def test_registry_missing_block(pf, tmp_path):
    t = tmp_path / "template.toml"
    t.write_text(TEMPLATE)
    c = pf.check_registry(t, {"timer": object}, log_tail="")
    assert (c.id, c.status) == ("preflight.blocks.registry", "FAIL") and "ghost" in c.evidence


def test_registry_complete_passes(pf, tmp_path):
    t = tmp_path / "template.toml"
    t.write_text(TEMPLATE)
    assert pf.check_registry(t, {"timer": object, "ghost": object}, log_tail="").status == "PASS"


def test_registry_log_line_fails(pf):
    c = pf.check_registry(None, {}, log_tail="block host: no block registered for 'capture-bullets'\n")
    assert c.status == "FAIL" and "capture-bullets" in c.evidence


def test_unit_changed_after_start_fails(pf):
    assert pf.check_unit_pending("stt", need_reload=False, changed_after_start=1).status == "FAIL"
    assert pf.check_unit_pending("stt", need_reload=True, changed_after_start=0).status == "FAIL"
    assert pf.check_unit_pending("stt", need_reload=False, changed_after_start=0).status == "PASS"


def test_unit_pending_inputs_counts_newer_files_read_only(pf, tmp_path):
    old = tmp_path / "old.service"
    new = tmp_path / "new.conf"
    old.write_text("x")
    new.write_text("x")
    import os
    os.utime(old, (1000, 1000))
    os.utime(new, (3000, 3000))
    calls = []

    def sh(argv):
        calls.append(argv)
        if argv[0] == "date":
            return "2000\n"
        return (f"NeedDaemonReload=no\nActiveEnterTimestamp=Tue 2026-11-24 20:42:11 CET\n"
                f"FragmentPath={old}\nDropInPaths={new}\n")

    assert pf._unit_pending_inputs("stt", sh) == (False, 1)
    assert all(c[0] in ("systemctl", "date") and c[2] == "show" for c in calls if c[0] == "systemctl")
    assert not any(w in " ".join(sum(calls, [])) for w in ("restart", "reload ", "daemon-reload"))


def _unit_sh(props, date_out="2000\n"):
    def sh(argv):
        return date_out if argv[0] == "date" else props
    return sh


def test_unit_pending_empty_start_time_fails(pf, tmp_path):
    f = tmp_path / "u.service"
    f.write_text("x")
    sh = _unit_sh(f"NeedDaemonReload=no\nActiveEnterTimestamp=\nFragmentPath={f}\nDropInPaths=\n")
    need, changed = pf._unit_pending_inputs("stt", sh)
    c = pf.check_unit_pending("stt", need, changed)
    assert c.status == "FAIL" and "start time not determinable" in c.evidence


def test_unit_pending_non_digit_date_output_fails(pf, tmp_path):
    f = tmp_path / "u.service"
    f.write_text("x")
    sh = _unit_sh(f"NeedDaemonReload=no\nActiveEnterTimestamp=garbage\nFragmentPath={f}\nDropInPaths=\n",
                  date_out="date: invalid date\n")
    need, changed = pf._unit_pending_inputs("stt", sh)
    c = pf.check_unit_pending("stt", need, changed)
    assert c.status == "FAIL" and "start time not determinable" in c.evidence


def test_unit_pending_unreadable_unit_file_fails(pf, tmp_path):
    gone = tmp_path / "missing.service"
    sh = _unit_sh(f"NeedDaemonReload=no\nActiveEnterTimestamp=Tue 2026-11-24 20:42:11 CET\n"
                  f"FragmentPath={gone}\nDropInPaths=\n")
    need, changed = pf._unit_pending_inputs("stt", sh)
    c = pf.check_unit_pending("stt", need, changed)
    assert c.status == "FAIL" and "cannot stat 1 unit file(s)" in c.evidence


def test_main_without_unit_reports_skipped(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    extra = _write_lanes(tmp_path, proc_root, 7)
    pf.main(["--proc-root", str(proc_root), "--consumer-pid", "7"] + THR + extra)
    out = capsys.readouterr().out
    assert "SKIPPED preflight.unit.pending: no --unit given" in out


def test_main_registry_check_fails_for_unresolvable_block(pf, tmp_path, capsys):
    proc_root = tmp_path / "proc"
    extra = _write_lanes(tmp_path, proc_root, 7)
    tpl = tmp_path / "template.toml"
    tpl.write_text(TEMPLATE.replace('"ghost"', '"no-such-block-zz"'))
    log = tmp_path / "consumer.log"
    log.write_text("started\n")
    pf.main(["--proc-root", str(proc_root), "--consumer-pid", "7"] + THR + extra
            + ["--consumer-log", str(log), "--template", str(tpl)])
    out = capsys.readouterr().out
    assert "FAIL preflight.blocks.registry:" in out and "no-such-block-zz" in out


def test_main_out_writes_report_with_fingerprint(pf, tmp_path, monkeypatch, capsys):
    proc_root = tmp_path / "proc"
    profile = _write_profile(tmp_path, "call", [])
    _write_environ(proc_root, 9, {"HERMES_PROFILE_DIR_CALL": str(profile)})
    extra = _write_lanes(tmp_path, proc_root, 9)
    seen = {}

    def fake_collect(units, **kw):
        seen.update(kw)
        return {"unit:x:env": b"SECRET_VALUE_123"}

    monkeypatch.setattr(pf.fingerprint, "collect", fake_collect)
    monkeypatch.setattr(pf, "_repo_of_unit", lambda unit: tmp_path)
    monkeypatch.setenv("DEMO_ENV", str(tmp_path / "demo.env"))
    out = tmp_path / "out"
    rc = pf.main(["--proc-root", str(proc_root), "--consumer-pid", "9",
                  "--consumer-unit", "u.service", "--out", str(out)] + THR + extra)
    assert rc == 0
    assert seen["repo"] == tmp_path and seen["demo_env"] == tmp_path / "demo.env"
    assert set(seen["profiles"]) == {"main", "call", "coach"}
    (d,) = list(out.glob("preflight-*"))
    text = (d / "report.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert d.name == "preflight-" + data["run_id"]
    assert data["fingerprint"]["digest"]
    assert "SECRET_VALUE_123" not in text
    assert any(c["id"] == "preflight.fingerprint" and c["status"] == "PASS"
               for c in data["checks"])
