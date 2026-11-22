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
