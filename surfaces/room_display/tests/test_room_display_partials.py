"""Partial (provisional) transcript lines (issue #206, live-trial feedback:
distracting for some) -- default OFF, togglable with the `P` key, muted,
shown below the committed lines of the `live` view only while held on."""
from __future__ import annotations

from surfaces.contract.fixtures import Frame
from surfaces.contract.models import (
    TranscriptLine, TranscriptPartialIntent, TranscriptPartialSnapshot, TranscriptRecentIntent,
)
from surfaces.room_display.tests.helpers import empty_snapshot


def snapshot_with_partial():
    return empty_snapshot().model_copy(update={
        "transcript_recent": [TranscriptLine(speaker="Tamar", started_at="2026-11-27T09:00:00+00:00", text="Let's park the vendor question.")],
        "transcript_partial": TranscriptPartialSnapshot(
            turn_id="t1", speaker="Jonas", text="And then we agreed", started_at="2026-11-27T09:01:00+00:00",
        ),
    })


def test_partial_is_hidden_by_default(contract, surface_url, page):
    api = contract([Frame(delay_s=0.0, snapshot=snapshot_with_partial(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    assert page.locator('[data-view="live"] [data-partial]').count() == 0
    assert "Let's park the vendor question." in page.locator('[data-view="live"]').inner_text()


def test_pressing_p_shows_the_partial_muted_below_committed_lines(contract, surface_url, page):
    api = contract([Frame(delay_s=0.0, snapshot=snapshot_with_partial(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.keyboard.press("p")
    rows = page.locator('[data-view="live"] [data-row]')
    assert rows.count() == 2
    assert "And then we agreed" in rows.nth(1).inner_text()
    assert rows.nth(1).get_attribute("data-partial") is not None


def test_pressing_p_twice_hides_it_again(contract, surface_url, page):
    api = contract([Frame(delay_s=0.0, snapshot=snapshot_with_partial(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.keyboard.press("p")
    assert page.locator('[data-view="live"] [data-partial]').count() == 1
    page.keyboard.press("p")
    assert page.locator('[data-view="live"] [data-partial]').count() == 0


def test_transcript_partial_intent_updates_the_shown_partial(contract, surface_url, page):
    api = contract([
        Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[]),
        Frame(delay_s=2.0, snapshot=empty_snapshot().model_copy(update={
            "transcript_partial": TranscriptPartialSnapshot(
                turn_id="t1", speaker="Mira", text="Let's", started_at="2026-11-27T09:01:00+00:00",
            ),
        }), view_intents=[
            TranscriptPartialIntent(id="transcript:partial", turn_id="t1", speaker="Mira", text="Let's",
                                     started_at="2026-11-27T09:01:00+00:00"),
        ]),
    ])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.keyboard.press("p")
    page.wait_for_selector('[data-view="live"] [data-partial]', timeout=10000)
    assert "Let's" in page.locator('[data-view="live"] [data-partial]').inner_text()


def test_committed_transcript_recent_clears_the_partial_even_while_shown(contract, surface_url, page):
    committed = empty_snapshot().model_copy(update={
        "transcript_recent": [TranscriptLine(speaker="Mira", started_at="2026-11-27T09:01:05+00:00", text="Let's agree on the timeline.")],
    })
    api = contract([
        Frame(delay_s=0.0, snapshot=snapshot_with_partial(), view_intents=[]),
        Frame(delay_s=2.0, snapshot=committed, view_intents=[
            TranscriptRecentIntent(id="transcript:recent", lines=committed.transcript_recent),
        ]),
    ])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.keyboard.press("p")
    page.wait_for_selector('[data-view="live"] [data-partial]')
    page.wait_for_selector('[data-view="live"] [data-partial]', state="detached", timeout=10000)
