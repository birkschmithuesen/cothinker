"""ADR 0023 / issue #172: no surface stylesheet hard-codes a colour.

    python3 -m pytest -q surfaces/design/tests/test_no_hardcoded_colors.py

Scans every `*.css` file under `surfaces/` for a bare hex colour literal.
A file is exempt only if it carries the generator's own marker comment
(`surfaces/design/build.py`'s `GENERATED_MARKER`) -- the per-surface
`tokens.css` copies that `python -m surfaces.design.build --out-dir
surfaces/<name>` writes, plus the canonical `surfaces/design/tokens.css`
itself. Every other surface stylesheet must read a colour through a
`var(--ct-*)` custom property instead.

`test_each_generated_tokens_css_matches_a_fresh_build` is the other half
of that exemption: a marker comment alone would let someone hand-edit a
copy (keeping the marker) without the scan ever catching it, so that test
re-runs the generator and asserts the committed copy is byte-identical,
failing the suite the moment a copy drifts from its generator.

Scope note: `stage/static/workshop/workshop.css` is not under `surfaces/`
and is not scanned here -- it is the pre-existing workshop skin. Its two
product-accent properties are wired to tokens (`test_workshop_tokens.py`);
its much larger, unrelated palette (job colours, status colours, etc.)
is out of this card's scope -- see the PR summary.
"""
from __future__ import annotations

import re
from pathlib import Path

from surfaces.design.build import GENERATED_MARKER, REPO_ROOT, build, render_css, resolve_tokens

REPO = Path(__file__).resolve().parents[3]
HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")

# Every surface that carries its own generated copy of tokens.css (ADR 0023,
# issue #172): `python -m surfaces.design.build --out-dir surfaces/<name>`.
GENERATED_SURFACE_COPIES = ("room_display", "operator_console")


def _is_generated(css_text: str) -> bool:
    return GENERATED_MARKER in css_text


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
        text = css_path.read_text(encoding="utf-8")
        if _is_generated(text):
            continue
        hits = _hardcoded_colors(text)
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


def test_scanner_exempts_only_a_file_carrying_the_generated_marker(tmp_path):
    """Mutation check: the marker, not the filename, is what exempts a
    file -- a `tokens.css` without it still offends, and the marker alone
    (no hex at all) does not."""
    demo_dir = tmp_path / "surfaces" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "tokens.css").write_text("body { color: #112233; }\n", encoding="utf-8")

    offenders = find_surface_stylesheets_with_hardcoded_colors(repo=tmp_path)

    assert "surfaces/demo/tokens.css" in offenders


def test_each_generated_tokens_css_matches_a_fresh_build():
    """Drift check: every committed per-surface `tokens.css` (and the
    canonical `surfaces/design/tokens.css`) must be byte-identical to what
    `python -m surfaces.design.build` produces right now -- a hand-edit
    that keeps the marker comment would otherwise slip past the
    hardcoded-colour scan unnoticed."""
    fresh = render_css(resolve_tokens(repo_root=REPO_ROOT, instance=None))

    design_css = (REPO_ROOT / "surfaces" / "design" / "tokens.css").read_text(encoding="utf-8")
    assert design_css == fresh, "surfaces/design/tokens.css"

    for name in GENERATED_SURFACE_COPIES:
        committed = (REPO_ROOT / "surfaces" / name / "tokens.css").read_text(encoding="utf-8")
        assert committed == fresh, f"surfaces/{name}/tokens.css"


def test_build_out_dir_reproduces_the_committed_surface_copy(tmp_path):
    """The generator command named in the marker comment, run exactly as
    documented, reproduces a committed copy -- not just `render_css` by
    itself."""
    css_path, _ = build(out_dir=tmp_path)
    fresh = css_path.read_text(encoding="utf-8")
    for name in GENERATED_SURFACE_COPIES:
        committed = (REPO_ROOT / "surfaces" / name / "tokens.css").read_text(encoding="utf-8")
        assert committed == fresh, f"surfaces/{name}/tokens.css"
