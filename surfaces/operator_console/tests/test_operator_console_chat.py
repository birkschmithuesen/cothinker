"""Issue #208: the operator console shows the meeting chat only for a
same-origin `?chat=` URL, embedded as the participant contract's own page;
without it, or for a foreign origin, the panel stays hidden and nothing is
requested from that origin."""
from __future__ import annotations

from urllib.parse import urlparse

from surfaces.operator.fixtures import OperatorFrame
from surfaces.operator_console.tests.helpers import TOKEN, empty_status


def _open(page, operator, surface_url, chat=None):
    api, _ = operator([OperatorFrame(delay_s=0.0, status=empty_status())], projects=())
    query = f"?api={api}&token={TOKEN}" + (f"&chat={chat}" if chat is not None else "")
    requested: list[str] = []
    page.on("request", lambda r: requested.append(r.url))
    page.goto(f"{surface_url}{query}")
    page.wait_for_selector("#updates > div")
    return requested


def test_without_chat_the_panel_is_hidden(operator, surface_url, page):
    _open(page, operator, surface_url)
    assert page.locator('[data-panel="chat"]').is_hidden()
    assert page.get_attribute("#chat-frame", "src") is None


def test_a_same_origin_chat_url_is_embedded(operator, surface_url, page):
    _open(page, operator, surface_url, chat="favicon.ico")
    panel = page.locator('[data-panel="chat"]')
    assert panel.is_visible()
    assert panel.locator("h2").inner_text() == "Chat"
    src = page.get_attribute("#chat-frame", "src")
    assert src.startswith(surface_url.rsplit("/", 1)[0]) and src.endswith("/favicon.ico")
    assert page.get_attribute("#chat-frame", "title") == "Meeting chat"


def test_a_foreign_chat_url_is_not_embedded(operator, surface_url, page):
    requested = _open(page, operator, surface_url, chat="https://example.invalid/chat/")
    assert page.locator('[data-panel="chat"]').is_hidden()
    assert page.get_attribute("#chat-frame", "src") is None
    assert not any(urlparse(url).hostname == "example.invalid" for url in requested)
