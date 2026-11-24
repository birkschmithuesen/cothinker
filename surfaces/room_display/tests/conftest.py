"""Fixtures for the room display's level-1 tests (ADR 0022). The contract's own
fixture mode (`create_app` + `ScenarioPlayer`) and the surface's static files,
both served by `surfaces.contract.check`'s server threads -- D1: no new server."""
from __future__ import annotations

import os
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
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    page.console_errors = []
    page.on("console", lambda msg: page.console_errors.append(msg.text) if msg.type == "error" else None)
    yield page
    context.close()


@pytest.fixture
def shot_dir(tmp_path):
    """PR screenshots go to $COTHINKER_SHOT_DIR/room_display when set, else to tmp."""
    target = Path(os.environ.get("COTHINKER_SHOT_DIR", tmp_path)) / "room_display"
    target.mkdir(parents=True, exist_ok=True)
    return target
