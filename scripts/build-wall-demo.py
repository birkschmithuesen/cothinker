#!/usr/bin/env python3
# CoThinker — 360° wall: the text-free demo build for the venue technicians
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Build a static, self-contained copy of the 360° wall page (MUST 4/5)
that the venue technicians can project 1:1 to test their mapping,
with no CoThinker server running and no network call after the page loads.

Data source: `tests/fixtures/wall/preview10` -- already 8 tables x 5 people
of plausible, invented English content (checked below); this script runs
`workshop/wall.py --once` on it once to get the settled full state (ring at
rest, every term placed, every quote released), then slices that single
state into a growth timeline -- the own/shared terms revealed in the order
they were "said" (`added_at`), quotes in the order they were released --
and bundles it with wall.js and its own static assets so the page needs
nothing but a plain static file server.

    env -u PYTHONPATH python3 scripts/build-wall-demo.py [--out var/wall-demo] [--frames 14]

The result is a plain directory of static files, served by ANY static file
server (not a CoThinker process) -- e.g. `python3 -m http.server --directory
var/wall-demo 8000`. Chromium refuses to load an ES module (wall.js's own
`type="module"`) from a bare `file://` URL, so opening index.html directly
will NOT work; it must be served over http(s).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "workshop" / "static"
PREVIEW10 = REPO / "tests" / "fixtures" / "wall" / "preview10"
WALL_PY = REPO / "workshop" / "wall.py"

# The decision this card records: 8 tables a 5 people, taken straight from
# preview10 (already exactly that shape for tables 01-08, checked below) --
# the only "adjustment" is dropping tables 09/10.
DEMO_TABLES = [f"{i:02d}" for i in range(1, 9)]

DEFAULT_FRAMES = 14
DEFAULT_OUT = REPO / "var" / "wall-demo"

STATIC_ASSETS = [
    "wall.js", "wall-config.js", "wall-separators.js", "wall-repel.js",
    # MUST 5: wall.js imports wall-hint-fit.js for the hint line's
    # font-shrink math -- missing from this list would 404 the module graph, same
    # failure mode the motion-values.js comment just below already documents.
    "wall-hint-fit.js",
    "wall-theme.js", "wall-theme.css", "wall-testgrid.js", "room-grid.js",
    # W9 motion/colour token library, cherry-picked: wall.js/
    # wall-config.js import motion-values.js; wall.html links motion-tokens.css.
    # Missing from this list left the demo bundle's module graph 404ing on
    # these (found the hard way -- tests/test_wall_demo_build.py's own
    # run_real_build() timed out waiting for cytoscape nodes that never
    # rendered because wall-config.js's import never resolved).
    "motion-values.js", "motion-tokens.css", "motion-tokens.js", "motion-patterns.js",
    # wall.js now also imports login-line.js -- same "missing
    # from this list 404s the module graph" trap the comment above already
    # names for motion-values.js.
    "login-line.js",
    # wall.js now also imports wall-wifi.js (the Wi-Fi join
    # QR's URI builder) and floor-qrcode.js (its QR encoder, reused unchanged
    # per that earlier change's own MUST 3) -- same "missing from this list 404s the
    # module graph" trap as every entry above.
    "wall-wifi.js", "floor-qrcode.js",
    # login-line.js now also imports floor-config.js (the
    # group's OWN wall edge, ROOM_UNFOLD/PX_PER_M) and its own import
    # floor-layout.js -- same 404 trap, one level deeper.
    "floor-config.js", "floor-layout.js",
    # Wall master timer: wall.js also imports wall-hint-timer.js
    # (effectiveWallHint()) and wall.html links timer.js -- same trap, found the
    # same way (test_wall_demo_build.py's run_real_build() timing out waiting
    # for cytoscape nodes that never rendered).
    "wall-hint-timer.js", "timer.js",
    # Known_red triage: wall.js also imports person-symbol.js
    # (the person-icon renderer), room-config.js (room-wide wall/offset math,
    # itself pulling in room-grid.js, already listed above), room-look-apply.js
    # (mergeRoomLook()) and wall-sky.js/wall-legend.js -- same 404 trap as every
    # entry above, found the same way (run_real_build() timing out waiting for
    # cytoscape nodes that never rendered because window.wallDebug never got
    # defined at all).
    "person-symbol.js", "room-config.js", "room-look-apply.js", "wall-sky.js",
    "wall-legend.js",
]
# wall.html's own unconditional <script> tags for the W6 feedback-step fcose
# layout (see frontend/static/vendor/README.md's load order) --
# vendored under stage/static/vendor/, same copy cytoscape.min.js already
# comes from below. Missing any one of these is the same 404/module-graph trap.
VENDOR_ASSETS = ["layout-base.js", "cose-base.js", "cytoscape-fcose.js", "cytoscape-layout-utilities.js"]


