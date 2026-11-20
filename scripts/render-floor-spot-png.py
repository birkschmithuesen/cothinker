#!/usr/bin/env python3
# CoThinker — render /floor's default 'spot' look to a PNG
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A still of /floor against a real `workshop/wall.py make_server()` test
server, exact venue canvas size (3710 x 2015), for the operator's vault -- never
written into the repo itself.

    env -u PYTHONPATH python3 scripts/render-floor-spot-png.py --out /path/to/boden-spots.png
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from workshop import wall  # noqa: E402
from acceptance.browser import _launch_chromium  # noqa: E402

TOKEN = "floorpng"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="PNG path to write (outside the repo)")
    ap.add_argument("--query", default="", help="extra /floor query string, e.g. '&look=ring'")
    args = ap.parse_args()

    run = Path(tempfile.mkdtemp(prefix="cothinker-floor-png-"))
    srv = wall.make_server(run, "127.0.0.1", 0, TOKEN)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        ctx = browser.new_context(viewport={"width": 3710, "height": 2015})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{port}/floor?t={TOKEN}{args.query}", wait_until="load")
        page.wait_for_function(
            "window.floorDebug && window.floorDebug.positions().length > 0", timeout=8000)
        print("look:", page.evaluate("window.floorDebug.look()"))
        print("spot:", page.evaluate("window.floorDebug.spot()"))
        page.screenshot(path=args.out, clip={"x": 0, "y": 0, "width": 3710, "height": 2015})
        ctx.close()
        browser.close()

    srv.shutdown()
    srv.server_close()
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
