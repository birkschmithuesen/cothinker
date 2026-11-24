"""surfaces/contract/check.py: the `api=` query param it appends to open
the surface under test (found live while building demo/acceptance/e2e.py,
issue #153 point 6 -- a token-gated surface already carries `?t=...`, and
the check used to clobber it with a second bare `?api=`).
"""
from __future__ import annotations

from surfaces.contract.check import _with_api_param


def test_appends_api_param_with_question_mark_when_surface_has_no_query():
    url = _with_api_param("https://example.org/monitor/", "http://127.0.0.1:9000")
    assert url == "https://example.org/monitor/?api=http://127.0.0.1:9000/"


def test_appends_api_param_with_ampersand_when_surface_already_has_a_query():
    url = _with_api_param("https://example.org/monitor/?t=abc123", "http://127.0.0.1:9000")
    assert url == "https://example.org/monitor/?t=abc123&api=http://127.0.0.1:9000/"
