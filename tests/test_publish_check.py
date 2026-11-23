"""Tests for tools/publish_check.py's public email-real allow-list.

This repo's commit identity (FACTORY.yaml commit_identity) is a GitHub
no-reply address (<user-id>+<login>@users.noreply.github.com). The public
denylist's email-real class allow-lists that whole address *shape* --
GitHub issues one per account and the login/id pairing is already public,
so it is not a leaked operator value -- while still catching every other
real address, including this repo's own earlier, pre-GitHub identity.
"""
from publish_check import ALLOWLIST, PUBLIC_DENYLIST, load_allow, load_list

CURRENT_IDENTITY = "8000653+birkschmithuesen@users.noreply.github.com"
OTHER_GITHUB_NOREPLY = "1+octocat@users.noreply.github.com"
# Split at "@" (same trick as publish_check.py's own CANARY) so these two
# intentionally-real-looking negative-test addresses don't themselves read
# as a contiguous real email in this file's own tracked text -- the public
# denylist scans tracked file content, not just commit history.
OTHER_REAL_ADDRESS = "someone" + "@realmail.org"
RETIRED_REAL_ADDRESS = "robo" + "@birkschmithuesen.com"


def test_public_list_does_not_flag_the_current_identity():
    pl = load_list(PUBLIC_DENYLIST, "public", allow=load_allow(ALLOWLIST))
    assert not pl.hit(f"Robo <{CURRENT_IDENTITY}>")


def test_public_list_does_not_flag_any_github_noreply_address():
    pl = load_list(PUBLIC_DENYLIST, "public", allow=load_allow(ALLOWLIST))
    assert not pl.hit(f"Someone Else <{OTHER_GITHUB_NOREPLY}>")


def test_public_list_still_flags_other_real_addresses():
    pl = load_list(PUBLIC_DENYLIST, "public", allow=load_allow(ALLOWLIST))
    assert pl.hit(f"Someone <{OTHER_REAL_ADDRESS}>")


def test_public_list_still_flags_the_retired_real_address():
    """Proves the github-noreply allow pattern is not broad enough to also
    swallow this repo's own earlier, non-GitHub real address."""
    pl = load_list(PUBLIC_DENYLIST, "public", allow=load_allow(ALLOWLIST))
    assert pl.hit(f"Robo <{RETIRED_REAL_ADDRESS}>")


def test_public_list_without_allow_flags_the_current_identity_too():
    pl = load_list(PUBLIC_DENYLIST, "public")
    assert pl.hit(f"Robo <{CURRENT_IDENTITY}>")


"""Tests for tools/publish_check.py (issue #125: git exports repository-location
variables such as GIT_DIR/GIT_WORK_TREE into every hook; publish_check.py must
strip them from every subprocess call it makes, or self_test() commits onto the
inherited repository and history_lines()/detect_secrets() scan it instead of
the repo they were asked to scan)."""
import subprocess

import publish_check as pc


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                           capture_output=True, text=True).stdout


def _init_repo(path, filename, content):
    path.mkdir()
    _git(path, "init", "-q")
    (path / filename).write_text(content)
    _git(path, "-c", "user.email=t@example.org", "-c", "user.name=t", "add", ".")
    _git(path, "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-qm", "init")
    return _git(path, "rev-parse", "HEAD").strip()


def test_self_test_and_scan_ignore_inherited_git_location_vars(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    second = tmp_path / "second"
    scratch_head = _init_repo(scratch, "scratch.txt", "scratch-marker-1a2b\n")
    _init_repo(second, "second.txt", "second-marker-9f3c\n")

    # Simulate the environment a git hook inherits: GIT_DIR/GIT_WORK_TREE
    # pinned to the repo being pushed (here: scratch).
    monkeypatch.setenv("GIT_DIR", str(scratch / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(scratch))

    lists = [pc.load_list(pc.PUBLIC_DENYLIST, "public")]
    assert pc.self_test(lists) is True
    assert _git(scratch, "rev-parse", "HEAD").strip() == scratch_head

    lines = [line for _, _, line in pc.history_lines(second)]
    assert any("second-marker-9f3c" in line for line in lines)
    assert not any("scratch-marker-1a2b" in line for line in lines)
