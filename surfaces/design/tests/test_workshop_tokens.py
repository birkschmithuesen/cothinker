"""ADR 0023 / issue #172: the workshop skin reads its product accent from
the design tokens.

    python3 -m pytest -q surfaces/design/tests/test_workshop_tokens.py

`stage/static/workshop/workshop.css` cannot `@import` `tokens.css` (it is
served standalone, one file at a time, and may not reach outside its own
folder -- `stage/static/workshop/README.md` rule R3). So the wiring is at
the custom-property level: `--accent`/`--accent-tint` read `var(--ct-
accent, <literal>)` -- sourced from `tokens.css` when some page also
loads it (default or instance-overridden), falling back to today's
literal otherwise.

This is the structural check the task brief asks for in place of a real
screenshot diff (no headless-browser/screenshot tool exists in this
environment): it proves the fallback is byte-identical to the generated
`tokens.css` default-theme value, which is exactly "no visible change to
the rendered look, only where the value comes from changes".
"""
from __future__ import annotations

import re
from pathlib import Path

from surfaces.design import build

REPO = Path(__file__).resolve().parents[3]
WORKSHOP_CSS = REPO / "stage" / "static" / "workshop" / "workshop.css"


def _custom_property(css_text: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*([^;]+);", css_text)
    assert match, f"--{name} not declared in {WORKSHOP_CSS}"
    return match.group(1).strip()


def test_workshop_accent_properties_read_from_the_ct_token_with_a_fallback():
    css_text = WORKSHOP_CSS.read_text(encoding="utf-8")
    accent = _custom_property(css_text, "accent")
    accent_tint = _custom_property(css_text, "accent-tint")

    assert accent.startswith("var(--ct-accent,"), accent
    assert accent_tint.startswith("var(--ct-accent-tint,"), accent_tint


def test_workshop_accent_fallback_matches_the_generated_default_theme_value():
    """The fallback literal is exactly today's rendered colour, so a page
    that does not (yet) load tokens.css renders identically to before."""
    css_text = WORKSHOP_CSS.read_text(encoding="utf-8")
    accent = _custom_property(css_text, "accent")
    accent_tint = _custom_property(css_text, "accent-tint")
    fallback_accent = accent.split(",", 1)[1].rstrip(")").strip()
    fallback_accent_tint = accent_tint.split(",", 1)[1].rstrip(")").strip()

    tokens = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    default_color = tokens["themes"]["default"]["color"]

    assert fallback_accent == default_color["accent"]
    assert fallback_accent_tint == default_color["accent-tint"]
