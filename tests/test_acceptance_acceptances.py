import shutil
import subprocess

import pytest

from demo.acceptance import acceptances as acc

ENTRY = '[[accept]]\ncheck_id = "scenario.d.topic"\nfingerprint = "d1"\naccepted_by = "Alex Example"\nreason = "known, F2"\n'


def _git(repo, *args, name="Alex Example", email="alex@example.invalid", committer=None):
    cname, cemail = committer or (name, email)
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": cname,
           "GIT_COMMITTER_EMAIL": cemail, "PATH": "/usr/bin:/bin", "HOME": str(repo)}
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def _repo(tmp_path, author=("Alex Example", "alex@example.invalid")):
    _git(tmp_path, "init", "-q")
    (tmp_path / "acceptances.toml").write_text(ENTRY)
    _git(tmp_path, "add", "acceptances.toml")
    _git(tmp_path, "commit", "-qm", "accept", name=author[0], email=author[1])
    return tmp_path / "acceptances.toml"


AGENTS = {("Robo", "robo@example.invalid")}


def test_human_entry_counts(tmp_path):
    ok, rejected = acc.load_verified(_repo(tmp_path), agent_ids=AGENTS)
    assert [a.check_id for a in ok] == ["scenario.d.topic"] and rejected == []
    assert ok[0].verification == "author-checked"


def test_agent_entry_rejected(tmp_path):
    ok, rejected = acc.load_verified(_repo(tmp_path, ("Robo", "robo@example.invalid")), agent_ids=AGENTS)
    assert ok == [] and "agent identity" in rejected[0]


def test_agent_edit_of_human_entry_rejected(tmp_path):
    f = _repo(tmp_path)
    f.write_text(ENTRY.replace('"d1"', '"d2"'))
    _git(tmp_path, "commit", "-qam", "bump", name="Robo", email="robo@example.invalid")
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert ok == [] and "agent identity" in rejected[0]


def test_uncommitted_entry_ignored(tmp_path):
    f = _repo(tmp_path)
    f.write_text(ENTRY + ENTRY.replace("scenario.d.topic", "scenario.a"))
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert ok == [] and "uncommitted" in rejected[0]


def test_signature_rule_rejects_unsigned_commit(tmp_path):
    f = _repo(tmp_path)

    def sh(argv, cwd):
        if "verify-commit" in argv:
            return 1, ""
        return acc.run_sh_in(argv, cwd)
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS, allowed_signers=tmp_path / "signers", sh=sh)
    assert ok == [] and "no valid allowed signature" in rejected[0]

    def sh_ok(argv, cwd):
        return (0, "") if "verify-commit" in argv else acc.run_sh_in(argv, cwd)
    ok, _ = acc.load_verified(f, agent_ids=AGENTS, allowed_signers=tmp_path / "signers", sh=sh_ok)
    assert ok[0].verification == "signature-checked"


def test_file_outside_git_counts_nothing(tmp_path):
    f = tmp_path / "acceptances.toml"
    f.write_text(ENTRY)
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert ok == [] and "not in a git repository" in rejected[0]


def test_blame_failure_counts_nothing(tmp_path):
    f = _repo(tmp_path)

    def sh(argv, cwd):
        if "blame" in argv:
            return 1, ""
        return acc.run_sh_in(argv, cwd)
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS, sh=sh)
    assert ok == [] and "blame" in rejected[0]


def test_agent_identities_from_factory(tmp_path):
    fy = tmp_path / "FACTORY.yaml"
    fy.write_text('commit_identity:\n  name: "Robo"\n  email: "1+x@users.noreply.github.com"\n')
    ids = acc.agent_identities(fy, ["Example Agent <agent@example.invalid>"])
    assert ids == {("Robo", "1+x@users.noreply.github.com"), ("Example Agent", "agent@example.invalid")}


