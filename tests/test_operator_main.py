"""The operator console's CLI entry point (issue #159):

    python -m surfaces.operator --fixtures --token-file <path>
    python -m surfaces.operator --demo-kit <demo.sh path> --projects-file <path> --token-file <path>

Mirrors `surfaces/contract/__main__.py`'s `--fixtures`/`--live` mode split
and `--cors-origin` handling, but with a mandatory token source (flag or
`OPERATOR_TOKEN_FILE` env var) instead of an optional one.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import surfaces.operator.__main__ as main_module
from surfaces.operator.__main__ import build_demo_kit_app, build_fixture_app, main
from surfaces.operator.fixtures import ALLOWED_PROJECTS


def test_build_fixture_app_serves_status_with_the_given_token():
    app = build_fixture_app(token="s3cr3t")
    with TestClient(app) as client:
        response = client.get("/operator/status", headers={"Authorization": "Bearer s3cr3t"})
        assert response.status_code == 200


def test_build_demo_kit_app_uses_the_given_demo_sh_and_projects_file(tmp_path):
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([{"id": "p1", "label": "Project one"}]), encoding="utf-8")
    demo_sh = tmp_path / "demo.sh"
    demo_sh.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    app = build_demo_kit_app(demo_sh=demo_sh, projects_file=projects_file, token="s3cr3t")
    with TestClient(app) as client:
        response = client.get("/operator/projects", headers={"Authorization": "Bearer s3cr3t"})
        assert response.status_code == 200
        assert response.json() == [{"id": "p1", "label": "Project one"}]


def test_main_requires_a_mode_flag():
    with pytest.raises(SystemExit):
        main(["--token-file", "/dev/null"])


def test_main_requires_a_token_source(monkeypatch):
    monkeypatch.delenv("OPERATOR_TOKEN_FILE", raising=False)
    assert main(["--fixtures"]) != 0


def test_main_reads_the_token_from_the_token_file_flag(tmp_path):
    token_path = tmp_path / "operator.token"
    token_path.write_text("flag-token\n", encoding="utf-8")

    captured = {}
    original = main_module.build_fixture_app

    def spy(token, cors_origins=None):
        captured["token"] = token
        return original(token, cors_origins=cors_origins)

    main_module.build_fixture_app = spy
    main_module.uvicorn.run = lambda *a, **k: None
    try:
        main(["--fixtures", "--token-file", str(token_path)])
    finally:
        main_module.build_fixture_app = original

    assert captured["token"] == "flag-token"


def test_main_reads_the_token_from_the_env_var_when_the_flag_is_absent(tmp_path, monkeypatch):
    token_path = tmp_path / "operator.token"
    token_path.write_text("env-token\n", encoding="utf-8")
    monkeypatch.setenv("OPERATOR_TOKEN_FILE", str(token_path))

    captured = {}
    original = main_module.build_fixture_app

    def spy(token, cors_origins=None):
        captured["token"] = token
        return original(token, cors_origins=cors_origins)

    main_module.build_fixture_app = spy
    main_module.uvicorn.run = lambda *a, **k: None
    try:
        main(["--fixtures"])
    finally:
        main_module.build_fixture_app = original

    assert captured["token"] == "env-token"


def test_main_defaults_to_binding_127_0_0_1(tmp_path):
    token_path = tmp_path / "operator.token"
    token_path.write_text("t\n", encoding="utf-8")

    captured = {}
    original_run = main_module.uvicorn.run
    original_build = main_module.build_fixture_app
    main_module.uvicorn.run = lambda app, **kwargs: captured.update(kwargs)
    main_module.build_fixture_app = lambda token, cors_origins=None: "app"
    try:
        main(["--fixtures", "--token-file", str(token_path)])
    finally:
        main_module.uvicorn.run = original_run
        main_module.build_fixture_app = original_build
    assert captured["host"] == "127.0.0.1"


def test_main_parses_repeatable_cors_origin_flag(tmp_path):
    token_path = tmp_path / "operator.token"
    token_path.write_text("t\n", encoding="utf-8")

    captured = {}
    original = main_module.build_fixture_app

    def spy(token, cors_origins=None):
        captured["cors_origins"] = cors_origins
        return original(token, cors_origins=cors_origins)

    main_module.build_fixture_app = spy
    main_module.uvicorn.run = lambda *a, **k: None
    try:
        main(["--fixtures", "--token-file", str(token_path),
              "--cors-origin", "http://localhost:5173",
              "--cors-origin", "http://localhost:3000"])
    finally:
        main_module.build_fixture_app = original

    assert captured["cors_origins"] == ["http://localhost:5173", "http://localhost:3000"]


def test_main_demo_kit_mode_requires_projects_file(tmp_path):
    token_path = tmp_path / "operator.token"
    token_path.write_text("t\n", encoding="utf-8")
    assert main(["--demo-kit", "/some/demo.sh", "--token-file", str(token_path)]) != 0