def build_settled_wand(tmp: Path) -> dict:
    """Copies the 8-table slice of preview10 into `tmp` and runs
    `wall.py --once` on it; returns the settled wand.json as a dict."""
    run = tmp / "run"
    run.mkdir(parents=True)
    for nn in DEMO_TABLES:
        src = PREVIEW10 / f"tisch-{nn}"
        people = json.loads((src / "personen.json").read_text(encoding="utf-8"))
        if len(people) != 5:
            raise SystemExit(f"preview10 tisch-{nn}: expected exactly 5 people, got {len(people)}")
        shutil.copytree(src, run / f"tisch-{nn}")
    sim_path = tmp / "sim.json"
    shutil.copy(PREVIEW10 / "sim.json", sim_path)

    r = subprocess.run([sys.executable, str(WALL_PY), "--run", str(run), "--once",
                        "--embed", "fake", "--themes", "off", "--fake-similarity", str(sim_path), "--merge-cos", "0.86"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"wall.py --once failed:\n{r.stderr}")
    return json.loads((run / "wand.json").read_text(encoding="utf-8"))


def build_frames(wand: dict, n_frames: int) -> list[dict]:
    """Slices one settled wand.json into `n_frames` growth snapshots: frame 0
    is the start image (groups only), the last frame is the full state, every
    frame in between reveals a growing prefix of terms (ordered by
    `added_at`, the moment a term qualified) and quotes (by `released_at`).
    Groups never move -- the ring is already at rest."""
    groups = wand["groups"]
    shown_terms = [t for t in wand["terms"] if t["slot"] is not None or t["lane"] is not None]
    shown_terms.sort(key=lambda t: (t["added_at"], t["id"]))
    quotes = sorted(wand.get("quotes", []), key=lambda q: q["released_at"])
    params = wand.get("params", {})

    frames = []
    for k in range(n_frames):
        frac = k / (n_frames - 1) if n_frames > 1 else 1.0
        n_terms = round(frac * len(shown_terms))
        n_quotes = round(frac * len(quotes))
        frames.append({
            "seq": k + 1,
            "groups": groups,
            "terms": shown_terms[:n_terms],
            "quotes": quotes[:n_quotes],
            "params": params,
        })
    return frames


def write_demo(frames: list[dict], out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in STATIC_ASSETS:
        shutil.copy(STATIC / name, out / name)
    for name in VENDOR_ASSETS:
        shutil.copy(REPO / "stage" / "static" / "vendor" / name, out / name)
    shutil.copy(REPO / "stage" / "static" / "vendor" / "cytoscape.min.js", out / "cytoscape.min.js")

    from workshop import languages  # noqa: E402  (REPO already needed on sys.path by caller)
    shutil.copy(languages.strings_path(languages.DEFAULT), out / "strings.js")

    (out / "demo-frames.js").write_text(
        "window.WALL_DEMO_FRAMES = " + json.dumps(frames, ensure_ascii=False) + ";\n", encoding="utf-8")

    html = (STATIC / "wall.html").read_text(encoding="utf-8")
    # Card MUST 4/5: a plain static bundle, no /wall/static/ server root, no
    # title/debug text -- strip both. The classic (non-module) demo-frames.js
    # script tag must come BEFORE wall.js's `type="module"` tag: a module
    # script is deferred to run after the document's classic scripts, so this
    # order is what guarantees window.WALL_DEMO_FRAMES exists in time.
    html = html.replace("/wall/static/", "./")
    html = html.replace("<title>Wand</title>", "<title>Wall demo</title>")
    html = html.replace('lang="de"', 'lang="en"')
    html = html.replace(
        '<script type="module" src="./wall.js"></script>',
        '<script src="./demo-frames.js"></script>\n'
        '<script type="module" src="./wall.js"></script>\n'
        '<script type="module" src="./wall-testgrid.js"></script>',
    )
    (out / "index.html").write_text(html, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO))
    tmp = Path(tempfile.mkdtemp(prefix="cothinker-wall-demo-build-"))
    try:
        wand = build_settled_wand(tmp)
        frames = build_frames(wand, args.frames)
        write_demo(frames, args.out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"wall demo built: {args.out} ({args.frames} frames, "
          f"{len(frames[-1]['terms'])} terms, {len(frames[-1]['quotes'])} quotes at the full state)")
    print(f"serve it with: python3 -m http.server --directory {args.out} 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