def _edit_hidden(tmp_path, flag):
    f = _repo(tmp_path)
    _git(tmp_path, "update-index", flag, "acceptances.toml")
    f.write_text(ENTRY + ENTRY.replace("scenario.d.topic", "scenario.a"))
    return f


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_worktree_edit_counts_nothing(tmp_path, flag):
    f = _edit_hidden(tmp_path, flag)
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert [a.check_id for a in ok] == ["scenario.d.topic"]  # only the committed entry, never the edit
    assert all("scenario.a" not in a.check_id for a in ok)


def test_zero_sha_blame_rejected_without_status_check(tmp_path):
    f = _repo(tmp_path)

    def sh(argv, cwd):
        if "status" in argv:
            return 0, ""
        rc, out = acc.run_sh_in(argv, cwd)
        if "blame" in argv:
            out = out.replace(out.split()[0], b"0" * 40)
        return rc, out
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS, sh=sh)
    assert ok == [] and "not committed" in rejected[0]


def test_ssh_signature_real(tmp_path):
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen missing")
    keys = tmp_path / "keys"
    keys.mkdir()
    key = keys / "id"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    pub = (keys / "id.pub").read_text().split()
    signers = keys / "signers"
    signers.write_text(f"alex@example.invalid {pub[0]} {pub[1]}\n")
    other = keys / "other"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "acceptances.toml").write_text(ENTRY)
    _git(repo, "add", "acceptances.toml")
    _git(repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "commit", "-S", "-qm", "accept")
    ok, rejected = acc.load_verified(repo / "acceptances.toml", agent_ids=AGENTS, allowed_signers=signers)
    assert [a.verification for a in ok] == ["signature-checked"], rejected
    ok, rejected = acc.load_verified(repo / "acceptances.toml", agent_ids=AGENTS, allowed_signers=keys / "none")
    assert ok == [] and "no valid allowed signature" in rejected[0]
    # a commit signed by a key not in allowed signers is rejected
    (repo / "acceptances.toml").write_text(ENTRY.replace('"d1"', '"d2"'))
    _git(repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={other}", "commit", "-S", "-qam", "bump")
    ok, rejected = acc.load_verified(repo / "acceptances.toml", agent_ids=AGENTS, allowed_signers=signers)
    assert ok == [] and "no valid allowed signature" in rejected[0]


def test_verify_commit_uses_ssh_format(tmp_path):
    f = _repo(tmp_path)
    seen = []

    def sh(argv, cwd):
        if "verify-commit" in argv:
            seen.append(argv)
            return 0, ""
        return acc.run_sh_in(argv, cwd)
    acc.load_verified(f, agent_ids=AGENTS, allowed_signers=tmp_path / "s", sh=sh)
    assert "gpg.format=ssh" in seen[0]
    assert f"gpg.ssh.allowedSignersFile={tmp_path / 's'}" in seen[0]


def _committed(tmp_path, text):
    _git(tmp_path, "init", "-q")
    f = tmp_path / "acceptances.toml"
    f.write_text(text)
    _git(tmp_path, "add", "acceptances.toml")
    _git(tmp_path, "commit", "-qm", "accept")
    return f


@pytest.mark.parametrize("text,word", [
    ("[[accept\nbroken", "invalid TOML"),
    ('[[accept]]\ncheck_id = "x"\nfingerprint = "f"\n', "missing"),
    ('accept = "nope"\n', "list of tables"),
    ('accept = [1, 2]\n', "list of tables"),
])
def test_malformed_input_rejects(tmp_path, text, word):
    ok, rejected = acc.load_verified(_committed(tmp_path, text), agent_ids=AGENTS)
    assert ok == [] and word in rejected[0]


def test_header_hidden_in_multiline_string_rejects_file(tmp_path):
    text = ENTRY.replace('reason = "known, F2"', 'reason = """\n[[accept]]\n"""')
    ok, rejected = acc.load_verified(_committed(tmp_path, text), agent_ids=AGENTS)
    assert ok == [] and "could not be matched" in rejected[0]


def test_header_whitespace_tolerated(tmp_path):
    text = ENTRY.replace("[[accept]]", "[[ accept ]]  # note")
    ok, _ = acc.load_verified(_committed(tmp_path, text), agent_ids=AGENTS)
    assert len(ok) == 1


def test_mailmap_cannot_launder_agent(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".mailmap").write_text("Alex Example <alex@example.invalid> <robo@example.invalid>\n")
    (tmp_path / "acceptances.toml").write_text(ENTRY)
    _git(tmp_path, "add", ".mailmap", "acceptances.toml")
    _git(tmp_path, "commit", "-qm", "accept", name="Robo", email="robo@example.invalid")
    ok, rejected = acc.load_verified(tmp_path / "acceptances.toml", agent_ids=AGENTS)
    assert ok == [] and "agent identity" in rejected[0]


def test_agent_email_matched_case_insensitively_any_name(tmp_path):
    f = _repo(tmp_path, ("Someone Else", "ROBO@Example.Invalid"))
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert ok == [] and "agent identity" in rejected[0]


def _forged_first(tmp_path, comment):
    """Human commit holds ENTRY; an agent commit prepends `comment` plus its own forged entry above it."""
    f = _repo(tmp_path)
    forged = ENTRY.replace("scenario.d.topic", "scenario.forged")
    f.write_bytes((comment + forged + ENTRY).encode("utf-8"))
    _git(tmp_path, "commit", "-qam", "forge", name="Bot", email="bot@example.invalid")
    return f


BOT = {("Bot", "bot@example.invalid")}


# U+2028, U+2029 and NEL are legal in a TOML comment, so the human entry survives; the
# control characters are not, so the whole file is invalid TOML and nothing counts.
@pytest.mark.parametrize("sep,expected", [
    ("\u2028", ["scenario.d.topic"]), ("\u2029", ["scenario.d.topic"]), ("\x85", ["scenario.d.topic"]),
    ("\x0b", []), ("\x0c", []), ("\x1c", []),
])
def test_unicode_line_separator_cannot_shift_agent_entry(tmp_path, sep, expected):
    f = _forged_first(tmp_path, "# c" + sep * 5 + "\n")
    ok, rejected = acc.load_verified(f, agent_ids=BOT)
    assert all(a.check_id != "scenario.forged" for a in ok)
    assert [a.check_id for a in ok] == expected


def test_lone_cr_cannot_shift_agent_entry(tmp_path):
    f = _forged_first(tmp_path, "# c" + "\r" * 5 + "\n")
    ok, _ = acc.load_verified(f, agent_ids=BOT)
    assert all(a.check_id != "scenario.forged" for a in ok)


def test_separator_does_not_misattribute_human_entries(tmp_path):
    f = _forged_first(tmp_path, "# c" + " " * 5 + "\n")
    ok, rejected = acc.load_verified(f, agent_ids=BOT)
    assert "scenario.forged" not in [a.check_id for a in ok]
    assert [a.check_id for a in ok] == ["scenario.d.topic"]
    assert any("scenario.forged" in r and "agent identity" in r for r in rejected)


def test_invalid_utf8_rejects(tmp_path):
    f = _committed(tmp_path, ENTRY)
    f.write_bytes(ENTRY.encode() + b'# \xff\xfe\n')
    _git(tmp_path, "commit", "-qam", "bad")
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert ok == [] and rejected and "UTF-8" in rejected[0]


def test_missing_git_binary_rejects(tmp_path):
    f = _repo(tmp_path)

    def sh(argv, cwd):
        raise FileNotFoundError("git")
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS, sh=sh)
    assert ok == [] and rejected and "git" in rejected[0]


