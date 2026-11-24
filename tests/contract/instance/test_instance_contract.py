"""Instance API contract (ADR 0022 level 2; format: docs/instance-format.md;
target: docs/architecture/target.md §2.5).

A subject is an instance directory (the one holding `instance.toml`). Every
assertion message starts with a rule id (INST-...), so a red run names the
rule that broke. Outside instances: `COTHINKER_CONTRACT_INSTANCE=<path to the
instance directory>` (docs/contract-tests.md).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from shared.instance import InstanceError, load, validate

CONTRACT_API = "instance"
REPO = Path(__file__).resolve().parents[3]
BUILTIN_SUBJECTS = {name: REPO / "instances" / name
                    for name in ("example", "example-workshop", "example-production")}
RUN_DATA_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".webm"}


def resolve_outside(value: str) -> Path:
    return Path(value).resolve()


# ---------------------------------------------------------------- contract tests
def test_it_validates_without_errors(subject):
    errors = validate(subject)
    assert errors == [], f"INST-VALIDATE: {'; '.join(errors)}"


def test_it_loads_with_the_manifest_values(subject):
    try:
        cfg = load(subject)
    except InstanceError as e:
        raise AssertionError(f"INST-LOAD: {e}")
    manifest = tomllib.loads((subject / "instance.toml").read_text(encoding="utf-8"))["instance"]
    assert cfg.name == manifest["name"], "INST-LOAD: name differs from the manifest"
    assert cfg.languages and all(isinstance(lang, str) for lang in cfg.languages), (
        "INST-LOAD: languages must be a non-empty list of language codes")


def test_the_command_line_check_says_ok(subject):
    proc = subprocess.run([sys.executable, "-m", "shared.instance", "check", str(subject)],
                          cwd=REPO, capture_output=True, text=True, timeout=60)
    assert (proc.returncode, proc.stdout.strip()) == (0, "ok"), (
        f"INST-CLI: exit {proc.returncode}: {proc.stdout.strip()} {proc.stderr.strip()}")


def test_a_missing_content_path_is_an_error_not_a_guess(subject):
    cfg = load(subject) if not validate(subject) else None
    if cfg is None:
        return  # INST-VALIDATE already reports this subject
    for resolve in (cfg.content, cfg.profile, cfg.acceptance):
        with pytest.raises(InstanceError):
            resolve("contract-test-does-not-exist")
            raise AssertionError(f"INST-PATHS: {resolve.__name__}() returned a missing path")


def test_it_holds_no_run_data(subject):
    audio = sorted(str(p.relative_to(subject)) for p in subject.rglob("*")
                   if p.suffix.lower() in RUN_DATA_SUFFIXES)
    assert not audio, f"INST-NO-RUN-DATA: audio in the instance directory: {audio}"


SUBJECT_TESTS = [
    test_it_validates_without_errors,
    test_it_loads_with_the_manifest_values,
    test_the_command_line_check_says_ok,
    test_a_missing_content_path_is_an_error_not_a_guess,
    test_it_holds_no_run_data,
]


# ------------------------------------------------- red proof: the broken fake
BROKEN_TOML = """\
[instance]
name = "broken-secret"
timezone = "UTC"
languages = ["en"]
app_format = "example-app/1"
poster_format = "example-poster/1"

[secrets]
hub_secret = "s3cr3t-literal-value"  # pragma: allowlist secret
"""


def _failures(instance_dir: Path) -> dict[str, str]:
    """Runs every subject test against `instance_dir`; returns rule id -> message."""
    failures = {}
    for test in SUBJECT_TESTS:
        try:
            test(instance_dir)
        except AssertionError as e:
            failures[str(e).split(":", 1)[0]] = str(e)
    return failures


def test_the_broken_fake_fails_the_contract_only_for_its_literal_secret(tmp_path):
    """Breaks exactly one rule of docs/instance-format.md: a `[secrets]` value
    is a literal, not the name of an environment variable."""
    (tmp_path / "instance.toml").write_text(BROKEN_TOML, encoding="utf-8")
    failures = _failures(tmp_path)
    assert set(failures) == {"INST-VALIDATE", "INST-LOAD", "INST-CLI"}, failures
    assert all("[secrets] hub_secret must name an env variable" in msg
               for msg in failures.values()), failures


def test_an_instance_holding_audio_fails_only_the_run_data_rule(tmp_path):
    instance = tmp_path / "example-with-audio"
    shutil.copytree(REPO / "instances" / "example", instance)
    (instance / "content" / "rec.wav").write_bytes(b"")
    assert set(_failures(instance)) == {"INST-NO-RUN-DATA"}
