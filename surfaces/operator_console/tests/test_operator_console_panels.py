"""Operator console panels with data (issue #173, spec 4.2), from the operator
fixture's own frames; produces the PR screenshots:
COTHINKER_SHOT_DIR=pr-shots python -m pytest -q surfaces/operator_console/tests/test_operator_console_panels.py"""
from __future__ import annotations

from surfaces.operator.fixtures import build_frames
from surfaces.operator_console.tests.helpers import TOKEN


def test_running_session_fills_every_panel(operator, surface_url, page, shot_dir):
    # Frame "error_lands" (surfaces/operator/fixtures.py:141-146): participants, call, a `warning`-level
    # error, agenda, session title, disk 71 % / 71 GB / 6.1 recording hours left.
    error_lands = build_frames()[4]
    api, _ = operator([error_lands])
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector('html[data-running="true"]')
    for key in ("services", "meeting", "errors"):
        assert page.locator(f'[data-panel="{key}"] [data-row]').count() > 0, key
    for key in ("services", "meeting", "stt", "session", "session-agenda", "call", "errors"):
        assert not page.locator(f'[data-empty="{key}"]').is_visible(), key
    assert "spricht" in page.locator('[data-panel="meeting"]').inner_text()
    errors = page.locator('[data-panel="errors"]').inner_text()
    assert "Warnung · stt: reconnect after timeout" in errors  # level as a word, never colour alone
    session = page.locator('[data-panel="session"]').inner_text()
    assert "Decisions & risks" in session
    assert "Titel" in session and "Quarterly planning" in session
    assert page.locator('[data-panel="disk"] .content').inner_text() == (
        "71,0 % frei · 71,0 GB · Aufnahme reicht noch ca. 6,1 h"
    )
    assert page.locator("#switch").is_disabled()
    assert page.locator("#switch-hint").inner_text() == "Projektwechsel nur im gestoppten Zustand."
    page.screenshot(path=str(shot_dir / "running.png"), full_page=True)
    page.click("#stop")
    assert page.locator("#stop-warning").inner_text() == "Noch 2 Teilnehmende im Meeting."
    page.screenshot(path=str(shot_dir / "stop-dialog.png"), full_page=True)
    assert page.console_errors == []


def test_stopped_state_offers_start(operator, surface_url, page, shot_dir):
    api, _ = operator([build_frames()[0]])
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector('html[data-running="false"]')
    page.wait_for_selector("#start-project option", state="attached")  # an <option> is never "visible"
    assert page.locator("#start-button").is_enabled()
    assert page.locator("#stop").is_disabled()
    assert page.locator("#switch").is_enabled()
    page.screenshot(path=str(shot_dir / "stopped.png"), full_page=True)
