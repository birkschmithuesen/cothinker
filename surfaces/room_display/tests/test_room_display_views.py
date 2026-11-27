"""Each room display view with data (issue #173, spec 3.5-3.6), plus the
derived CoThinker state (spec 3.4). Also produces the per-view PR screenshots:
COTHINKER_SHOT_DIR=pr-shots python -m pytest -q surfaces/room_display/tests/test_room_display_views.py"""
from __future__ import annotations

from surfaces.contract.fixtures import Frame, load_scenario
from surfaces.contract.models import (
    ListenSnapshot, PendingAnnouncement, ShareRow, ShareSnapshot, StatusIntent, VoiceIntent,
    VoiceSnapshot,
)
from surfaces.room_display.tests.helpers import VIEW_KEYS, empty_snapshot, open_view

EXPECTED_TEXT = {
    "live": "migration runbook",
    "topics": "Decisions & risks",
    "results": "We lock the vendor timeline as proposed.",
    "queue": "Tamar",
    "share": "Person A",
    "memory": "Vendor timeline",
}


def populated_snapshot():
    """The meeting fixture mid-session (frame 5: queue, voice, decision, open
    question, knowledge hits) plus the closing frame's tasks, plus test-local
    talk share (the fixture has none, spec F-4)."""
    frames = load_scenario("meeting")
    return frames[5].snapshot.model_copy(update={
        "tasks": frames[7].snapshot.tasks,
        "share": ShareSnapshot(total_words=200, shares=[
            ShareRow(person="P1", given="Person A", words=120, share=0.6),
            ShareRow(person="P2", given="Person B", words=80, share=0.4),
        ]),
    })