def test_line_count_mismatch_between_file_and_blame_rejects(tmp_path):
    f = _repo(tmp_path)

    def sh(argv, cwd):
        rc, out = acc.run_sh_in(argv, cwd)
        if "blame" in argv:
            out = b"\n".join(out.split(b"\n")[:-30])  # truncate: blame reports fewer lines
        return rc, out
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS, sh=sh)
    assert ok == [] and rejected


def test_agent_committer_rejected(tmp_path):
    f = _repo(tmp_path)
    f.write_text(ENTRY.replace('"d1"', '"d2"'))
    _git(tmp_path, "commit", "-qam", "bump", committer=("Bot", "bot@example.invalid"))
    ok, rejected = acc.load_verified(f, agent_ids=BOT)
    assert ok == [] and "agent identity" in rejected[0]


def test_agent_committer_email_case_insensitive(tmp_path):
    f = _repo(tmp_path)
    f.write_text(ENTRY.replace('"d1"', '"d2"'))
    _git(tmp_path, "commit", "-qam", "bump", committer=("Someone", "BOT@Example.Invalid"))
    ok, rejected = acc.load_verified(f, agent_ids=BOT)
    assert ok == [] and "agent identity" in rejected[0]


def test_agent_name_with_other_email_rejected(tmp_path):
    f = _repo(tmp_path, ("bot", "other@example.invalid"))
    ok, rejected = acc.load_verified(f, agent_ids=BOT)
    assert ok == [] and "agent identity" in rejected[0]


