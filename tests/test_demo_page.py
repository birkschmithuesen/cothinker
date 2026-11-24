from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "demo" / "page"


def test_call_only_page_has_jitsi_but_no_stage():
    html = (PAGE / "index.html").read_text()
    assert 'id="jitsi"' in html
    assert 'id="stage"' not in html
    assert "api/session" in html


def test_split_page_has_jitsi_and_stage():
    html = (PAGE / "split.html").read_text()
    assert 'id="jitsi"' in html
    assert 'id="stage"' in html
    assert "api/session" in html


def test_both_pages_share_the_name_cookie_at_root_path():
    for name in ("index.html", "split.html"):
        html = (PAGE / name).read_text()
        assert "ct_demo_name" in html
        assert "path=/;" in html


def test_split_page_addresses_the_shared_session_endpoint_absolutely():
    # split.html is served at /meeting-split/ but the one session endpoint
    # (ADR 0021) lives at /meeting/api/session; a relative "api/session"
    # would resolve under /meeting-split/ instead and 404.
    html = (PAGE / "split.html").read_text()
    assert "/meeting/api/session" in html


def test_split_page_resolves_stage_path_against_meeting_prefix():
    html = (PAGE / "split.html").read_text()
    assert "'/meeting/' + s.stage_path" in html