def test_every_view_shows_its_data(contract, surface_url, page, shot_dir):
    api = contract([Frame(delay_s=0.0, snapshot=populated_snapshot(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    assert "Decisions & risks" in page.locator("#left-agenda").inner_text()
    assert "Tamar" in page.locator("#left-queue").inner_text()
    assert page.locator("#left-speaker .speaker").inner_text() == "Tamar"  # voice.speaking, frame 5
    for key in VIEW_KEYS:
        open_view(page, key)
        view = page.locator(f'[data-view="{key}"]')
        assert EXPECTED_TEXT[key] in view.inner_text(), key
        assert view.locator("[data-row]").count() > 0, key
        assert not page.locator(f'[data-empty="{key}"]').is_visible(), key
        page.screenshot(path=str(shot_dir / f"view-{key}.png"))
    results = page.locator('[data-view="results"]').inner_text()
    assert "Who owns the migration runbook?" in results and "Jonas" in results and "2026-12-01" in results
    assert page.console_errors == []


def test_voice_speaking_shows_when_the_snapshot_carries_it(contract, surface_url, page):
    snapshot = empty_snapshot().model_copy(update={"voice": VoiceSnapshot(speaking="Person A")})
    api = contract([Frame(delay_s=0.0, snapshot=snapshot, view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    assert page.locator("#left-speaker .speaker").inner_text() == "Person A"
    assert not page.locator('[data-empty="left-speaker"]').is_visible()


def test_voice_present_but_nobody_speaking(contract, surface_url, page):
    snapshot = empty_snapshot().model_copy(update={"voice": VoiceSnapshot()})
    api = contract([Frame(delay_s=0.0, snapshot=snapshot, view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    assert page.locator('[data-empty="left-speaker"]').inner_text() == "Nobody is speaking right now."


def test_voice_intent_alone_updates_who_speaks_and_marks_it_fresh(contract, surface_url, page):
    """A `voice` intent renders by itself (spec 3.6): once the page is up,
    every further `/snapshot` re-fetch is aborted, so only the intent can put
    the name on screen. It also marks "Speaking now" as just confirmed."""
    voiced = empty_snapshot().model_copy(update={"voice": VoiceSnapshot(speaking="Person B")})
    api = contract([
        Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[]),
        Frame(delay_s=3.0, snapshot=voiced, view_intents=[VoiceIntent(id="voice:room", speaking="Person B")]),
    ])
    block = {"refetch": False}

    def load_only(route):
        if block["refetch"]:
            route.abort()
        else:
            route.continue_()

    page.route("**/snapshot", load_only)
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    block["refetch"] = True  # from here on, only the intent can change the speaker
    assert page.locator('[data-empty="left-speaker"]').inner_text() == "Speaker detection not active."
    page.wait_for_selector("#left-speaker[data-fresh]", state="attached", timeout=10000)
    assert page.locator("#left-speaker .speaker").inner_text(timeout=5000) == "Person B"


def test_pending_announcement_means_working(contract, surface_url, page):
    busy = empty_snapshot().model_copy(update={"pending": [
        PendingAnnouncement(id="P1", kind="decision", label="notiere", since="2026-01-01T00:00:00+00:00"),
    ]})
    api = contract([Frame(delay_s=0.0, snapshot=busy, view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="working"]')
    assert page.locator("#state").inner_text() == "Robo is working … notiere"


def test_unreachable_contract_means_offline(surface_url, page):
    page.goto(f"{surface_url}?api=http://127.0.0.1:9/")
    page.wait_for_selector('html[data-state="offline"]')
    assert page.locator("#state").inner_text() == "offline"


def test_idle_state_shows_robo_alone(contract, surface_url, page):
    api = contract([Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    assert page.locator("#state").inner_text() == "Robo"


def test_open_call_window_means_listening_with_remaining_seconds(contract, surface_url, page):
    snapshot = empty_snapshot().model_copy(update={
        "listen": ListenSnapshot(state="listening", opened_by="voice", closes_in_s=95.0),
    })
    api = contract([Frame(delay_s=0.0, snapshot=snapshot, view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="listening"]')
    assert page.locator("#state").inner_text() == "Robo listens 1:35"


def test_call_window_without_closes_in_s_omits_the_timer(contract, surface_url, page):
    snapshot = empty_snapshot().model_copy(update={
        "listen": ListenSnapshot(state="listening", opened_by="tap"),
    })
    api = contract([Frame(delay_s=0.0, snapshot=snapshot, view_intents=[])])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="listening"]')
    assert page.locator("#state").inner_text() == "Robo listens"


def test_call_answer_in_snapshot_shows_answer_ready_and_beats_working(contract, surface_url, page):
    """Priority rule (issue #206): an answer that just landed must not be
    hidden by a new pending announcement starting at the same moment.

    `call_answer` has no owning view-intent kind of its own
    (`surfaces/contract/live_player.py` `_OWNED_INTENT`), so a change to it
    reaches the surface as a generic `StatusIntent` that only tells the
    client to refetch `/snapshot` -- the answer text itself travels in the
    snapshot's `call_answer` field, read directly (`stage/stage.py:1356`
    does the same for the fixed stage display)."""
    busy = empty_snapshot().model_copy(update={
        "pending": [PendingAnnouncement(id="P1", kind="decision", label="notiere", since="2026-01-01T00:00:00+00:00")],
        "call_answer": "The budget is approved.",
    })
    api = contract([
        Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[]),
        Frame(delay_s=2.0, snapshot=busy, view_intents=[
            StatusIntent(id="s1", text="stage state changed: call_answer, pending"),
        ]),
    ])
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.wait_for_selector('html[data-state="answer"]', timeout=10000)
    assert page.locator("#state").inner_text() == "Answer ready"
    assert page.locator("#answer").inner_text() == "The budget is approved."


def test_call_answer_expires_after_300_seconds(contract, surface_url, page):
    """The fixed answer window is `CALL_ANSWER_VISIBLE_S` (300 s, mirroring
    `gateway/call_lane.py:161`) measured from the moment the room display
    first observes the new `call_answer` text, not from a server timestamp
    (the snapshot carries none)."""
    answered = empty_snapshot().model_copy(update={"call_answer": "The budget is approved."})
    api = contract([
        Frame(delay_s=0.0, snapshot=empty_snapshot(), view_intents=[]),
        Frame(delay_s=2.0, snapshot=answered, view_intents=[
            StatusIntent(id="s1", text="stage state changed: call_answer"),
        ]),
    ])
    page.clock.install()
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('html[data-state="idle"]')
    page.clock.pause_at(page.evaluate("Date.now()") + 1)
    page.wait_for_selector('html[data-state="answer"]', timeout=10000)
    page.clock.fast_forward(299000)
    assert page.locator("html").get_attribute("data-state") == "answer"
    page.clock.fast_forward(2000)
    assert page.locator("html").get_attribute("data-state") == "idle"
