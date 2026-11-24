"""CORS for the surface contract (issue #147 point 5): a surface served
from another origin (e.g. a local dev server against
`?api=http://127.0.0.1:8000/`) failed in the browser since the app never
sent an `Access-Control-Allow-Origin` header. Opt-in only: the default
(no flag) must stay same-origin, exactly as before this fix.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from surfaces.contract.__main__ import build_app, main


def test_default_fixture_app_sends_no_cors_header():
    app = build_app("meeting")
    with TestClient(app) as client:
        response = client.get("/snapshot", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in response.headers


def test_cors_origin_flag_adds_the_allow_origin_header():
    app = build_app("meeting", cors_origins=["http://localhost:5173"])
    with TestClient(app) as client:
        response = client.get("/snapshot", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_origin_flag_does_not_allow_an_unlisted_origin():
    app = build_app("meeting", cors_origins=["http://localhost:5173"])
    with TestClient(app) as client:
        response = client.get("/snapshot", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in response.headers


def test_main_parses_repeatable_cors_origin_flag():
    import surfaces.contract.__main__ as main_module

    captured = {}
    original_build_app = main_module.build_app

    def spy_build_app(scenario, cors_origins=None):
        captured["cors_origins"] = cors_origins
        return original_build_app(scenario, cors_origins=cors_origins)

    original_uvicorn_run = main_module.uvicorn.run
    main_module.build_app = spy_build_app
    main_module.uvicorn.run = lambda *a, **k: None
    try:
        main(["--fixtures", "meeting",
              "--cors-origin", "http://localhost:5173",
              "--cors-origin", "http://localhost:3000"])
    finally:
        main_module.build_app = original_build_app
        main_module.uvicorn.run = original_uvicorn_run

    assert captured["cors_origins"] == ["http://localhost:5173", "http://localhost:3000"]
