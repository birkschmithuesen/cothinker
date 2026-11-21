"""Tests for scripts/check_dco.py (D1: every commit needs a DCO Signed-off-by: trailer)."""
from check_dco import has_signoff, violations


def test_commit_with_signoff_trailer_passes():
    msg = "fix: handle empty input\n\nSigned-off-by: Jane Doe <jane@example.com>\n"
    assert has_signoff(msg)


def test_commit_without_signoff_fails():
    msg = "fix: handle empty input\n"
    assert not has_signoff(msg)


def test_mention_in_prose_does_not_count():
    msg = "docs: explain the Signed-off-by: requirement in CONTRIBUTING.md\n"
    assert not has_signoff(msg)


def test_trailer_without_email_does_not_count():
    msg = "fix: x\n\nSigned-off-by: Jane Doe\n"
    assert not has_signoff(msg)


def test_trailer_among_other_trailers_counts():
    msg = "fix: x\n\nCo-Authored-By: Bot <bot@example.com>\nSigned-off-by: Jane Doe <jane@example.com>\n"
    assert has_signoff(msg)


def test_violations_lists_commits_missing_signoff():
    commits = [
        ("sha1", "fix: a\n\nSigned-off-by: Jane Doe <jane@example.com>\n"),
        ("sha2", "fix: b\n"),
    ]
    found = violations(commits)
    assert found == ["sha2: missing Signed-off-by: trailer (fix: b)"]


def test_violations_empty_when_all_signed_off():
    commits = [("sha1", "fix: a\n\nSigned-off-by: Jane Doe <jane@example.com>\n")]
    assert violations(commits) == []
