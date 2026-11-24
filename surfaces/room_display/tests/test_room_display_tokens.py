"""Room display look rules (issue #173, spec section 5): colour and size only
through `var(--ct-*)` tokens that `tokens.css` defines, no hex literal outside
`tokens.css`, and nothing loaded from outside the surface's own folder."""
from __future__ import annotations

import re
from pathlib import Path

SURFACE_DIR = Path(__file__).resolve().parents[1]
VAR_USE = re.compile(r"var\((--ct-[a-z0-9-]+)")
VAR_DEF = re.compile(r"(--ct-[a-z0-9-]+)\s*:")
HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
# HTML, CSS/JS block and JS line comments: "issue #173" in a comment is an
# issue number, not a colour, so comments are stripped before the hex search.
COMMENTS = re.compile(r"<!--.*?-->|/\*.*?\*/|(?m:^\s*//.*$)", re.S)
EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""")


def _read(name: str) -> str:
    return (SURFACE_DIR / name).read_text(encoding="utf-8")


def hex_colours(text: str) -> list[str]:
    return HEX.findall(COMMENTS.sub("", text))


def test_hex_scanner_ignores_comments_but_catches_a_colour():
    """Mutation check: the scanner must not trip on an issue number in a
    comment, and must still catch a real colour literal."""
    assert hex_colours("<!-- issue #173 -->\n/* #173 */\n// issue #173\n") == []
    assert hex_colours("body { color: #112233; }") == ["#112233"]
    assert hex_colours('<p style="color:#abc">') == ["#abc"]


def test_every_token_style_css_uses_is_defined_in_tokens_css():
    used = set(VAR_USE.findall(_read("style.css")))
    defined = set(VAR_DEF.findall(_read("tokens.css")))
    assert used, "style.css reads no token at all"
    assert used <= defined, sorted(used - defined)


def test_no_hex_colour_outside_tokens_css():
    for name in ("style.css", "index.html", "texts.js", "views.js", "app.js", "switcher.js"):
        path = SURFACE_DIR / name
        if path.exists():
            assert hex_colours(path.read_text(encoding="utf-8")) == [], name


def test_index_loads_only_its_own_files_tokens_first():
    html = _read("index.html")
    assert not EXTERNAL.search(html)
    assert html.index('href="tokens.css"') < html.index('href="style.css"')
    assert 'data-theme="display"' in html
