# CoThinker — tests of stage/static/transcript-panel.js
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Issue #181 L1: a plain node run against a mock `#transcript` element --
newest on top, 10 lines max, a tentative partial replaced on every update,
a final confirming it, and per-line opacity by age/position.

Only the pure functions (`tpMerge`, `tpRows`, `tpRenderRows`) are driven
here, the same way the issue's own scope cut puts it: the live
fetch/EventSource wiring (`tpBoot`) is markup/wiring only for this card,
not exercised end to end.

This repository's only prior JS test harness (`tests/test_stage_js.py`)
is built around the stage page's INLINE `<script>` (extracted from a
served page by regex) -- `transcript-panel.js` is a standalone static
file, so this is a new, minimal harness in the same spirit: the real
script's source, concatenated with a small driver, run under node.

    python3 -m pytest -q tests/test_transcript_panel_js.py

Skipped without node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "stage" / "static" / "transcript-panel.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")

# A mock element small and honest about what it fakes: the script only
# ever sets `.innerHTML` and reads/writes `.style.*` on the container
# (`tpBoot`'s `container.style.overflow = "hidden"`, not exercised by the
# drivers below since they call the pure functions directly).
MOCK_ELEMENT = r"""
function MockElement(){ this._html=''; this.style={}; }
MockElement.prototype = {
  get innerHTML(){ return this._html; },
  set innerHTML(v){ this._html=String(v); },
};
"""

DRIVER = r"""
var el = new MockElement();
var state = tpNewState();

function line(speaker, startedAtIso, text){
  return {speaker: speaker, started_at: startedAtIso, text: text};
}
function recent(lines){ return {kind: "transcript:recent", lines: lines}; }
function partial(turnId, speaker, text, startedAtIso){
  return {kind: "transcript_partial", turn_id: turnId, speaker: speaker,
          text: text, started_at: startedAtIso};
}

var NOW_ISO = "2026-11-24T12:00:00+00:00";
var NOW_MS = Date.parse(NOW_ISO);

var out = {};

// --- newest on top: two finished lines, newest must render first -------
tpMerge(state, recent([
  line("Mira", "2026-11-24T11:59:00+00:00", "first line"),
  line("Jonas", NOW_ISO, "second line"),
]));
tpRender(el, state, NOW_MS);
out.newestOnTopHtml = el.innerHTML;
out.newestOnTopFirstIndex = el.innerHTML.indexOf("second line");
out.newestOnTopSecondIndex = el.innerHTML.indexOf("first line");

// --- 10 lines max: 12 finished lines, only the last 10 rendered --------
var twelve = [];
for (var i = 0; i < 12; i++){
  twelve.push(line("Mira", NOW_ISO, "line " + i));
}
state = tpNewState();
tpMerge(state, recent(twelve));
var rows = tpRows(state, NOW_MS);
out.rowCountAfterTwelve = rows.length;
out.topRowAfterTwelve = rows[0].text;
out.bottomRowAfterTwelve = rows[rows.length - 1].text;

// --- tentative replaced: growing partials for one turn_id --------------
state = tpNewState();
tpMerge(state, partial("T1", "Mira", "hal", NOW_ISO));
tpRender(el, state, NOW_MS);
out.afterFirstPartial = el.innerHTML;
tpMerge(state, partial("T1", "Mira", "hallo wie", NOW_ISO));
tpRender(el, state, NOW_MS);
out.afterSecondPartial = el.innerHTML;
out.secondPartialHasOnlyOneTentativeCount =
  (el.innerHTML.match(/class="tentative"/g) || []).length;
out.rowsAfterSecondPartial = tpRows(state, NOW_MS).length;
out.secondPartialRowText = tpRows(state, NOW_MS)[0].text;

// --- final confirms: the partial clears, the finished line shows -------
tpMerge(state, recent([line("Mira", NOW_ISO, "hallo wie geht's")]));
tpMerge(state, partial("T1", "Mira", "", NOW_ISO));
tpRender(el, state, NOW_MS);
out.afterFinal = el.innerHTML;
var afterFinalRows = tpRows(state, NOW_MS);
out.afterFinalRows = afterFinalRows.length;
out.afterFinalRowConfirmed = afterFinalRows[0].confirmed;

// --- opacity by age: position fade, then a full fade-out past 120s -----
state = tpNewState();
tpMerge(state, recent([
  line("A", "2026-11-24T11:58:00+00:00", "oldest"),
  line("B", "2026-11-24T11:59:00+00:00", "middle"),
  line("C", NOW_ISO, "newest"),
]));
var ageRows = tpRows(state, NOW_MS);
out.opacities = ageRows.map(function(r){ return r.opacity; });

state = tpNewState();
var longAgoIso = "2026-11-24T11:00:00+00:00";   // 60 minutes before NOW_ISO
tpMerge(state, recent([line("A", longAgoIso, "very old")]));
out.oldLineOpacity = tpRows(state, NOW_MS)[0].opacity;

console.log(JSON.stringify(out));
"""


def _run_node(tmp_path: Path) -> dict:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    combined = script + "\n" + MOCK_ELEMENT + "\n" + DRIVER
    path = tmp_path / "run.js"
    path.write_text(combined, encoding="utf-8")
    proc = subprocess.run([shutil.which("node") or "node", str(path)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def out(tmp_path_factory) -> dict:
    return _run_node(tmp_path_factory.mktemp("transcript-panel-js"))


def test_newest_line_renders_before_the_older_one(out):
    assert out["newestOnTopFirstIndex"] != -1
    assert out["newestOnTopSecondIndex"] != -1
    assert out["newestOnTopFirstIndex"] < out["newestOnTopSecondIndex"]


def test_rows_are_capped_at_ten(out):
    assert out["rowCountAfterTwelve"] == 10


def test_the_cap_keeps_the_newest_ten_not_the_oldest(out):
    assert out["topRowAfterTwelve"] == "line 11"
    assert out["bottomRowAfterTwelve"] == "line 2"


def test_a_second_partial_replaces_the_first_rather_than_appending(out):
    assert "hal" in out["afterFirstPartial"]
    assert "hallo wie" in out["afterSecondPartial"]
    assert out["secondPartialHasOnlyOneTentativeCount"] == 1
    assert out["rowsAfterSecondPartial"] == 1
    assert out["secondPartialRowText"] == "hallo wie"


def test_the_final_clears_the_partial_and_confirms_the_line(out):
    assert "hallo wie geht's" in out["afterFinal"]
    assert out["afterFinalRows"] == 1
    assert out["afterFinalRowConfirmed"] is True


def test_opacity_falls_with_position_newest_highest(out):
    opacities = out["opacities"]
    assert len(opacities) == 3
    assert opacities[0] == 1.0
    assert opacities[0] > opacities[1] > opacities[2]
    assert all(o >= 0.25 for o in opacities)


def test_a_line_older_than_120s_fades_out_completely(out):
    assert out["oldLineOpacity"] == 0


def test_the_panel_defaults_to_the_proxied_contract_path():
    """Without ?api= the panel must read /api/snapshot + /api/events (the
    event server proxies the contract under /api/), not the site root --
    live, the root answered nothing and the panel stayed empty."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "stage" / "static" / "transcript-panel.js").read_text()
    assert 'var TP_DEFAULT_API = "/api/";' in js
    assert '.get("api") || TP_DEFAULT_API' in js
