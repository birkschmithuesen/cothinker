"""Meeting chat look rules (ADR 0023, AGENTS.md rule 11): colour and size only
through `var(--ct-*)` tokens that its generated `tokens.css` defines, no hex
literal outside `tokens.css`, nothing loaded from outside its own folder."""
from __future__ import annotations

import re
from pathlib import Path

SURFACE_DIR = Path(__file__).resolve().parents[1]
VAR_USE = re.compile(r"var\((--ct-[a-z0-9-]+)")
VAR_DEF = re.compile(r"(--ct-[a-z0-9-]+)\s*:")
HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
COMMENTS = re.compile(r"<!--.*?-->|/\*.*?\*/|(?m:^\s*//.*$)", re.S)
EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""")


def _read(name: str) -> str:
    return (SURFACE_DIR / name).read_text(encoding="utf-8")


def test_every_token_style_css_uses_is_defined_in_tokens_css():
    used = set(VAR_USE.findall(_read("style.css")))
    defined = set(VAR_DEF.findall(_read("tokens.css")))
    assert used, "style.css reads no token at all"
    assert used <= defined, sorted(used - defined)


def test_no_hex_colour_outside_tokens_css():
    for name in ("style.css", "index.html", "texts.js", "app.js"):
        assert HEX.findall(COMMENTS.sub("", _read(name))) == [], name


def test_index_loads_only_its_own_files_tokens_first():
    html = _read("index.html")
    assert not EXTERNAL.search(html)
    assert html.index('href="tokens.css"') < html.index('href="style.css"')


def test_every_string_comes_from_texts_js():
    """No English literal in the markup: every label is set from TEXT."""
    html = COMMENTS.sub("", _read("index.html"))
    visible = re.sub(r"<[^>]+>", " ", html.split("<body>", 1)[1])
    assert visible.split() == []
