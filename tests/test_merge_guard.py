"""Tests for tools/merge_guard.py (merge-time publish gate + card-id guard)."""
import os
import subprocess
import sys
from pathlib import Path

MERGE_GUARD = Path(__file__).resolve().parent.parent / "tools" / "merge_guard.py"

# Split so this card-id fixture doesn't itself read as a literal card id in
# this file's own tracked text (same trick as publish_check.py's CANARY) --
# the public denylist scans tracked file content, not just commit history.
CARD_ID = "t_" + "12345678"

# GIT_AUTHOR_NAME/EMAIL and GIT_COMMITTER_NAME/EMAIL in the process
# environment outrank "git config user.*" -- every Hermes agent shell sets
# these, so pin all four explicitly (same reasoning as publish_check.py's
# own self_test()) or every test commit silently carries this session's
# real identity instead of the placeholder below.
_GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV)


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "chore: init")
    return repo


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()


def _run_guard(repo, candidate, home):
    """`home` is an isolated HOME so the real operator's own
    ~/.config/cothinker/denylist.d (if any) never leaks into this test."""
    env = dict(_GIT_ENV, HOME=str(home))
    return subprocess.run(
        [sys.executable, str(MERGE_GUARD), "--repo", str(repo), "--base", "main", "--candidate", candidate],
        capture_output=True, text=True, env=env,
    )


def test_rejects_candidate_with_card_id_in_commit_message(tmp_path):
    repo = _init_repo(tmp_path)
    base_head = _head(repo)
    _git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", f"fix: foo ({CARD_ID})")
    _git(repo, "checkout", "-q", "main")

    result = _run_guard(repo, "feature", tmp_path / "home")

    assert result.returncode != 0
    assert "refused" in result.stdout
    assert "internal work-item id" in result.stdout
    assert CARD_ID not in result.stdout
    assert CARD_ID not in result.stderr
    assert _head(repo) == base_head
    assert not (repo / "feature.txt").exists()


def test_accepts_clean_candidate_and_merges_it(tmp_path):
    repo = _init_repo(tmp_path)
    base_head = _head(repo)
    _git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fix: add feature file")
    _git(repo, "checkout", "-q", "main")

    result = _run_guard(repo, "feature", tmp_path / "home")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo) != base_head
    assert (repo / "feature.txt").exists()
