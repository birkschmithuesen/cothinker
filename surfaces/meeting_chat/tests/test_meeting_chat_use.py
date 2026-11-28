"""Issue #208, in the browser: the chat shows the thread with each message's
status, and typing a task then Enter posts exactly the contract's
`message` action (fixture mode answers 202 without acting)."""
from __future__ import annotations

import json
import time

from surfaces.contract.fixtures import Frame, load_scenario
from surfaces.contract.models import ChatIntent, ChatMessage, ChatSnapshot

QUESTION = ChatMessage(id="p-a-1", role="participant", name="Person A",
                       text="Compare X and Y as an interactive page on the stage.",
                       at="2026-11-27T10:00:00+01:00", status="answered")
ANSWER = ChatMessage(id="agent-p-a-1", role="agent", text="X is faster, Y is cheaper.",
                     at="2026-11-27T10:00:30+01:00", reply_to="p-a-1", status="answered")
WAITING = ChatMessage(id="p-b-1", role="participant", name="", text="Summarise the risks.",
                      at="2026-11-27T10:01:00+01:00", status="pending")


def _frames(messages):
    base = load_scenario("meeting")[0].snapshot
    snapshot = base.model_copy(update={"chat": ChatSnapshot(messages=messages)})
    return [Frame(delay_s=0.2, snapshot=snapshot,
                  view_intents=[ChatIntent(id="chat:room", messages=messages)])]


def test_the_thread_shows_names_answers_and_the_pending_status(page, contract, surface_url):
    api = contract(_frames([QUESTION, ANSWER, WAITING]))
    page.goto(f"{surface_url}?api={api}/")
    page.wait_for_selector('#thread li[data-id="p-b-1"]')

    rows = page.locator("#thread li")
    assert rows.count() == 3
    assert rows.nth(0).locator(".who").inner_text() == "Person A"
    assert rows.nth(1).locator(".who").inner_text() == "Robo"
    assert rows.nth(1).locator(".text").inner_text() == "X is faster, Y is cheaper."
    assert rows.nth(2).locator(".who").inner_text() == "Guest"
    assert rows.nth(2).locator(".status").inner_text() == "Robo is working on it…"
    assert page.console_errors == []


def test_enter_posts_the_message_action_and_clears_the_field(page, contract, surface_url):
    api = contract(_frames([]))
    page.goto(f"{surface_url}?api={api}/")
    page.fill("#message", "  Compare X and Y  ")
    with page.expect_request(lambda r: r.url.endswith("/actions") and r.method == "POST") as req:
        page.press("#message", "Enter")
    assert json.loads(req.value.post_data) == {
        "kind": "message", "intent_id": "chat:room", "text": "Compare X and Y"}
    deadline = time.monotonic() + 5.0
    while page.input_value("#message") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert page.input_value("#message") == ""


def test_a_name_is_sent_as_the_participant_name_edit(page, contract, surface_url):
    api = contract(_frames([]))
    page.goto(f"{surface_url}?api={api}/")
    page.fill("#name", "Person A")
    with page.expect_request(lambda r: r.url.endswith("/actions") and r.method == "POST") as req:
        page.click("#name-button")
    assert json.loads(req.value.post_data) == {
        "kind": "edit", "intent_id": "participant:name", "text": "Person A"}


def test_a_stored_name_is_restored_silently(page, contract, surface_url):
    api = contract(_frames([]))
    page.add_init_script('localStorage.setItem("cothinker.chat.name", "Person A")')
    with page.expect_request(lambda r: r.url.endswith("/actions") and r.method == "POST"):
        page.goto(f"{surface_url}?api={api}/")
    assert page.input_value("#name") == "Person A"
    time.sleep(0.5)
    assert page.inner_text("#notice") == ""
