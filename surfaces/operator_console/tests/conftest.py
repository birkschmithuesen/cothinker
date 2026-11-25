"""Fixtures for the operator console's level-1 tests (ADR 0022): the operator
contract's own fixture backend and app, served by `surfaces.operator.check`'s
server threads -- D1: no new server."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from surfaces.operator.app import create_app
from surfaces.operator.check import _ServerThread, _StaticServerThread
from surfaces.operator.fixtures import FixtureOperatorBackend
from surfaces.operator_console.tests.helpers import TOKEN

SURFACE_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def surface_url():
    server = _StaticServerThread(SURFACE_DIR)
    base = server.start()
    yield f"{base}/index.html"
    server.stop()


@pytest.fixture
def operator():
    """`operator(frames, projects=None)` -> (`?api=` base, the live backend object)."""
    servers = []

    def start(frames, projects=None):
        backend = FixtureOperatorBackend(frames=frames, projects=projects)
        server = _ServerThread(create_app(backend, TOKEN, cors_origins=["*"], push_interval_s=0.2))
        servers.append(server)
        return f"{server.start()}/operator/", backend

    yield start
    for server in servers:
        server.stop()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    # Font binaries are not shipped in this public repo (surfaces/design/build.py
    # documents the gap); a real deployment supplies them. Stub the @font-face
    # request so that known, documented gap never shows up as a console error.
    page.route("**/fonts/*.woff2", lambda route: route.fulfill(status=200, content_type="font/woff2", body=b""))
    page.console_errors = []
    page.on("console", lambda msg: page.console_errors.append(msg.text) if msg.type == "error" else None)
    yield page
    context.close()


@pytest.fixture
def shot_dir(tmp_path):
    target = Path(os.environ.get("COTHINKER_SHOT_DIR", tmp_path)) / "operator_console"
    target.mkdir(parents=True, exist_ok=True)
    return target
