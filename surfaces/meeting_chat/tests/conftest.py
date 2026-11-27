"""Fixtures for the meeting chat's level-1 tests (ADR 0022): the contract's
own fixture mode (`create_app` + `ScenarioPlayer`) and the surface's static
files, both served by `surfaces.contract.check`'s server threads -- no new
server."""
from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from surfaces.contract.app import create_app
from surfaces.contract.check import _ServerThread, _StaticServerThread
from surfaces.contract.player import ScenarioPlayer

SURFACE_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def surface_url():
    server = _StaticServerThread(SURFACE_DIR)
    base = server.start()
    yield f"{base}/index.html"
    server.stop()


@pytest.fixture
def contract():
    """`contract(frames)` starts a fixture-mode contract server and returns its base URL."""
    servers = []

    def start(frames):
        server = _ServerThread(create_app(ScenarioPlayer(frames, frames[0].snapshot), cors_origins=["*"]))
        servers.append(server)
        return server.start()

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
    context = browser.new_context(viewport={"width": 1024, "height": 768})
    page = context.new_page()
    # Font binaries are not shipped (surfaces/design/build.py documents the gap).
    page.route("**/fonts/*.woff2", lambda route: route.fulfill(status=200, content_type="font/woff2", body=b""))
    page.console_errors = []
    page.on("console", lambda msg: page.console_errors.append(msg.text) if msg.type == "error" else None)
    yield page
    context.close()
