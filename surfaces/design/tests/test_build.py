"""ADR 0023 / issue #172: the token generator.

    python3 -m pytest -q surfaces/design/tests/test_build.py

Covers: the default tokens load and render to CSS/JSON, an instance
override wins over the default without disturbing tokens it does not
touch, and the CLI entry point (`python -m surfaces.design.build`) writes
both generated files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from surfaces.design import build

HERE = Path(__file__).resolve().parent


def test_default_tokens_file_has_every_fixed_token_name():
    tokens = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    default_color = tokens["themes"]["default"]["color"]
    display_color = tokens["themes"]["display"]["color"]

    for name in ("surface", "sheet", "ink", "muted", "rule", "grid", "signal",
                 "accent", "accent-tint", "ok", "warn", "danger"):
        assert name in default_color, name

    for name in ("surface", "sheet", "ink", "muted", "rule", "accent", "signal", "ok", "warn", "danger"):
        assert name in display_color, name

    assert default_color["accent"] == "#4A3ABF"
    assert default_color["signal"] == "#d6401c"
    assert display_color["accent"] == "#A99BF5"

    display_sizes = tokens["type"]["display-size"]
    assert display_sizes == {"brand": 56, "value": 88, "main": 64, "agenda": 44, "side": 40, "note": 28}

    assert tokens["space"] == [4, 8, 12, 16, 24, 32, 48, 64]
    assert tokens["radius"] == [4, 8, 16, 999]
    assert tokens["motion"] == [150, 300]


def test_render_css_declares_every_custom_property_with_the_ct_prefix():
    tokens = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    css = build.render_css(tokens)

    assert ":root {" in css
    assert '[data-theme="display"] {' in css
    assert "--ct-surface: #f3f1ea;" in css
    assert "--ct-accent: #4A3ABF;" in css
    assert "--ct-size-xs: 13px;" in css
    assert "--ct-display-main: 64px;" in css
    assert "--ct-space-4: 4px;" in css
    assert "--ct-space-64: 64px;" in css
    assert "--ct-radius-999: 999px;" in css
    assert "--ct-motion-150: 150ms;" in css

    # the display theme overrides only the tokens it carries
    display_block = css.split('[data-theme="display"] {', 1)[1]
    assert "--ct-surface: #12101A;" in display_block
    assert "--ct-accent: #A99BF5;" in display_block


def test_render_css_has_no_bare_hex_outside_declared_custom_properties():
    """Every hex literal in the generated CSS is the value of a `--ct-*`
    declaration or a documented font-face comment -- never a bare colour
    used directly in a rule, which is exactly what surfaces must avoid."""
    tokens = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    css = build.render_css(tokens)
    for line in css.splitlines():
        stripped = line.strip()
        if "#" not in stripped or stripped.startswith(("/*", "*")):
            continue
        assert stripped.startswith("--ct-") and stripped.endswith(";"), stripped


def test_render_css_documents_the_missing_font_binaries():
    tokens = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    css = build.render_css(tokens)
    assert "@font-face" in css
    assert "Fira Sans" in css
    assert "Fira Mono" in css
    # the gap is written down, not silently skipped
    assert "not shipped" in css.lower() or "not present" in css.lower()


def test_render_json_round_trips_the_same_values():
    tokens = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    rendered = json.loads(build.render_json(tokens))
    assert rendered == tokens


def test_deep_merge_override_wins_without_touching_other_keys():
    default = build.load_tokens(build.DEFAULT_TOKENS_PATH)
    override = {"themes": {"default": {"color": {"accent": "#112233"}}}}

    merged = build.deep_merge(default, override)

    assert merged["themes"]["default"]["color"]["accent"] == "#112233"
    # untouched sibling tokens keep their default value
    assert merged["themes"]["default"]["color"]["ink"] == default["themes"]["default"]["color"]["ink"]
    assert merged["themes"]["display"]["color"]["accent"] == default["themes"]["display"]["color"]["accent"]
    # the input dicts are not mutated
    assert "accent" not in override.get("themes", {}).get("default", {}).get("color", {}) or \
        override["themes"]["default"]["color"]["accent"] == "#112233"
    assert default["themes"]["default"]["color"]["accent"] == "#4A3ABF"


def test_resolve_tokens_merges_an_instance_override_file(tmp_path):
    instance_dir = tmp_path / "instances" / "acme" / "look"
    instance_dir.mkdir(parents=True)
    (instance_dir / "tokens.yaml").write_text(
        "themes:\n  default:\n    color:\n      accent: \"#010203\"\n", encoding="utf-8",
    )

    resolved = build.resolve_tokens(repo_root=tmp_path, instance="acme")

    assert resolved["themes"]["default"]["color"]["accent"] == "#010203"
    assert resolved["themes"]["default"]["color"]["ink"] == build.load_tokens(build.DEFAULT_TOKENS_PATH)[
        "themes"]["default"]["color"]["ink"]


def test_resolve_tokens_without_instance_returns_the_default_unmodified():
    resolved = build.resolve_tokens(repo_root=HERE, instance=None)
    assert resolved == build.load_tokens(build.DEFAULT_TOKENS_PATH)


def test_build_writes_css_and_json(tmp_path):
    css_path, json_path = build.build(out_dir=tmp_path)

    assert css_path == tmp_path / "tokens.css"
    assert json_path == tmp_path / "tokens.json"
    assert css_path.read_text(encoding="utf-8") == build.render_css(build.load_tokens(build.DEFAULT_TOKENS_PATH))
    assert json.loads(json_path.read_text(encoding="utf-8")) == build.load_tokens(build.DEFAULT_TOKENS_PATH)


def test_main_cli_writes_both_generated_files(tmp_path):
    exit_code = build.main(["--out-dir", str(tmp_path)])
    assert exit_code == 0
    assert (tmp_path / "tokens.css").exists()
    assert (tmp_path / "tokens.json").exists()


def test_main_cli_with_instance_merges_override(tmp_path):
    repo_root = tmp_path / "repo"
    instance_dir = repo_root / "instances" / "acme" / "look"
    instance_dir.mkdir(parents=True)
    (instance_dir / "tokens.yaml").write_text(
        "themes:\n  default:\n    color:\n      accent: \"#010203\"\n", encoding="utf-8",
    )
    out_dir = repo_root / "instances" / "acme" / "look"

    exit_code = build.main(["--instance", "acme", "--repo-root", str(repo_root), "--out-dir", str(out_dir)])

    assert exit_code == 0
    css = (out_dir / "tokens.css").read_text(encoding="utf-8")
    assert "--ct-accent: #010203;" in css


def test_main_cli_rejects_unknown_instance(tmp_path):
    exit_code = build.main(["--instance", "does-not-exist", "--repo-root", str(tmp_path), "--out-dir", str(tmp_path)])
    assert exit_code != 0
