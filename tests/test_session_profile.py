# CoThinker — tests for shared/profiles.py: session agent profile (ADR 0018)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_session_profile.py"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

from shared import instance as si
from shared import profiles as sp
from shared import templates as st

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "instances" / "example"
RULES = ("grounding", "add-dont-restructure")


@pytest.fixture
def cfg():
    return si.load(EXAMPLE)


def _template_dir(tmp_path: Path, *, en: str | None = "en text", de: str | None = None) -> Path:
    tdir = tmp_path / "template"
    profile = tdir / "profile"
    profile.mkdir(parents=True)
    if en is not None:
        (profile / "SOUL.en.md").write_text(en, encoding="utf-8")
    if de is not None:
        (profile / "SOUL.de.md").write_text(de, encoding="utf-8")
    return tdir


def test_rule_sections_are_appended_in_the_session_language(tmp_path, cfg):
    out = tmp_path / "out"
    sp.materialize_session("main", cfg, out, rules=RULES, language="de",
                            template_dir=None, mcp=None, token=None)
    soul = (out / "SOUL.md").read_text(encoding="utf-8")
    assert sp.SESSION_MARKER in soul
    assert "## Grounding" in soul
    assert "## Ergänzen, nicht umbauen" in soul
    assert soul.index(sp.SESSION_MARKER) < soul.index("## Grounding") < soul.index(
        "## Ergänzen, nicht umbauen")


def test_missing_language_falls_back_to_english(tmp_path, cfg):
    out = tmp_path / "out"
    sp.materialize_session("main", cfg, out, rules=RULES, language="fr",
                            template_dir=None, mcp=None, token=None)
    soul = (out / "SOUL.md").read_text(encoding="utf-8")
    assert "## Grounding" in soul
    assert "## Add, don't restructure" in soul


def test_template_soul_section_follows_the_rules(tmp_path, cfg):
    tdir = _template_dir(tmp_path, en="## Template voice\n\nSpeak like this.\n")
    out = tmp_path / "out"
    sp.materialize_session("main", cfg, out, rules=RULES, language="en",
                            template_dir=tdir, mcp=None, token=None)
    soul = (out / "SOUL.md").read_text(encoding="utf-8")
    assert soul.index("## Add, don't restructure") < soul.index("## Template voice")


def test_mcp_entry_is_written_under_mcp_servers_with_stdio_keys(tmp_path, cfg):
    out = tmp_path / "out"
    entry = sp.mcp_server_entry("http://localhost:8080", "group-1", REPO)
    sp.materialize_session("main", cfg, out, rules=RULES, language="en",
                            template_dir=None, mcp=entry, token="secret-token")
    config = yaml.safe_load((out / "config.yaml").read_text(encoding="utf-8"))
    written = config["mcp_servers"][sp.SERVER_NAME]
    assert written["command"] == "python3"
    assert written["args"] == ["-m", "shared.mcp_stdio", "--url", "http://localhost:8080",
                                "--scope", "group-1"]
    assert written["cwd"] == str(REPO)
    assert written["timeout"] == 120
    assert written["connect_timeout"] == 30
    assert written["env"] == {"COTHINKER_TOOL_TOKEN": "${COTHINKER_TOOL_TOKEN}"}


def test_token_goes_to_env_file_not_config(tmp_path, cfg):
    out = tmp_path / "out"
    entry = sp.mcp_server_entry("http://localhost:8080", "group-1", REPO)
    sp.materialize_session("main", cfg, out, rules=RULES, language="en",
                            template_dir=None, mcp=entry, token="secret-token")
    config_text = (out / "config.yaml").read_text(encoding="utf-8")
    assert "secret-token" not in config_text
    assert "${COTHINKER_TOOL_TOKEN}" in config_text
    env_path = out / ".env"
    assert env_path.read_text(encoding="utf-8") == "COTHINKER_TOOL_TOKEN=secret-token\n"
    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600


def test_instance_overlay_config_keys_survive(tmp_path, cfg):
    out = tmp_path / "out"
    entry = sp.mcp_server_entry("http://localhost:8080", "group-1", REPO)
    sp.materialize_session("main", cfg, out, rules=RULES, language="en",
                            template_dir=None, mcp=entry, token="secret-token")
    config = yaml.safe_load((out / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["default"] == "example-model"
    assert config["providers"]["example_provider"]["key_env"] == "EXAMPLE_PROVIDER_KEY"
    assert config["agent"]["enabled_toolsets"] == ["file"]


def test_unknown_rule_fails(tmp_path, cfg):
    out = tmp_path / "out"
    with pytest.raises(FileNotFoundError, match="no-such-rule"):
        sp.materialize_session("main", cfg, out, rules=("no-such-rule",), language="en",
                                template_dir=None, mcp=None, token=None)


def test_every_core_rule_has_en_and_de_text(cfg):
    for rule in st.CORE_RULES:
        assert (sp.RULES_ROOT / f"{rule}.en.md").is_file(), rule
        assert (sp.RULES_ROOT / f"{rule}.de.md").is_file(), rule
