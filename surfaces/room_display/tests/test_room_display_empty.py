"""Issue #173 acceptance: each view renders its empty state with an empty
snapshot -- an explicit element, never sample content (spec section 3.5)."""
from __future__ import annotations

from surfaces.contract.fixtures import Frame
from surfaces.room_display.tests.helpers import VIEW_KEYS, empty_snapshot, open_view

EXPECTED = {
    "live": "Noch nichts gesagt.", "topics": "Noch keine Themen.", "results": "Noch keine Ergebnisse.",
    "queue": "Niemand auf der Redeliste.", "share": "Noch keine Redeanteile.",
    "memory": "Keine Wissenstreffer zum aktuellen Thema.",
}
LEFT = {
    "left-agenda": "Keine Tagesordnung.",
    "left-speaker": "Sprechererkennung nicht aktiv.",
    "left-queue": "Niemand auf der Redeliste.",
}


def test_every_view_and_region_shows_its_empty_state(contract, surface_url, page, shot_dir):
    api = contract([Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="listening"]')

    for key, text in LEFT.items():
        empty = page.locator(f'[data-empty="{key}"]')
        assert empty.is_visible() and empty.inner_text() == text, key

    for key in VIEW_KEYS:
        open_view(page, key)
        view = page.locator(f'[data-view="{key}"]')
        assert view.is_visible(), key
        empty = page.locator(f'[data-empty="{key}"]')
        assert empty.is_visible() and empty.inner_text() == EXPECTED[key], key
        assert view.locator("[data-row]").count() == 0, key
        page.screenshot(path=str(shot_dir / f"empty-{key}.png"))

    assert page.console_errors == []
