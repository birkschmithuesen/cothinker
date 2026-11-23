# CoThinker — design reference-image gate: single-state render command (issue #61)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Render one surface state to a PNG via headless Chromium, for the
reviewer to inspect or to commit as a new reference image under a
Design-Signoff. Run by the reviewer, never by the test suite -- Chromium
automation is not a requirement/requirements-dev.txt dependency of
tests/test_reference_diff.py or tests/test_design_reference_guard.py.

    python3 -m tools.design_gate.render <url> <out.png> [--width W] [--height H]
"""
from __future__ import annotations

import argparse
from pathlib import Path


def render_state(url: str, out_path: Path, *, width: int = 1280, height: int = 800) -> Path:
    from playwright.sync_api import sync_playwright  # lazy: a reviewer-only dependency

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url)
        page.screenshot(path=str(out_path))
        browser.close()
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL of the rendered state, e.g. a local file:// URL")
    parser.add_argument("out", type=Path, help="PNG path to write")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args(argv)
    render_state(args.url, args.out, width=args.width, height=args.height)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
