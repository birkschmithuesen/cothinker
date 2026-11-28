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


def test_local_diff_changes_the_digest():
    base = {"repo:head": b"abc", "repo:diff": b""}
    assert fpm.from_inputs(base).changed(fpm.from_inputs({**base, "repo:diff": b"+x"})) == ["repo:diff"]


def _sh_stub(untracked=""):
    def sh(argv):
        if argv[:3] == ["systemctl", "--user", "show"]:
            return "0\n"
        if "--others" in argv:
            return untracked
        return ""
    return sh


def test_profile_skills_edit_shows_in_changed(tmp_path):
    env = tmp_path / "demo.env"
    env.write_text("")
    prof = tmp_path / "main"
    (prof / "skills" / "x").mkdir(parents=True)
    (prof / "skills" / "x" / "y.md").write_text("one")

    def run():
        return fpm.from_inputs(fpm.collect([], repo=tmp_path, demo_env=env, profiles={"main": prof},
                                           sh=_sh_stub(), proc=tmp_path))
    before = run()
    (prof / "skills" / "x" / "y.md").write_text("two")
    assert before.changed(run()) == ["profile:main:skills/x/y.md"]


def test_profile_without_skills_dir_is_missing_input(tmp_path):
    env = tmp_path / "demo.env"
    env.write_text("")
    prof = tmp_path / "main"
    prof.mkdir()
    inputs = fpm.collect([], repo=tmp_path, demo_env=env, profiles={"main": prof},
                         sh=_sh_stub(), proc=tmp_path)
    assert inputs["profile:main:skills"] == b"<missing>"


def test_untracked_file_changes_the_digest(tmp_path):
    env = tmp_path / "demo.env"
    env.write_text("")

    def run():
        return fpm.from_inputs(fpm.collect([], repo=tmp_path, demo_env=env, profiles={},
                                           sh=_sh_stub("new.txt\n"), proc=tmp_path))
    before = fpm.from_inputs(fpm.collect([], repo=tmp_path, demo_env=env, profiles={},
                                         sh=_sh_stub(""), proc=tmp_path))
    (tmp_path / "new.txt").write_text("a")
    first = run()
    assert before.changed(first) == ["repo:untracked"]
    (tmp_path / "new.txt").write_text("b")
    assert first.changed(run()) == ["repo:untracked"]


def test_unreadable_proc_is_an_input_not_a_crash(tmp_path):
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)  # exists, but cmdline/environ cannot be read
    env = tmp_path / "demo.env"
    env.write_text("")

    def sh(argv):
        return "42\n" if argv[:3] == ["systemctl", "--user", "show"] else ""
    inputs = fpm.collect(["stt"], repo=tmp_path, demo_env=env, profiles={}, sh=sh,
                         proc=tmp_path / "proc")
    assert inputs["unit:stt:argv"] == b"<unreadable>"
    assert inputs["unit:stt:env"] == b"<unreadable>"


def _git_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    (repo / "pkg" / "__pycache__").mkdir(parents=True)
    (repo / "mod.py").write_text("x = 1\n")
    (repo / "pkg" / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"v1")
    (repo / "top.pyc").write_bytes(b"v1")

    def git(*a):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.org", *a],
                       check=True, capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    return repo


def _digest_of(repo, tmp_path):
    env = tmp_path / "demo.env"
    env.write_text("")
    return fpm.from_inputs(fpm.collect([], repo=repo, demo_env=env, profiles={}))


def test_bytecode_does_not_change_the_digest_but_source_does(tmp_path):
    repo = _git_repo(tmp_path)
    base = _digest_of(repo, tmp_path).digest
    (repo / "pkg" / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"v2")
    (repo / "top.pyc").write_bytes(b"v2")
    (repo / "pkg" / "__pycache__" / "new.cpython-311.pyc").write_bytes(b"new")
    (repo / "fresh.pyc").write_bytes(b"new")
    assert _digest_of(repo, tmp_path).digest == base
    (repo / "mod.py").write_text("x = 2\n")
    assert _digest_of(repo, tmp_path).digest != base


def test_missing_profile_dir_is_recorded_not_a_crash(tmp_path):
    env = tmp_path / "demo.env"
    env.write_text("")
    inputs = fpm.collect([], repo=tmp_path, demo_env=env, profiles={"main": None}, sh=lambda argv: "")
    assert inputs["profile:main:config.yaml"] == b"<missing>"
    assert inputs["profile:main:skills"] == b"<missing>"