def test_empty_agent_identities_fail_closed(tmp_path):
    ok, rejected = acc.load_verified(_repo(tmp_path), agent_ids=set())
    assert ok == [] and "no agent identities known" in rejected[0]


def _entry(cid, fp):
    return f'[[accept]]\ncheck_id = "{cid}"\nfingerprint = "{fp}"\naccepted_by = "Alex Example"\n'


def test_deletion_only_agent_commit_cannot_splice_human_entries(tmp_path):
    _git(tmp_path, "init", "-q")
    f = tmp_path / "acceptances.toml"
    f.write_text(_entry("scenario.x", "old") + _entry("scenario.y", "cur"))
    _git(tmp_path, "add", "acceptances.toml")
    _git(tmp_path, "commit", "-qm", "accept")
    lines = f.read_text().split("\n")
    # delete X.fingerprint, X.accepted_by, Y.header, Y.check_id: result is check_id x with fingerprint cur
    f.write_text("\n".join(lines[0:2] + lines[6:]))
    _git(tmp_path, "commit", "-qam", "trim", name="Bot", email="bot@example.invalid")
    ok, rejected = acc.load_verified(f, agent_ids=BOT)
    assert ok == []
    assert len(rejected) == 1 and "scenario.x" in rejected[0] and "not contiguous" in rejected[0]


def test_human_entry_edited_in_later_human_commit_rejected(tmp_path):
    f = _repo(tmp_path)
    f.write_text(ENTRY.replace('"d1"', '"d2"'))
    _git(tmp_path, "commit", "-qam", "bump")
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert ok == []
    assert len(rejected) == 1 and "not contiguous in one commit" in rejected[0]


def test_two_untouched_human_entries_both_count(tmp_path):
    _git(tmp_path, "init", "-q")
    f = tmp_path / "acceptances.toml"
    f.write_text(_entry("scenario.x", "a") + _entry("scenario.y", "b"))
    _git(tmp_path, "add", "acceptances.toml")
    _git(tmp_path, "commit", "-qm", "accept")
    ok, rejected = acc.load_verified(f, agent_ids=AGENTS)
    assert [a.check_id for a in ok] == ["scenario.x", "scenario.y"] and rejected == []


def test_blame_ignores_repo_ignore_revs_config(tmp_path):
    f = _repo(tmp_path)
    seen = []

    def sh(argv, cwd):
        if "blame" in argv:
            seen.append(argv)
        return acc.run_sh_in(argv, cwd)
    acc.load_verified(f, agent_ids=AGENTS, sh=sh)
    assert "blame.ignoreRevsFile=" in seen[0]
