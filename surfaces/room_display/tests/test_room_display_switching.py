"""Issue #173 switching rules (spec 3.7): rest view live; an automatic switch
holds ~30 s; a manual choice pins 2 min; menu by round button and key M."""
from __future__ import annotations

from surfaces.contract.fixtures import Frame
from surfaces.contract.models import (
    AgendaSnapshot, Decision, KnowledgeHit, KnowledgeIntent, KnowledgeSnapshot, StatusIntent,
)
from surfaces.room_display.tests.helpers import empty_snapshot, open_view

HITS = [KnowledgeHit(kind="note", title="Notiz A", href="notes/a.md")]


def knowledge_frames():
    base = empty_snapshot()
    with_hits = base.model_copy(update={"knowledge": KnowledgeSnapshot(topic_id="t1", query="Thema A", hits=HITS)})
    return [
        Frame(delay_s=0.0, snapshot=base, view_intents=[]),
        Frame(delay_s=3.0, snapshot=with_hits, view_intents=[
            KnowledgeIntent(id="knowledge:t1", topic_id="t1", query="Thema A", hits=HITS),
        ]),
    ]


def visible_view(page) -> str:
    return page.locator("[data-view]:visible").get_attribute("data-view")


def freeze_clock(page) -> None:
    """Stop the page's fake clock from flowing with real time, so the real
    seconds a test spends waiting for an SSE event never count against a
    hold or a pin; from here on only `fast_forward` moves it."""
    page.clock.pause_at(page.evaluate("Date.now()") + 1)


def test_auto_switch_holds_30_seconds_then_returns_to_live(contract, surface_url, page):
    api = contract(knowledge_frames())
    page.clock.install()
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    freeze_clock(page)
    page.wait_for_selector('[data-view="memory"]:visible', timeout=10000)
    assert page.locator("html").get_attribute("data-mode") == "auto"
    page.clock.fast_forward(29000)
    assert visible_view(page) == "memory"
    page.clock.fast_forward(2000)
    assert visible_view(page) == "live"


def test_manual_choice_pins_two_minutes_and_ignores_auto_switches(contract, surface_url, page):
    api = contract(knowledge_frames())
    page.clock.install()
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    freeze_clock(page)
    open_view(page, "share")
    assert page.locator("html").get_attribute("data-mode") == "pinned"
    page.wait_for_selector('[data-view="memory"] [data-row]', state="attached", timeout=10000)
    assert visible_view(page) == "share"
    page.clock.fast_forward(119000)
    assert visible_view(page) == "share"
    page.clock.fast_forward(2000)
    assert visible_view(page) == "live"


def test_new_decision_in_a_refetched_snapshot_switches_to_results(contract, surface_url, page):
    base = empty_snapshot()
    decided = base.model_copy(update={"decisions": [
        Decision(id="D1", time="", text="Entscheidung A", added_at="2026-01-01T00:00:00+00:00"),
    ]})
    api = contract([
        Frame(delay_s=0.0, snapshot=base, view_intents=[]),
        Frame(delay_s=2.0, snapshot=decided, view_intents=[StatusIntent(id="s1", text="stage state changed: decisions")]),
    ])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('[data-view="results"]:visible', timeout=10000)
    assert "Entscheidung A" in page.locator('[data-view="results"]').inner_text()


def test_agenda_item_change_switches_to_topics(contract, surface_url, page):
    def agenda(current: int) -> AgendaSnapshot:
        return AgendaSnapshot(current=current, current_text=f"Punkt {current}", total=2, item_started_at=0.0)

    base = empty_snapshot()
    api = contract([
        Frame(delay_s=0.0, snapshot=base.model_copy(update={"agenda": agenda(1)}), view_intents=[]),
        Frame(delay_s=2.0, snapshot=base.model_copy(update={"agenda": agenda(2)}),
              view_intents=[StatusIntent(id="s1", text="stage state changed: agenda")]),
    ])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('[data-view="topics"]:visible', timeout=10000)


def test_menu_opens_by_button_and_key_and_closes_with_escape(contract, surface_url, page):
    api = contract([Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.click("#menu-button")
    assert page.locator("#menu").is_visible()
    page.keyboard.press("Escape")
    assert not page.locator("#menu").is_visible()
    page.keyboard.press("m")
    focused = page.evaluate("document.activeElement.dataset.choose")
    assert focused == "live", "the menu opens on the view shown now"
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert not page.locator("#menu").is_visible()
    assert visible_view(page) == "topics"
    assert page.locator("html").get_attribute("data-mode") == "pinned"
