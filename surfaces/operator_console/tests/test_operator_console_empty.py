"""Issue #173 acceptance: every operator console panel renders its empty
state with an empty status snapshot (spec 4.2)."""
from __future__ import annotations

from surfaces.operator.fixtures import OperatorFrame
from surfaces.operator_console.tests.helpers import TOKEN, empty_status

EXPECTED = {
    "control": "No projects available.", "services": "No services reported.",
    "meeting": "Nobody in the meeting.", "stt": "Speech recognition not connected.",
    "session": "No session running.", "session-agenda": "No agenda.",
    "call": "No call yet.", "errors": "No errors.",
}


def test_every_panel_shows_its_empty_state(operator, surface_url, page, shot_dir):
    api, _ = operator([OperatorFrame(delay_s=0.0, status=empty_status())], projects=())
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#updates > div")
    for key, text in EXPECTED.items():
        empty = page.locator(f'[data-empty="{key}"]')
        assert empty.is_visible() and empty.inner_text() == text, key
    for key in ("services", "meeting", "errors"):
        assert page.locator(f'[data-panel="{key}"] [data-row]').count() == 0, key
    assert page.locator("#start-button").is_disabled()
    page.screenshot(path=str(shot_dir / "empty.png"), full_page=True)
    assert page.console_errors == []
