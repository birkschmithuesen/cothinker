# CoThinker — shared/instance.py: generic instance loader
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_shared_instance.py"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from shared import instance as si

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "instances" / "example"


@pytest.fixture
def cfg():
    return si.load(EXAMPLE)


def test_example_loads_by_path(cfg):
    assert cfg.name == "example" and cfg.root == EXAMPLE
    assert cfg.languages == ("de", "en"), cfg.languages
    assert cfg.modules == ("example_echo",), cfg.modules
    assert cfg.secret_env.get("hub_secret") == "EXAMPLE_HUB_SECRET"


def test_no_path_and_no_env_raises_naming_the_variable(monkeypatch):
    monkeypatch.delenv(si.ENV_DIR, raising=False)
    with pytest.raises(si.InstanceError, match=si.ENV_DIR):
        si.load()


def test_for_repo_without_env_never_picks_an_instance_itself(monkeypatch):
    monkeypatch.delenv(si.ENV_DIR, raising=False)
    with pytest.raises(si.InstanceError, match=si.ENV_DIR):
        si.for_repo(REPO)


def test_env_variable_resolves_for_load_and_for_repo(monkeypatch):
    monkeypatch.setenv(si.ENV_DIR, str(EXAMPLE))
    assert si.load().name == "example"
    assert si.for_repo(REPO).name == "example"
    assert si.for_repo(None).name == "example"


def test_missing_instance_toml_reported(tmp_path):
    assert si.validate(tmp_path) == ["instance.toml missing"]


def test_missing_keys_and_literal_secret_rejected(tmp_path):
    (tmp_path / "instance.toml").write_text(
        '[instance]\nname = "x"\n[secrets]\nk = "«redacted:sk-…»"\n', encoding="utf-8")
    errs = si.validate(tmp_path)
    assert any("timezone missing" in e for e in errs), errs
    assert any("[secrets] k" in e for e in errs), errs


def test_env_view_carries_name_dir_tz_languages_and_hosts(cfg):
    view = si.env_view(cfg)
    assert view["COTHINKER_INSTANCE_NAME"] == "example"
    assert view["COTHINKER_LANGUAGES"] == "de en"
    assert "COTHINKER_HOST_WALL" in view, view


def test_getenv_reads_cothinker_prefix(monkeypatch):
    monkeypatch.delenv("COTHINKER_SURELY_UNSET_X", raising=False)
    assert si.getenv("SURELY_UNSET_X", "d") == "d"


def test_no_legacy_layout_left():
    assert not hasattr(si, "LEGACY_SETTINGS") and not hasattr(si, "_legacy")
    assert "legacy" not in si.InstanceConfig.__dataclass_fields__


def test_cli_check_prints_ok_for_the_example_instance():
    r = subprocess.run([sys.executable, "-m", "shared.instance", "check", str(EXAMPLE)],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stdout + r.stderr


def test_shared_modules_and_profiles_import():
    r = subprocess.run([sys.executable, "-c", "import shared.modules, shared.profiles"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_example_has_no_template_or_block_or_module_tables(cfg):
    assert cfg.template_name() is None
    assert cfg.block_config("timer") == {}
    assert cfg.module_config("example_echo") == {}


def _instance_dir_with_tables(tmp_path):
    (tmp_path / "instance.toml").write_text(
        '[instance]\nname = "x"\ntimezone = "UTC"\nlanguages = ["en"]\n'
        'app_format = "x/1"\nposter_format = "x/1"\n'
        '[template]\nname = "reflect"\n'
        '[blocks.timer]\ndefault_minutes = 45\n'
        '[modules.example_echo]\nvolume = 3\n',
        encoding="utf-8",
    )
    return tmp_path


def test_template_name_reads_template_table(tmp_path):
    cfg = si._from_dir(_instance_dir_with_tables(tmp_path))
    assert cfg.template_name() == "reflect"


def test_block_config_reads_blocks_table(tmp_path):
    cfg = si._from_dir(_instance_dir_with_tables(tmp_path))
    assert cfg.block_config("timer") == {"default_minutes": 45}
    assert cfg.block_config("missing") == {}


def test_module_config_reads_modules_table(tmp_path):
    cfg = si._from_dir(_instance_dir_with_tables(tmp_path))
    assert cfg.module_config("example_echo") == {"volume": 3}
    assert cfg.module_config("missing") == {}
