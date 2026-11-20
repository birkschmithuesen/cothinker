#!/usr/bin/env python3
# CoThinker — floor projection: the text-free demo build for the venue technicians
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Build a static, self-contained copy of the floor projection page that the
venue technicians (Videnie) can project 1:1 to
confirm their mapping, with no CoThinker server running and no network call
after the page loads.

Unlike `build-wall-demo.py`, the floor page carries no server-side state (no
`wand.json` equivalent) -- this build is a straight copy of the static
assets (`floor.js`, `floor-config.js`, `floor-layout.js`, `floor-testgrid.js`,
`wall-theme.css`, reused as-is) plus the same `/floor/static/` -> `./` path
rewrite `build-wall-demo.py` does for `/wall/static/`. No settled-run step.

Default layout/look (card's own decision point, recorded here): the demo
defaults to `?look=ring&layout=venue-b` -- the venue's real layout-B seating
positions (`workshop/static/floor-config.js`'s `VENUE_B_POSITIONS_M`),
rendered with the existing 'ring' look (table ring + chair ring + group
number), matching the card's own acceptance wording ("8 group circles
... group number"). The DEFAULT 'spot' look draws no label and currently
forces the 4x2 grid layout for any `?layout=` (`floor.js`'s own
spot-forces-grid behaviour) -- defaulting the DEMO to the 'ring' look
instead is the smaller, fully reversible choice between the two the card
offers: it needs no change to `floor.js`'s live spot-forces-grid logic at
all, only this build's own default query string.

That default is baked into the generated `index.html` as a small inline
script, run before `floor.js` loads, that fills in `layout`/`look` ONLY
when the URL does not already set them -- `?calibrate=1` on the demo URL
therefore still shows the venue-b/ring defaults (the script merges in the
missing keys, never overwrites an explicit one). A static file server
answers the SAME `index.html` for any query string on this path, so the
plain URL is still a 200 before this script's own, second load (triggered
by `location.search = ...`) applies the defaults.

    env -u PYTHONPATH python3 scripts/build-floor-demo.py [--out var/floor-demo]

The result is a plain directory of static files, served by ANY static file
server (not a CoThinker process) -- e.g. `python3 -m http.server --directory
var/floor-demo 8000`. Chromium refuses to load an ES module (floor.js's own
`type="module"`) from a bare `file://` URL, so opening index.html directly
will NOT work; it must be served over http(s).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "workshop" / "static"

DEFAULT_OUT = REPO / "var" / "floor-demo"

STATIC_ASSETS = [
    "floor.js", "floor-config.js", "floor-layout.js", "floor-testgrid.js", "wall-theme.css",
    # fb 216: floor-config.js's personWallSM() imports REFERENCE_W from wall-config.js
    # (which itself imports wall-separators.js/motion-values.js) -- missing here 404s
    # floor.js's whole module graph in this static demo bundle, same trap as
    # FLOOR_STATIC_FILES's own.
    "wall-config.js", "wall-separators.js", "motion-values.js",
    # Known_red triage: floor.js also imports floor-markers.js
    # (drawMarkers() etc.), floor-qrcode.js, room-look-apply.js (mergeRoomLook()),
    # login-line.js and motion-tokens.js, and login-line.js itself imports
    # room-grid.js/person-symbol.js -- all missing from this list left the SAME
    # 404/module-graph trap the comment above documents (found the same way:
    # test_floor_demo_build.py's run_real_build() timing out waiting for
    # window.floorDebug, which never got defined at all).
    "floor-markers.js", "floor-qrcode.js", "room-look-apply.js", "login-line.js",
    "motion-tokens.js", "room-grid.js", "person-symbol.js",
]

# Fills in the demo's own defaults (layout=venue-b, look=ring) for whichever
# of the two keys the URL does not already set -- an explicit ?calibrate=1
# (or anything else) is left untouched and simply carried along.
DEFAULT_PARAMS_SCRIPT = """<script>
(function () {
  var sp = new URLSearchParams(location.search);
  var changed = false;
  if (!sp.has("layout")) { sp.set("layout", "venue-b"); changed = true; }
  if (!sp.has("look")) { sp.set("look", "ring"); changed = true; }
  if (changed) location.search = sp.toString();
})();
</script>
"""


def write_demo(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in STATIC_ASSETS:
        shutil.copy(STATIC / name, out / name)

    html = (STATIC / "floor.html").read_text(encoding="utf-8")
    # A plain static bundle, no /floor/static/ server root,
    # no token -- strip both, same posture as build-wall-demo.py's own
    # rewrite for /wall/static/.
    html = html.replace("/floor/static/", "./")
    html = html.replace("<title>Floor</title>", "<title>Floor demo</title>")
    html = html.replace(
        '<script type="module" src="./floor.js"></script>',
        DEFAULT_PARAMS_SCRIPT
        # Known_red triage: floor.js now polls several
        # /wall/*.json endpoints (login-line/room-look/control/room-mode state)
        # that do not exist in this static bundle -- without this flag every
        # demo build made a network request forever, same trap build-wall-demo.py's
        # own WALL_DEMO_FRAMES flag already avoids for wall.js.
        + '<script>window.FLOOR_DEMO_STATIC = true;</script>\n'
        + '<script type="module" src="./floor.js"></script>',
    )
    (out / "index.html").write_text(html, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    write_demo(args.out)

    print(f"floor demo built: {args.out}")
    print(f"serve it with: python3 -m http.server --directory {args.out} 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
