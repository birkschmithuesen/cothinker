"""Operator console commands (issue #173, spec 4.3) against the operator
fixture backend: start with project + title, stop with confirmation and the
participants warning, project switch only while stopped -- all by keyboard."""
from __future__ import annotations

import time

from surfaces.operator.fixtures import build_frames
from surfaces.operator.models import Meeting, Participant
from surfaces.operator_console.tests.helpers import TOKEN


def _open(operator, surface_url, page, frame_index=0):
    api, backend = operator([build_frames()[frame_index]])
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#start-project option", state="attached")  # an <option> is never "visible"
    return backend


def test_start_by_keyboard(operator, surface_url, page):
    backend = _open(operator, surface_url, page)
    page.focus("#start-project")
    page.select_option("#start-project", "design-retro")
    page.fill("#start-title", "Runde A")
    page.press("#start-title", "Enter")
    page.wait_for_selector('html[data-running="true"]')
    assert backend.current.session.project.id == "design-retro"
    assert backend.current.session.title == "Runde A"
    assert "Runde A" in page.locator('[data-panel="session"]').inner_text()  # Session.title (#176)
    assert page.locator("#switch").is_disabled()


def test_stop_without_participants_asks_once(operator, surface_url, page):
    backend = _open(operator, surface_url, page, frame_index=1)  # running, nobody in the meeting
    stops = []
    page.on("request", lambda r: stops.append(r) if r.url.endswith("/operator/stop") else None)
    page.wait_for_selector('html[data-running="true"]')
    page.click("#stop")
    page.keyboard.press("Escape")
    assert stops == [] and backend.current.running
    page.click("#stop")
    assert page.locator("#stop-confirm").inner_text() == "Beenden"
    page.click("#stop-confirm")
    page.wait_for_selector('html[data-running="false"]')


def test_stop_with_participants_warns_and_forces(operator, surface_url, page):
    backend = _open(operator, surface_url, page, frame_index=1)
    backend.current = backend.current.model_copy(update={
        "meeting": Meeting(participants=[Participant(name="Person A", speaking=False, level_dbfs=-40.0)], count=1),
    })
    page.wait_for_selector('[data-panel="meeting"] [data-row]')
    page.click("#stop")
    assert page.locator("#stop-warning").inner_text() == "Noch 1 Teilnehmende im Meeting."
    assert page.locator("#stop-confirm").inner_text() == "Trotzdem beenden"
    page.click("#stop-confirm")
    page.wait_for_selector('html[data-running="false"]', timeout=5000)
    assert not backend.current.running


def test_project_switch_only_while_stopped(operator, surface_url, page):
    backend = _open(operator, surface_url, page)
    page.select_option("#switch-project", "design-retro")
    page.click("#switch")
    deadline = time.monotonic() + 5
    while backend.current.session.project.id != "design-retro" and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert backend.current.session.project.id == "design-retro"


def test_project_switch_is_disabled_while_running(operator, surface_url, page):
    _open(operator, surface_url, page, frame_index=1)
    page.wait_for_selector('html[data-running="true"]')
    assert page.locator("#switch").is_disabled()
    assert page.locator("#switch-project").is_disabled()
