"""ADR 0023 / issue #172: WCAG contrast of the token colour pairs.

    python3 -m pytest -q surfaces/design/tests/test_contrast.py

Implements the WCAG 2.x relative-luminance contrast formula directly (no
new dependency -- `pyyaml` is the generator's only one, see
`surfaces/design/requirements.txt`). Birk's instruction (2026-11-24) fixes
the scope: every `ink`/`muted` on `surface`/`sheet` pair must be >= 4.5:1
in the default theme, and the `display` theme's main content (`ink`) must
be >= 7:1. `signal` is deliberately not held to a flat >= 4.5:1 here: the
issue's own rule ("status colours always paired with an icon or word")
is what makes a weaker ratio on that one token acceptable -- see the
dedicated test below, which documents rather than hides the exception.
"""
from __future__ import annotations

from pathlib import Path

from surfaces.design import build

TOKENS = build.load_tokens(build.DEFAULT_TOKENS_PATH)


def _linearize(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1, l2 = relative_luminance(fg_hex), relative_luminance(bg_hex)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_ratio_matches_known_wcag_examples():
    # black on white is the formula's own ceiling: exactly 21:1.
    assert round(contrast_ratio("#000000", "#ffffff"), 2) == 21.0
    # a colour against itself is always 1:1.
    assert round(contrast_ratio("#4A3ABF", "#4A3ABF"), 2) == 1.0


def test_default_theme_ink_and_muted_on_surface_and_sheet_meet_4_5():
    color = TOKENS["themes"]["default"]["color"]
    for bg in ("surface", "sheet"):
        for fg in ("ink", "muted"):
            ratio = contrast_ratio(color[fg], color[bg])
            assert ratio >= 4.5, f"{fg} on {bg}: {ratio:.2f}:1"


def test_default_theme_status_colours_other_than_signal_meet_4_5():
    color = TOKENS["themes"]["default"]["color"]
    for bg in ("surface", "sheet"):
        for fg in ("ok", "warn", "danger"):
            ratio = contrast_ratio(color[fg], color[bg])
            assert ratio >= 4.5, f"{fg} on {bg}: {ratio:.2f}:1"


def test_default_theme_signal_is_the_documented_sub_4_5_exception():
    """`signal` (#d6401c, the website's own signal colour, fixed verbatim
    by the issue) measures below 4.5:1 as flat text on surface/sheet. This
    is accepted, not silently ignored, because the issue's own rule
    ("status colours always paired with an icon or word") means `signal`
    is never relied on as text-only. If a future change pushes this ratio
    to 4.5 or above, nothing here breaks; if it drops further, this test
    still documents why that is tolerated for this one token."""
    color = TOKENS["themes"]["default"]["color"]
    for bg in ("surface", "sheet"):
        ratio = contrast_ratio(color["signal"], color[bg])
        assert ratio >= 4.0, f"signal on {bg} dropped further than expected: {ratio:.2f}:1"


def test_display_theme_main_content_ink_meets_7_to_1():
    color = TOKENS["themes"]["display"]["color"]
    for bg in ("surface", "sheet"):
        ratio = contrast_ratio(color["ink"], color[bg])
        assert ratio >= 7.0, f"ink (main content) on {bg}: {ratio:.2f}:1"


def test_display_theme_muted_on_surface_and_sheet_meets_4_5():
    color = TOKENS["themes"]["display"]["color"]
    for bg in ("surface", "sheet"):
        ratio = contrast_ratio(color["muted"], color[bg])
        assert ratio >= 4.5, f"muted on {bg}: {ratio:.2f}:1"


def test_display_theme_status_colours_meet_4_5():
    color = TOKENS["themes"]["display"]["color"]
    for bg in ("surface", "sheet"):
        for fg in ("signal", "ok", "warn", "danger"):
            ratio = contrast_ratio(color[fg], color[bg])
            assert ratio >= 4.5, f"{fg} on {bg}: {ratio:.2f}:1"
