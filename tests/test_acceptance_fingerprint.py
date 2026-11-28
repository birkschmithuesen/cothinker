from pathlib import Path
from demo.acceptance import fingerprint as fpm


def test_digest_stable_and_names_changed_input():
    a = fpm.from_inputs({"unit:stt:argv": b"x --eou-silence 0.5", "repo:head": b"abc"})
    b = fpm.from_inputs({"repo:head": b"abc", "unit:stt:argv": b"x --eou-silence 0.5"})
    c = fpm.from_inputs({"repo:head": b"abc", "unit:stt:argv": b"x --eou-silence 1.0"})
    assert a.digest == b.digest != c.digest
    assert a.changed(c) == ["unit:stt:argv"]


def test_environ_allow_list_drops_secrets_and_pointer():
    raw = b"\0".join([b"HERMES_PROFILE_DIR_CALL=/p/call", b"LANE_MODEL=m", b"SPEECHMATICS_API_KEY=k",
                      b"COTHINKER_SESSION_POINTER=/run/x", b"COTHINKER_DECIDER=jev"]) + b"\0"
    out = fpm.filter_environ(raw)
    assert b"SPEECHMATICS" not in out and b"SESSION_POINTER" not in out
    assert out.split(b"\n") == [b"COTHINKER_DECIDER=jev", b"HERMES_PROFILE_DIR_CALL=/p/call", b"LANE_MODEL=m"]


def test_collect_reads_running_argv_profiles_repo(tmp_path):
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"python\0-m\0stt\0--eou-silence\0001.0\0")
    (proc / "environ").write_bytes(b"LANE_MODEL=m\0")
    prof = tmp_path / "call"
    prof.mkdir()
    (prof / "config.yaml").write_text("agent: {disabled_toolsets: []}\n")
    env = tmp_path / "demo.env"
    env.write_text("DEMO_HOME=/x\n")

    def sh(argv):
        if argv[:3] == ["systemctl", "--user", "show"]:
            return "42\n"
        if argv[:3] == ["systemctl", "--user", "cat"]:
            return "# unit\nExecStart=...\n# drop-in\n"
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return "abc\n"
        return ""

    inputs = fpm.collect(["stt"], repo=tmp_path, demo_env=env, profiles={"call": prof},
                         sh=sh, proc=tmp_path / "proc")
    assert inputs["unit:stt:argv"].endswith(b"1.0\0")
    assert inputs["profile:call:SOUL.md"] == b"<missing>"
    assert set(inputs) >= {"unit:stt:cat", "unit:stt:env", "repo:head", "repo:diff", "demo.env",
                           "profile:call:config.yaml"}


def test_unit_not_running_is_an_input_not_a_crash(tmp_path):
    env = tmp_path / "demo.env"
    env.write_text("")

    def sh(argv):
        return "0\n" if argv[:3] == ["systemctl", "--user", "show"] else ""
    inputs = fpm.collect(["tap"], repo=tmp_path, demo_env=env, profiles={}, sh=sh, proc=tmp_path)
    assert inputs["unit:tap:argv"] == b"<not running>"


def test_local_diff_changes_the_digest(tmp_path):
    base = {"repo:head": b"abc", "repo:diff": b""}
    assert fpm.from_inputs(base).changed(fpm.from_inputs({**base, "repo:diff": b"+x"})) == ["repo:diff"]
