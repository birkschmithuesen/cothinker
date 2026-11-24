"""ADR 0023 / issue #172: no surface stylesheet hard-codes a colour.

    python3 -m pytest -q surfaces/design/tests/test_no_hardcoded_colors.py

Scans every `*.css` file under `surfaces/` for a bare hex colour literal.
The only file allowed to declare one is the generated `tokens.css` itself
(ADR 0023's own "allowlist only for the generated file" rule) -- any
other surface stylesheet must read a colour through a `var(--ct-*)`
custom property instead. Today `surfaces/` has no other `*.css` file, so
this test is a guard rail against one being added with a hard-coded
colour, not (yet) a purge of anything existing.

Scope note: `stage/static/workshop/workshop.css` is not under `surfaces/`
and is not scanned here -- it is the pre-existing workshop skin. Its two
product-accent properties are wired to tokens (`test_workshop_tokens.py`);
its much larger, unrelated palette (job colours, status colours, etc.)
is out of this card's scope -- see the PR summary.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")

ALLOWLIST = frozenset({
    "surfaces/design/tokens.css",  # the generated file ADR 0023 exempts
})


def _hardcoded_colors(css_text: str) -> list[str]:
    hits = []
    for line in css_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("/*", "*")):
            continue
        if HEX_COLOR.search(stripped):
            hits.append(stripped)
    return hits


def find_surface_stylesheets_with_hardcoded_colors(repo: Path = REPO) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    surfaces_dir = repo / "surfaces"
    if not surfaces_dir.is_dir():
        return offenders
    for css_path in sorted(surfaces_dir.rglob("*.css")):
        rel = css_path.relative_to(repo).as_posix()
        if rel in ALLOWLIST:
            continue
        hits = _hardcoded_colors(css_path.read_text(encoding="utf-8"))
        if hits:
            offenders[rel] = hits
    return offenders


def test_no_surface_stylesheet_hardcodes_a_colour():
    offenders = find_surface_stylesheets_with_hardcoded_colors()
    assert not offenders, offenders


def test_scanner_flags_a_hardcoded_colour_outside_the_allowlist(tmp_path):
    """Mutation check: a stray hex colour in a new surface stylesheet is
    caught, proving the assertion above would actually fail, not just
    that it passes today because nothing offends yet."""
    demo_dir = tmp_path / "surfaces" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "demo.css").write_text("body { color: #112233; }\n", encoding="utf-8")

    offenders = find_surface_stylesheets_with_hardcoded_colors(repo=tmp_path)

    assert "surfaces/demo/demo.css" in offenders
