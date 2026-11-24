"""Operator console failure handling (final review of issue #173): failed
commands and status loads are shown, never silent; the push stream reconnects;
Stop stays disabled until the first status arrived."""
from __future__ import annotations

from surfaces.operator.fixtures import build_frames
from surfaces.operator_console.tests.helpers import TOKEN


def _goto(operator, surface_url, page, frame_index=0):
    api, backend = operator([build_frames()[frame_index]])
    page.pageerrors = []
    page.on("pageerror", lambda err: page.pageerrors.append(str(err)))
    return api, backend


def test_stop_is_disabled_before_the_first_status(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page, frame_index=1)
    page.route("**/operator/events", lambda route: route.abort())
    page.route("**/operator/status", lambda route: route.fulfill(status=401, json={"detail": "no"}))
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#connection-state:not(:empty)")
    assert page.locator("#stop").is_disabled()


def test_status_auth_failure_is_shown(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page)
    page.route("**/operator/events", lambda route: route.abort())
    page.route("**/operator/status", lambda route: route.fulfill(status=401, json={"detail": "no"}))
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#connection-state:not(:empty)")
    assert "Zugang" in page.locator("#connection-state").inner_text()
    assert page.pageerrors == []


def test_status_network_failure_is_shown(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page)
    page.route("**/operator/events", lambda route: route.abort())
    page.route("**/operator/status", lambda route: route.abort())
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#connection-state:not(:empty)")
    assert "Verbindung" in page.locator("#connection-state").inner_text()
    assert page.pageerrors == []


def test_non_json_command_failure_is_shown(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page, frame_index=1)
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector('html[data-running="true"]')
    page.route("**/operator/stop", lambda route: route.fulfill(status=502, content_type="text/html", body="<html>Bad Gateway</html>"))
    page.click("#stop")
    page.click("#stop-confirm")
    page.wait_for_selector("#command-error:not(:empty)")
    assert page.pageerrors == []


def test_validation_error_detail_is_readable(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page)
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#start-project option", state="attached")
    page.route("**/operator/start", lambda route: route.fulfill(
        status=422, json={"detail": [{"loc": ["body", "title"], "msg": "field required", "type": "missing"}]}))
    page.fill("#start-title", "Runde A")
    page.press("#start-title", "Enter")
    page.wait_for_selector("#command-error:not(:empty)")
    text = page.locator("#command-error").inner_text()
    assert "[object" not in text and "field required" in text


def test_stream_reconnects_and_clears_the_connection_state(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page)
    calls = []

    def events(route):
        calls.append(1)
        route.abort() if len(calls) == 1 else route.continue_()

    page.route("**/operator/events", events)
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#connection-state:not(:empty)")
    page.wait_for_function("document.getElementById('connection-state').textContent === ''", timeout=15000)
    assert len(calls) >= 2


def test_quiet_reconnect_clears_the_connection_state_after_status_reload(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page)
    calls = []

    def events(route):
        calls.append(1)
        if len(calls) == 1:
            route.abort()
        # later attempts are left pending: the stream never sends a frame

    page.route("**/operator/events", events)
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#connection-state:not(:empty)")
    page.wait_for_function("document.getElementById('connection-state').textContent === ''", timeout=15000)
    assert len(calls) >= 2


def test_command_success_keeps_a_stale_connection_message(operator, surface_url, page):
    api, _ = _goto(operator, surface_url, page)
    page.route("**/operator/events", lambda route: route.abort())
    page.goto(f"{surface_url}?api={api}&token={TOKEN}")
    page.wait_for_selector("#start-project option", state="attached")
    page.wait_for_selector("#connection-state:not(:empty)")
    page.fill("#start-title", "Runde A")
    page.press("#start-title", "Enter")
    page.wait_for_selector('html[data-running="true"]')
    assert page.locator("#connection-state").inner_text() != ""
