# CoThinker — tests of the stage page's script
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The stage page's JavaScript, actually executed.

Every other stage test reads the page's script as TEXT: it asserts that a
handler is wired, not that pressing the key does the right thing. For the
history that is not enough. The pointer lives in the browser on purpose, so
the one rule that matters most -- a new surface arriving while somebody is
paged back must NOT jump them forward -- exists ONLY in the script. A test
that greps for `ArrowLeft` would pass on a script that pages the wrong way.

So: the real page is fetched from a real `stage.py`, its script is run
under node against a small DOM stand-in, and the history is paged with the
arrow keys, the buttons and the return link.

The stand-in fakes the DOM, not the script: the script is taken from the
served page and only its closing line gains one export, so the harness can
call the polling function instead of waiting for a timer.

    python3 -m pytest -q tests/test_stage_js.py

Skipped without node. Each scenario (one real `stage.py`, one node run)
is set up once by a module-scoped fixture; the checks are the tests.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))
from stage_state import StageState, TOPIC_MIN_HOLD_S  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")

# `stage.py` requires a token on every request. A fixed test value, passed
# to every subprocess below and appended to every fetch, so this file keeps
# exercising the real page/script instead of the token check -- that check
# has its own coverage in the stage tests.
TEST_TOKEN = "js-test-fixed-token"

HARNESS = r"""
// --- a DOM small enough to be honest about what it fakes -----------------
function El(id){ this.id=id; this._html=''; this._text=''; this.classList=new CL(this);
  this.attrs={}; }
El.prototype = {
  get innerHTML(){ return this._html; },
  set innerHTML(v){ this._html=String(v); },
  get textContent(){ return this._text; },
  set textContent(v){ this._text=String(v); },
  getAttribute:function(k){ return this.attrs[k]===undefined?null:this.attrs[k]; },
  setAttribute:function(k,v){ this.attrs[k]=v; },
  hasAttribute:function(k){ return this.attrs[k]!==undefined; },
  toggleAttribute:function(k,on){
    if(on) this.attrs[k]=''; else delete this.attrs[k];
    return on;
  },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  get offsetWidth(){ return 1; }
};
function CL(el){ this.el=el; this.set={}; }
CL.prototype = {
  add:function(c){ this.set[c]=true; },
  remove:function(c){ delete this.set[c]; },
  contains:function(c){ return !!this.set[c]; },
  toggle:function(c,on){ if(on) this.add(c); else this.remove(c); }
};

var els={};
function el(id){ if(!els[id]) els[id]=new El(id); return els[id]; }
['verlauf','content','collapsed','links','punkt','clock','warn','ops-text',
 'mirror','decisions','notices','headline',
 'protokoll-col','protokoll-content','protokoll-toggle'].forEach(el);

var listeners={};
global.document = {
  getElementById:function(id){ return els[id]||null; },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  addEventListener:function(t,fn){ (listeners[t]=listeners[t]||[]).push(fn); },
  activeElement:null, hidden:false, body:{scrollHeight:1000}
};
global.window = {
  scrollY:0, innerHeight:800,
  getSelection:function(){ return {isCollapsed:true}; },
  scrollTo:function(){},
  // `origin` matters for the link-token handler's same-origin check below --
  // a fake anchor's absolute href is built against this same value.
  location:{search:'', origin:'http://buehne.example'}
};
// The page reads the bare global `location`, as it does in a browser.
global.location = global.window.location;
global.setInterval=function(){ return 0; };
global.setTimeout=function(){ return 0; };

// fetch hands out the prepared payloads in order, so the second poll is
// the one that carries the newly arrived surface. Every call's URL is
// recorded so the harness can check the token rode along.
var payloads = JSON.parse(process.argv[2]);
var shot = 0;
var fetchCalls = [];
global.fetch = async function(u){
  fetchCalls.push(u);
  var p = payloads[Math.min(shot, payloads.length-1)];
  shot++;
  return { json: async function(){ return p; } };
};

%SCRIPT%

// --- drive it ------------------------------------------------------------
function fire(type, ev){ (listeners[type]||[]).forEach(function(fn){ fn(ev); }); }
function key(k){ fire('keydown', {key:k, preventDefault:function(){},
  metaKey:false, ctrlKey:false, altKey:false, shiftKey:false}); }
function clickTarget(v){
  fire('click', {target:{closest:function(sel){
    return sel==='[data-v]' ? {getAttribute:function(){ return v; }} : null; }},
    preventDefault:function(){}});
}
// A driver for the link-token handler: a fake anchor with a `href`
// PROPERTY (already-resolved, as a real anchor's would be under <base>),
// a getAttribute/setAttribute pair and no `data-v` unless requested.
// `opts.absHref` stands in for what <base> would resolve `href` to;
// defaults to `href` itself when the caller doesn't care.
function clickLink(href, opts){
  opts = opts || {};
  var attrs = {href: href};
  if(opts.dataV !== undefined) attrs['data-v'] = opts.dataV;
  var a = {
    href: opts.absHref !== undefined ? opts.absHref : href,
    getAttribute: function(k){ return attrs[k]===undefined ? null : attrs[k]; },
    setAttribute: function(k, v){ attrs[k] = v; if(k==='href') a.href = v; }
  };
  var prevented = false;
  fire('click', {
    target: {closest: function(sel){ return sel==='a[href]' ? a : null; }},
    metaKey: !!opts.metaKey, ctrlKey: !!opts.ctrlKey,
    shiftKey: !!opts.shiftKey, altKey: !!opts.altKey,
    preventDefault: function(){ prevented = true; }
  });
  return {anchor: a, prevented: prevented};
}
// The one real element that matches BOTH click-delegation selectors
// at once. The "zurück zur aktuellen Ansicht" return link is literally
// `<a href="#" data-v="live">`, which in a browser matches `[data-v]`
// (the history handler) AND `a[href]` (the link-token handler) from the
// very same click -- `clickTarget` and `clickLink` above only ever fake
// ONE of the two selectors matching, never both on the same object, so
// neither drives this element the way a real click actually would.
//
// `absHref` mirrors the parameter of the same name on `clickLink()`
// above -- the link handler resolves via the href PROPERTY (already
// absolute, as a real anchor's would be under <base href>), not the raw
// attribute. Without a resolvable absolute href, `new URL(a.href)` throws
// and the handler bails out on that alone, leaving the href unchanged for
// a reason that has nothing to do with the data-v guard under test.
function clickBoth(href, dataV, absHref){
  var attrs = {href: href, 'data-v': dataV};
  var a = {
    href: absHref !== undefined ? absHref : href,
    getAttribute: function(k){ return attrs[k]===undefined ? null : attrs[k]; },
    setAttribute: function(k, v){ attrs[k] = v; if(k==='href') a.href = v; }
  };
  var prevented = false;
  fire('click', {
    target: {closest: function(sel){
      return (sel==='[data-v]' || sel==='a[href]') ? a : null; }},
    metaKey:false, ctrlKey:false, shiftKey:false, altKey:false,
    preventDefault: function(){ prevented = true; }
  });
  return {anchor: a, prevented: prevented};
}
var out={steps:[]};
function snap(label){
  out.steps.push({step:label, verlauf:el('verlauf').innerHTML,
                  content:el('content').innerHTML,
                  zurueck:el('verlauf').classList.contains('zurueck')});
}
process.on('uncaughtException', function(e){
  console.error('UNCAUGHT', (e && e.stack) || e); process.exit(3); });

(async function(){
  await tickOnce();                        // first poll: three surfaces
  snap('start');
  key('ArrowLeft');  snap('links');
  key('ArrowLeft');  snap('links2');
  await tickOnce();                        // a fourth surface arrives
  snap('neuer-stand');
  clickTarget('vor');  snap('vor');
  clickTarget('live'); snap('live');

  // --- the token rides the poll -----------------------------------------
  out.fetchUrls = fetchCalls;

  // --- the token rides plain links -----------------------------------
  var normal = clickLink('file/foo', {absHref:'http://buehne.example/file/foo'});
  out.linkNormal = normal.anchor.href;
  out.linkNormalPrevented = normal.prevented;

  var dataV = clickLink('file/dv', {dataV:'live',
    absHref:'http://buehne.example/file/dv'});
  out.linkDataV = dataV.anchor.getAttribute('href');

  var hashHref = clickLink('#', {absHref:'http://buehne.example/#'});
  out.linkHash = hashHref.anchor.getAttribute('href');

  var already = clickLink('file/bar?t=already',
    {absHref:'http://buehne.example/file/bar?t=already'});
  out.linkAlready = already.anchor.href;

  var crossOrigin = clickLink('http://other.example/file/x',
    {absHref:'http://other.example/file/x'});
  out.linkCrossOrigin = crossOrigin.anchor.href;

  var modified = clickLink('file/mod',
    {absHref:'http://buehne.example/file/mod', ctrlKey:true});
  out.linkModifier = modified.anchor.href;

  // data-v is tested for PRESENCE now, not truthiness -- an anchor
  // carrying an explicit but empty data-v="" must still be left alone by
  // the link handler.
  var emptyDv = clickLink('file/emptydv', {dataV:'',
    absHref:'http://buehne.example/file/emptydv'});
  out.linkEmptyDataV = emptyDv.anchor.getAttribute('href');

  // Fire one click on an element that matches BOTH delegated click
  // listeners' selectors (the real return-link shape is `<a href="#"
  // data-v="live">`). The history handler must still act on it (page
  // back to live, preventDefault called); the link handler must leave
  // its href untouched.
  //
  // Driven here with href="file/irgendwas", NOT "#", and an absolute
  // absHref the link handler can resolve. A literal `href="#"` is caught
  // by the link handler's OWN href-starts-with-"#" guard regardless of
  // data-v, so `bothHrefUnchanged == '#'` used to hold even with the
  // data-v guard deleted -- it could never fail for the reason its
  // message named. A non-"#" href removes that confound: with the
  // data-v guard in place the link handler still bails out on this
  // anchor (data-v is present), so the href is left exactly as clickLink
  // above leaves an anchor carrying a data-v attribute (see the
  // data-v checks above); with the guard deleted the link handler would
  // run past it and rewrite the href to append the token, which the
  // check below would catch.
  key('ArrowLeft');  snap('paged-back-for-both');
  var both = clickBoth('file/irgendwas', 'live',
    'http://buehne.example/file/irgendwas');
  snap('after-both');
  out.bothPrevented = both.prevented;
  out.bothHrefUnchanged = both.anchor.getAttribute('href');

  // mitToken's double-token guard. Every poll up to here ran with
  // location.search==='' (a bare stage load), so
  // fetch(mitToken('parts'+location.search)) never saw a URL that
  // ALREADY carried a token -- only the "append it" branch of mitToken
  // ever ran, and deleting the `/[?&]t=/` guard would still leave every
  // check above green. A stage page loaded as /?t=... (the shipped
  // configuration) hits the OTHER branch on every single poll. The real
  // token is read back out of an already-observed fetch call rather than
  // hardcoded here.
  var tokM = /[?&]t=([^&]+)/.exec(fetchCalls[0] || '');
  window.location.search = '?t=' + (tokM ? tokM[1] : '');
  await tickOnce();
  out.pollUrlWithTokenAlreadyInLocation = fetchCalls[fetchCalls.length - 1];

  // --- Befund 2: the toggle must be REVERSIBLE ------------
  // A click hides #protokoll-content -- NOT the whole #protokoll-col,
  // which carries the button itself. Two clicks must return to the
  // exact start state: visible content, "ausblenden" label. This is
  // the toggle's own round-trip test, independent of the DOM stub used
  // for the history steps above.
  var protokollCol = els['protokoll-col'];
  var protokollContent = els['protokoll-content'];
  var protokollToggle = els['protokoll-toggle'];
  out.protokollStartHidden = protokollContent.hasAttribute('hidden');
  out.protokollStartLabel = protokollToggle.textContent;
  out.protokollColStartClass = protokollCol.classList.contains('protokoll-zu');
  clickTarget('protokoll-toggle');
  out.protokollAfter1ClickHidden = protokollContent.hasAttribute('hidden');
  out.protokollAfter1ClickLabel = protokollToggle.textContent;
  out.protokollColAfter1ClickClass = protokollCol.classList.contains('protokoll-zu');
  // The button itself must stay reachable while the content is hidden --
  // in the pre-fix version `hidden` sat on #protokoll-col, which the
  // button is a child of, so it vanished along with the content.
  out.protokollColHiddenAfter1Click = protokollCol.hasAttribute('hidden');
  clickTarget('protokoll-toggle');
  out.protokollAfter2ClicksHidden = protokollContent.hasAttribute('hidden');
  out.protokollAfter2ClicksLabel = protokollToggle.textContent;
  out.protokollColAfter2ClicksClass = protokollCol.classList.contains('protokoll-zu');

  console.log(JSON.stringify(out));
})();
"""


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


STATUS = {"state": "listening", "text": "hoert zu", "ticking": False,
          "since": "", "warning": ""}


# ------------------------------------------------------------------ helpers
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_stage(project: Path):
    """Starts a real `stage.py` on `project`; returns (process, port)."""
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "stage" / "stage.py"),
         "--project", project.name, "--port", str(port), "--host", "127.0.0.1",
         "--token", TEST_TOKEN],
        env={**os.environ, "COTHINKER_ENTITIES": str(project.parent)},
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
    return server, port


def _stop(server) -> None:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()


def _get(port: int, path: str = "") -> str | None:
    """GET `path` with the token, retrying while the server starts up."""
    for _ in range(50):
        try:
            return urllib.request.urlopen(
                f"http://127.0.0.1:{port}/{path}?t={TEST_TOKEN}",
                timeout=2).read().decode()
        except OSError:
            time.sleep(0.2)
    return None


def _parts(port: int) -> dict | None:
    text = _get(port, "parts")
    return json.loads(text) if text is not None else None


def _script_of(seite: str | None) -> str | None:
    treffer = re.search(r"<script>(.*)</script>", seite, re.S) if seite else None
    return treffer.group(1).rstrip() if treffer else None


def _body(skript: str) -> str:
    """The script with its closing line extended by one export."""
    return skript[:-len("})();")] + "\n  global.tickOnce = tick;\n})();"


def _initial_content(seite: str | None) -> str:
    m = (re.search(r'<div id="content">(.*?)</div>\s*(?:<div id="collapsed")',
                   seite, re.S) if seite else None)
    return m.group(1) if m else ""


def _node(path: Path, arg: str | None = None, timeout: int = 60):
    cmd = [shutil.which("node") or "node", str(path)]
    if arg is not None:
        cmd.append(arg)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _last_json(run) -> dict:
    try:
        return json.loads(run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def _out(run) -> str:
    return (run.stderr or run.stdout)[-400:]


VOR_SKRIPT = HARNESS.split("%SCRIPT%")[0]


# ---------------------------------------------------- the history under node
# Three surfaces, built the way a meeting builds them: one call-out and two
# topic changes, each after the minimum hold time; a fourth is published
# while the page is paged back.
@pytest.fixture(scope="module")
def history(tmp_path_factory):
    c = Clock()
    state = StageState(now=c.now)
    state.apply({"action": "main", "markdown": "## Kosten\n- 12.000 EUR",
                 "quote": "zeig die Kosten"}, "Robo, zeig die Kosten.")
    c.advance(TOPIC_MIN_HOLD_S + 30)
    state.apply({"action": "topic", "title": "Rechtsform",
                 "markdown": "## Rechtsform"})
    c.advance(TOPIC_MIN_HOLD_S + 30)
    state.apply({"action": "topic", "title": "Termine", "markdown": "## Termine"})

    projekt = tmp_path_factory.mktemp("cothinker-js")

    def veroeffentliche() -> None:
        inhalt = dict(state.content())
        inhalt["status"] = STATUS
        (projekt / "buehne.json").write_text(
            json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8")

    veroeffentliche()
    server, port = _start_stage(projekt)
    payloads: list[dict] = []
    seite = ""
    try:
        first = _parts(port)
        if first is not None:
            payloads.append(first)
            # A fourth surface, published while the page is paged back.
            c.advance(TOPIC_MIN_HOLD_S + 30)
            state.apply({"action": "topic", "title": "Nachtrag",
                         "markdown": "## Nachtrag"})
            veroeffentliche()
            payloads.append(_parts(port))
            seite = _get(port) or ""
    finally:
        _stop(server)

    ns = SimpleNamespace(payloads=payloads, seite=seite, skript=None,
                         lauf=None, voll={}, schritte={})
    ns.skript = _script_of(seite)
    if ns.skript is not None and ns.skript.endswith("})();"):
        # The script closes its own IIFE and starts on a timer. Only that
        # closing line is touched, so the harness can call the poll instead
        # of waiting a real three seconds -- everything above it is the
        # shipped code.
        laeufer = projekt / "run.js"
        laeufer.write_text(HARNESS.replace("%SCRIPT%", _body(ns.skript)),
                           encoding="utf-8")
        ns.lauf = _node(laeufer, json.dumps(payloads))
        if ns.lauf.returncode == 0:
            ns.voll = json.loads(ns.lauf.stdout)
            ns.schritte = {s["step"]: s for s in ns.voll["steps"]}
    return ns


def test_the_stage_answers_with_a_payload(history):
    assert history.payloads


def test_the_second_payload_carries_four_surfaces(history):
    assert history.payloads
    assert len(history.payloads[-1].get("history", [])) == 4


def test_the_page_carries_its_script(history):
    assert history.skript is not None


def test_the_script_still_closes_the_way_the_harness_expects(history):
    assert history.skript is not None
    assert history.skript.endswith("})();"), history.skript[-40:]


def test_the_page_script_runs_without_throwing(history):
    assert history.lauf is not None
    assert history.lauf.returncode == 0, _out(history.lauf)


def test_the_newest_surface_is_up_and_the_bar_counts_3_of_3(history):
    a = history.schritte["start"]
    assert "Termine" in a["content"] and "Verlauf 3/3" in a["verlauf"], \
        a["verlauf"][:200]


def test_nothing_is_marked_as_paged_back(history):
    assert history.schritte["start"]["zurueck"] is False


def test_the_left_arrow_shows_the_previous_surface_not_just_a_label(history):
    b = history.schritte["links"]
    assert "Rechtsform" in b["content"], b["content"][:200]


def test_an_old_state_is_distinguishable_from_the_current_one(history):
    b = history.schritte["links"]
    assert ("Verlauf 2/3" in b["verlauf"]
            and "zurück zur aktuellen Ansicht" in b["verlauf"]), \
        b["verlauf"][:300]


def test_the_bar_carries_the_paged_back_marking(history):
    assert history.schritte["links"]["zurueck"] is True


def test_a_second_left_arrow_reaches_the_oldest_surface(history):
    d = history.schritte["links2"]
    assert "Kosten" in d["content"], d["content"][:200]


# THE rule of the history design: a new entry must not move the reader.
def test_a_surface_arriving_while_paged_back_does_not_jump_the_reader(history):
    e = history.schritte["neuer-stand"]
    assert "Kosten" in e["content"], e["content"][:200]


def test_it_is_reported_instead_with_its_title(history):
    e = history.schritte["neuer-stand"]
    assert "neu: " in e["verlauf"] and "Nachtrag" in e["verlauf"], \
        e["verlauf"][:300]


def test_and_the_count_grew_to_four(history):
    e = history.schritte["neuer-stand"]
    assert "/4" in e["verlauf"], e["verlauf"][:200]


def test_the_forward_button_pages_forward(history):
    f = history.schritte["vor"]
    assert "Rechtsform" in f["content"], f["content"][:200]


def test_the_return_link_lands_on_the_newest_surface(history):
    g = history.schritte["live"]
    assert "Nachtrag" in g["content"], g["content"][:200]


def test_and_the_paged_back_marking_is_gone(history):
    assert history.schritte["live"]["zurueck"] is False


# --- the token rides the poll
def test_the_polls_fetch_url_carries_the_fixed_test_token(history):
    assert any(f"t={TEST_TOKEN}" in u
               for u in history.voll.get("fetchUrls", [])), \
        str(history.voll.get("fetchUrls"))


# --- the token rides plain links
def test_a_plain_link_gets_the_token_appended_to_its_href(history):
    assert f"t={TEST_TOKEN}" in history.voll.get("linkNormal", ""), \
        history.voll.get("linkNormal")


def test_clicking_a_link_does_not_call_preventdefault(history):
    # Navigation is left to the browser.
    assert history.voll.get("linkNormalPrevented") is False


def test_an_anchor_carrying_data_v_is_left_alone_by_the_link_handler(history):
    assert history.voll.get("linkDataV") == "file/dv", \
        history.voll.get("linkDataV")


def test_an_anchor_with_href_hash_is_left_alone_by_the_link_handler(history):
    assert history.voll.get("linkHash") == "#", history.voll.get("linkHash")


def test_a_link_that_already_carries_a_t_parameter_is_left_alone(history):
    assert (history.voll.get("linkAlready")
            == "http://buehne.example/file/bar?t=already"), \
        history.voll.get("linkAlready")


def test_a_cross_origin_link_is_left_alone(history):
    assert history.voll.get("linkCrossOrigin") == "http://other.example/file/x", \
        history.voll.get("linkCrossOrigin")


def test_a_click_held_with_a_modifier_key_is_left_alone(history):
    assert history.voll.get("linkModifier") == "http://buehne.example/file/mod", \
        history.voll.get("linkModifier")


def test_an_anchor_with_an_empty_data_v_is_left_alone_by_the_link_handler(history):
    # Presence, not truthiness, is what is tested.
    assert history.voll.get("linkEmptyDataV") == "file/emptydv", \
        history.voll.get("linkEmptyDataV")


# --- the one element matching BOTH click selectors
def test_paged_back_once_more_ahead_of_the_dual_selector_check(history):
    h = history.schritte["paged-back-for-both"]
    assert h["zurueck"] is True, h["verlauf"][:200]


def test_a_click_on_the_element_matching_both_selectors_is_acted_on_by_history(history):
    beide = history.schritte["after-both"]
    assert beide["zurueck"] is False and "Nachtrag" in beide["content"], beide


def test_preventdefault_is_called_for_it(history):
    assert history.voll.get("bothPrevented") is True


# Driven with a NON-"#" href so this can only pass because of the data-v
# guard, not the link handler's separate href-starts-with-"#" guard.
def test_the_link_token_handler_leaves_a_non_hash_href_untouched_on_the_dual_anchor(history):
    assert history.voll.get("bothHrefUnchanged") == "file/irgendwas", \
        history.voll.get("bothHrefUnchanged")


# --- the double-token guard of the poll URL
def test_with_the_token_already_in_location_the_poll_url_does_not_carry_it_twice(history):
    url = history.voll.get("pollUrlWithTokenAlreadyInLocation", "") or ""
    assert len(re.findall(r"[?&]t=", url)) == 1, url


# --- the hide/show toggle of the protocol column
def test_start_state_the_protocol_content_is_visible_labelled_ausblenden(history):
    v = history.voll
    assert (v.get("protokollStartHidden") is False
            and v.get("protokollStartLabel") == "ausblenden"), \
        str((v.get("protokollStartHidden"), v.get("protokollStartLabel")))


def test_after_one_click_the_content_is_hidden_label_flips_to_einblenden(history):
    v = history.voll
    assert (v.get("protokollAfter1ClickHidden") is True
            and v.get("protokollAfter1ClickLabel") == "einblenden"), \
        str((v.get("protokollAfter1ClickHidden"),
             v.get("protokollAfter1ClickLabel")))


def test_the_container_stays_visible_while_only_its_content_is_hidden(history):
    # #protokoll-col holds the button itself; hiding it would hide the only
    # way back.
    assert history.voll.get("protokollColHiddenAfter1Click") is False, \
        str(history.voll.get("protokollColHiddenAfter1Click"))


def test_after_a_second_click_back_to_the_exact_start_state(history):
    v = history.voll
    assert (v.get("protokollAfter2ClicksHidden") is False
            and v.get("protokollAfter2ClicksLabel") == "ausblenden"), \
        str((v.get("protokollAfter2ClicksHidden"),
             v.get("protokollAfter2ClicksLabel")))


# ----------------------------------------------------------------------- I1
# An unchanged graph surface is drawn once, not once per poll.
#
# The DOM stand-in above (`El`) stores `innerHTML` as the exact string
# `swap()` last set, so a counter measured against it would sit at 1 with
# or without the fix. A fake that just echoes back what was written can
# never show `swap()` comparing against something ELSE (what cytoscape
# mutated the live DOM into). This harness instead keeps a real element for
# `#content` whose `innerHTML` getter re-serialises the tree on every read,
# the way a browser does -- `data-auf` (set by `grafBooten()`) and a
# `<canvas>` (added by the drawing function) show up in the string `swap()`
# compares against, same as in a browser.
I1_HARNESS = r"""
function ContentEl(){
  this._raw=''; this._grafAttrs={}; this._grafExtra=''; this.classList=new CL(this);
}
ContentEl.prototype = {
  get innerHTML(){
    var m=/(<div class="graf"[^>]*>)/.exec(this._raw);
    var voll;
    if(!m){ voll=this._raw; }
    else {
      var open=m[1];
      var zusatz=Object.keys(this._grafAttrs).map(function(k){
        return ' '+k+'="'+this._grafAttrs[k]+'"'; }, this).join('');
      var neuOpen=open.slice(0,-1)+zusatz+'>';
      voll=this._raw.slice(0,m.index)+neuOpen+this._grafExtra
           +this._raw.slice(m.index+open.length);
    }
    // Decodes the specific entities (`&#x27;`, `&quot;`, `&lt;`, `&gt;`)
    // before the general one (`&amp;`), the way a real browser's innerHTML
    // getter re-serialises an apostrophe or quote back to a plain
    // character. `&amp;` is decoded LAST: decoding it first would turn a
    // source string that legitimately contains the literal text
    // `&amp;quot;` (an escaped ampersand followed by literal "quot;") into
    // `"` instead of the correct `&quot;` -- decoding the specific
    // entities first means the patterns `&amp;` decoding could produce no
    // longer exist to accidentally match afterwards.
    return voll.replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
               .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
               .replace(/&amp;/g, '&');
  },
  set innerHTML(v){
    this._raw=String(v); this._grafAttrs={}; this._grafExtra='';
  },
  querySelector:function(sel){
    if(sel!=='.graf' || !/<div class="graf"/.test(this._raw)) return null;
    var self=this;
    var dgM=/data-graph="([^"]*)"/.exec(this._raw);
    return {
      getAttribute:function(k){
        if(k==='data-graph'){
          // A real browser decodes HTML entities while parsing an
          // attribute (`&quot;` -> `"`) -- graph_part() HTML-escapes the
          // JSON before writing it (stage/stage.py), so grafBooten()'s
          // JSON.parse only works against the DECODED string, same as
          // it would in a real browser.
          return dgM ? dgM[1].replace(/&quot;/g,'"').replace(/&amp;/g,'&')
                              .replace(/&lt;/g,'<').replace(/&gt;/g,'>')
                     : null;
        }
        return self._grafAttrs[k]===undefined ? null : self._grafAttrs[k];
      },
      setAttribute:function(k,v){
        self._grafAttrs[k]=v;
        // The real drawing function appends a <canvas> into the
        // container it is handed -- a change to the live tree a
        // STRING-caching fake could never reflect back into innerHTML.
        if(k==='data-auf') self._grafExtra+='<canvas></canvas>';
      },
      textContent:''
    };
  },
  getAttribute:function(){ return null; },
  setAttribute:function(){},
  get offsetWidth(){ return 1; }
};
function El(id){ this.id=id; this._html=''; this.classList=new CL(this);
  this.attrs={}; }
El.prototype = {
  get innerHTML(){ return this._html; },
  set innerHTML(v){ this._html=String(v); },
  getAttribute:function(k){ return this.attrs[k]===undefined?null:this.attrs[k]; },
  setAttribute:function(k,v){ this.attrs[k]=v; },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  get offsetWidth(){ return 1; }
};
function CL(el){ this.el=el; this.set={}; }
CL.prototype = {
  add:function(c){ this.set[c]=true; },
  remove:function(c){ delete this.set[c]; },
  contains:function(c){ return !!this.set[c]; },
  toggle:function(c,on){ if(on) this.add(c); else this.remove(c); }
};
var els={};
var contentEl=new ContentEl();
function el(id){
  if(id==='content') return contentEl;
  if(!els[id]) els[id]=new El(id);
  return els[id];
}
['verlauf','collapsed','links','punkt','clock','warn','ops-text',
 'mirror','decisions','notices','headline'].forEach(el);

var listeners={};
global.document = {
  getElementById:function(id){ return id==='content' ? contentEl : (els[id]||null); },
  querySelector:function(sel){
    return sel==='#content .graf' ? contentEl.querySelector('.graf') : null;
  },
  querySelectorAll:function(){ return []; },
  addEventListener:function(t,fn){ (listeners[t]=listeners[t]||[]).push(fn); },
  activeElement:null, hidden:false, body:{scrollHeight:1000}
};
global.window = {
  scrollY:0, innerHeight:800,
  getSelection:function(){ return {isCollapsed:true}; },
  scrollTo:function(){}, location:{search:''}
};
global.location = global.window.location;
global.setInterval=function(){ return 0; };
global.setTimeout=function(){ return 0; };

var zeichnungen=0;
global.window.grafZeichnen=function(){ zeichnungen++; };

// A real page load already has #content server-rendered (the initial
// graph surface); the script's own boot line calls grafBooten()
// immediately on load, against that pre-existing DOM -- so the harness
// seeds it the same way instead of starting from an empty div.
el('content').innerHTML = %INITIAL_CONTENT%;

var payloads = JSON.parse(process.argv[2]);
var shot = 0;
global.fetch = async function(){
  var p = payloads[Math.min(shot, payloads.length-1)];
  shot++;
  return { json: async function(){ return p; } };
};

%SCRIPT%

%TREIBER%
"""

# The default driver for `I1_HARNESS`: three polls of the same (unchanged)
# graph surface. Both I1 and I2 drive the harness this way; I1b supplies its
# own arrow-key/history-paging driver instead (see there).
DREI_POLL_TREIBER = r"""(async function(){
  await tickOnce();
  await tickOnce();
  await tickOnce();
  console.log(JSON.stringify({zeichnungen: zeichnungen}));
})();"""


def _graph_run(tmp_path_factory, prefix: str, state, publish_labels, treiber: str,
               n_polls: int):
    """Serves `state`, collects the polls and runs `treiber` under I1_HARNESS."""
    projekt = tmp_path_factory.mktemp(prefix)

    def veroeffentliche() -> None:
        inhalt = dict(state.content())
        inhalt["status"] = STATUS
        (projekt / "buehne.json").write_text(
            json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8")

    server, port = _start_stage(projekt)
    gpayloads: list[dict] = []
    seite = ""
    try:
        for label in publish_labels:
            if label is None:
                veroeffentliche()
            else:
                state.apply({"action": "mirror", "text": label})
                veroeffentliche()
            got = _parts(port)
            if got is not None:
                gpayloads.append(got)
        if gpayloads:
            seite = _get(port) or ""
    finally:
        _stop(server)
    ns = SimpleNamespace(payloads=gpayloads, seite=seite, skript=_script_of(seite),
                         lauf=None)
    if ns.skript is not None:
        laeufer = projekt / "run-graph.js"
        laeufer.write_text(
            I1_HARNESS.replace("%SCRIPT%", _body(ns.skript))
                      .replace("%INITIAL_CONTENT%",
                               json.dumps(_initial_content(seite)))
                      .replace("%TREIBER%", treiber),
            encoding="utf-8")
        ns.lauf = _node(laeufer, json.dumps(gpayloads))
    return ns


def _graph_state(label: str):
    c = Clock()
    state = StageState(now=c.now)
    state.apply({"action": "graph", "title": "Vault",
                 "graph": {"seed": "a.md",
                           "nodes": [{"id": "a.md", "label": label, "ring": 0,
                                      "seed": True, "grace": False}],
                           "edges": []}})
    return state


# One graph surface, then two more polls that each change something
# UNRELATED (the mirror line) but keep the same graph position -- the graph
# must be drawn once for that one surface, not once per poll that happens to
# touch some other part of the page.
@pytest.fixture(scope="module")
def i1(tmp_path_factory):
    return _graph_run(tmp_path_factory, "cothinker-i1", _graph_state("a"),
                      ("eins", "zwei", "drei"), DREI_POLL_TREIBER, 3)


def test_three_parts_polls_for_the_graph_surface_all_answered(i1):
    assert len(i1.payloads) == 3, str(len(i1.payloads))


def test_the_page_carries_its_script_i1_run(i1):
    assert i1.skript is not None


def test_the_i1_harness_runs_without_throwing(i1):
    assert i1.lauf is not None
    assert i1.lauf.returncode == 0, _out(i1.lauf)


def test_an_unchanged_graph_position_is_drawn_once_not_once_per_poll(i1):
    assert i1.lauf is not None and i1.lauf.returncode == 0
    zahl = json.loads(i1.lauf.stdout.strip().splitlines()[-1])["zeichnungen"]
    assert zahl == 1, f"grafZeichnen called {zahl} times over 3 polls"


# ----------------------------------------------------------------------- I2
# An apostrophe in the seed does not fool letzterInhalt.
#
# `graph_part()` (stage/stage.py) HTML-escapes the node label into the
# `data-graph` attribute with `quote=True`, so a label with an apostrophe
# puts a literal `&#x27;` in the SERVER's `main` bytes. A real browser's
# `.innerHTML` getter re-serialises the parsed tree, and an apostrophe never
# needs escaping there -- so it comes back as a plain `'`. `I1_HARNESS`'s
# `ContentEl` models that, which is why a bug in *how the seed is read*
# could hide behind the I1 case: "a" has no such character. Here, a seed
# still built from a DOM read shows a false "change" on the first poll and
# draws the (unchanged) graph twice.
@pytest.fixture(scope="module")
def i2(tmp_path_factory):
    return _graph_run(tmp_path_factory, "cothinker-i2",
                      _graph_state("the operator's Notiz"),
                      ("eins", "zwei", "drei"), DREI_POLL_TREIBER, 3)


def test_three_parts_polls_for_the_apostrophe_surface_all_answered(i2):
    assert len(i2.payloads) == 3, str(len(i2.payloads))


def test_the_servers_own_bytes_carry_the_escaped_apostrophe(i2):
    assert any("&#x27;" in p.get("main", "") for p in i2.payloads), \
        "no &#x27; found in any /parts payload's main"


def test_the_page_carries_its_script_i2_run(i2):
    assert i2.skript is not None


def test_the_i2_harness_runs_without_throwing(i2):
    assert i2.lauf is not None
    assert i2.lauf.returncode == 0, _out(i2.lauf)


def test_an_apostrophe_in_the_seed_still_draws_the_graph_once_not_once_per_poll(i2):
    assert i2.lauf is not None and i2.lauf.returncode == 0
    zahl = json.loads(i2.lauf.stdout.strip().splitlines()[-1])["zeichnungen"]
    assert zahl == 1, f"grafZeichnen called {zahl} times over 3 polls"


# ---------------------------------------------------------------------- I1b
# An unchanged graph surface reached by paging history is drawn once, not
# once per repeated view.
#
# `zeige(i)` (stage/stage.py) is the SECOND call site that feeds #content
# through `swapInhalt`+`grafBooten` -- arrow keys, the paging buttons and the
# "back to live" link all go through it. I1 covers only the OTHER call site
# (the poll handler); this harness exercises `zeige(i)` the same way, by
# combining I1_HARNESS's `ContentEl` with the plain HARNESS's paging driver.
#
# An OLDER graph position is stripped to its seed server-side, so it can no
# longer carry a full drawing to page back to -- only the NEWEST position
# ever does. The fixture therefore puts the graph SECOND (the live/newest
# entry, keeps its nodes/edges) and an unrelated topic FIRST (the older,
# graph-less entry reachable by paging back).
#
# "Page away to the older topic and page back" is NOT the no-op case --
# #content's DOM node for the graph is genuinely destroyed when the
# graph-less topic is swapped in, so redrawing on the way back is correct.
# The real no-op case -- the one `swapInhalt`'s cache against
# `letzterInhalt` exists for -- is calling `zeige()` a SECOND TIME for the
# SAME (live) position with nothing having changed: the right-arrow handler
# does not check the button's `disabled` state, so pressing ArrowRight again
# while already back at the live entry re-invokes `zeige(null)` with an
# identical `e.main`. `swapInhalt` must then no-op and leave the already
# drawn `.graf` node (and its `data-auf` marker) untouched. A plain `swap()`
# compares against `el.innerHTML`, which `grafBooten()` already mutated on
# the previous view -- so it could never match the plain server string again
# and would redraw every time.
I1B_TREIBER = r"""// --- drive it, the way a reader's left/right arrows do --------------------
function fire(type, ev){ (listeners[type]||[]).forEach(function(fn){ fn(ev); }); }
function key(k){ fire('keydown', {key:k, preventDefault:function(){},
  metaKey:false, ctrlKey:false, altKey:false, shiftKey:false}); }
var out={steps:[]};
function snap(label){ out.steps.push({step:label, zeichnungen:zeichnungen}); }

(async function(){
  await tickOnce();                          // the only poll: two history entries
  snap('boot-draw');                         // the live graph, drawn once at load
  key('ArrowLeft');  snap('paged-away');           // older (graph-less) entry
  key('ArrowRight'); snap('back-to-live');         // live graph again, DOM was gone
  key('ArrowRight'); snap('repeat-same-position'); // already live, nothing changed
  console.log(JSON.stringify(out));
})();"""


@pytest.fixture(scope="module")
def i1b(tmp_path_factory):
    # An unrelated topic (history entry 0, the older one reachable by paging
    # back), then -- past the minimum hold -- the graph surface (history
    # entry 1, the live/newest one, which keeps its nodes/edges).
    c = Clock()
    state = StageState(now=c.now)
    state.apply({"action": "topic", "title": "Termine", "markdown": "## Termine"})
    c.advance(TOPIC_MIN_HOLD_S + 30)
    state.apply({"action": "graph", "title": "Vault",
                 "graph": {"seed": "a.md",
                           "nodes": [{"id": "a.md", "label": "a", "ring": 0,
                                      "seed": True, "grace": False}],
                           "edges": []}})
    return _graph_run(tmp_path_factory, "cothinker-i1b", state, (None,),
                      I1B_TREIBER, 1)


def test_the_poll_for_the_two_entry_history_answered(i1b):
    assert i1b.payloads, "no /parts payload"


def test_the_payload_carries_both_history_entries(i1b):
    assert i1b.payloads
    assert len(i1b.payloads[-1].get("history", [])) == 2


def test_the_page_carries_its_script_i1b_run(i1b):
    assert i1b.skript is not None


def test_the_i1b_harness_runs_without_throwing(i1b):
    assert i1b.lauf is not None
    assert i1b.lauf.returncode == 0, _out(i1b.lauf)


@pytest.fixture(scope="module")
def i1b_steps(i1b):
    assert i1b.lauf is not None and i1b.lauf.returncode == 0
    return {s["step"]: s["zeichnungen"]
            for s in json.loads(i1b.lauf.stdout)["steps"]}


def test_the_live_graph_entry_is_drawn_once_on_load(i1b_steps):
    assert i1b_steps.get("boot-draw") == 1, str(i1b_steps)


def test_paging_away_to_the_graph_less_older_entry_draws_nothing_new(i1b_steps):
    assert i1b_steps.get("paged-away") == i1b_steps.get("boot-draw"), \
        str(i1b_steps)


def test_paging_back_to_the_live_graph_entry_legitimately_redraws(i1b_steps):
    # The DOM node was genuinely gone while the older entry was shown.
    assert i1b_steps.get("back-to-live") == i1b_steps.get("boot-draw") + 1, \
        str(i1b_steps)


def test_re_paging_to_the_same_live_entry_is_a_no_op_for_swapinhalt(i1b_steps):
    assert (i1b_steps.get("repeat-same-position")
            == i1b_steps.get("back-to-live")), str(i1b_steps)


# --------------------------------------------------------------- protocol v2
# The open state of keyed details survives a new topic.
#
# The v2 column appends topic blocks; restoring the open state BY INDEX
# would hand the reader's state of one block to its neighbour the moment a
# block is inserted before it (the raw transcript sits after the last
# topic). Keyed blocks are restored BY KEY, and an unseen key keeps the
# server's default: the newest topic arrives open.
#
# The DOM stand-in below parses the `<details>` tags out of the HTML the
# script last set, so a swap really does replace them with fresh elements
# carrying the server's defaults -- the same thing a browser does.
V2_DETAILS = r"""
var dcache={};
function detailsVon(id){
  var e=els[id]; if(!e) return [];
  if(!dcache[id] || dcache[id].html!==e._html){
    var list=[], re=/<details([^>]*)>/g, m;
    while((m=re.exec(e._html))){
      var km=/data-key="([^"]*)"/.exec(m[1]);
      list.push({open:/\sopen(?=[\s>=]|$)/.test(m[1]), _key:km?km[1]:null,
                 _auf:/\sdata-auf=/.test(m[1]),
                 getAttribute:function(k){ return k==='data-key'?this._key:null; },
                 hasAttribute:function(k){ return k==='data-auf'?this._auf:false; }});
    }
    dcache[id]={html:e._html, list:list};
  }
  return dcache[id].list;
}
global.document.querySelectorAll=function(sel){
  return sel==='details'
    ? detailsVon('collapsed').concat(detailsVon('protokoll-content')) : [];
};
function zustand(){
  var z={};
  detailsVon('protokoll-content').forEach(function(d){ z[d._key]=d.open; });
  return z;
}
"""
V2_TREIBER = r"""(async function(){
  await tickOnce();                           // two topics, the second open
  var vorher=zustand();
  // The reader closes the open topic and opens the raw transcript.
  detailsVon('protokoll-content').forEach(function(d){
    if(d._key==='thema-2') d.open=false;
    if(d._key==='roh') d.open=true;
  });
  await tickOnce();                           // a third topic arrives
  console.log(JSON.stringify({vorher:vorher, nachher:zustand()}));
})();
"""
# Nobody touches anything: the previous newest topic must close when a newer
# one arrives -- only a READER's deviation is replayed, not the server's old
# default.
V2_TREIBER_OHNE = r"""(async function(){
  await tickOnce();                           // two topics, the second open
  var vorher=zustand();
  await tickOnce();                           // a third topic arrives
  console.log(JSON.stringify({vorher:vorher, nachher:zustand()}));
})();
"""


def v2_protokoll(themen: int) -> str:
    bloecke = "".join(f"### 10:{n:02d} Thema {n}\n- 10:{n:02d} [notiz] Punkt {n}\n\n"
                      for n in range(1, themen + 1))
    return ("---\nprotokoll_format: 2\n---\n\n# Test\n\n## Überblick\n\n"
            "<!-- Abgeleitete Sicht -->\n\n### Beschlüsse\n\n*noch keine*\n\n"
            f"## Themen\n\n{bloecke}## Rohtranskript\n\n**10:00:01** Hallo.\n")


@pytest.fixture(scope="module")
def v2(tmp_path_factory):
    projekt = tmp_path_factory.mktemp("cothinker-js-v2")
    (projekt / "buehne.json").write_text(
        json.dumps({**StageState(now=Clock().now).content(), "status": STATUS},
                   ensure_ascii=False), encoding="utf-8")
    protokoll = projekt / "protokoll-2026-09-26.md"
    protokoll.write_text(v2_protokoll(2), encoding="utf-8")
    server, port = _start_stage(projekt)
    payloads: list[dict] = []
    seite = ""
    try:
        first = _parts(port)
        if first is not None:
            payloads.append(first)
            protokoll.write_text(v2_protokoll(3), encoding="utf-8")
            payloads.append(_parts(port))
            seite = _get(port) or ""
    finally:
        _stop(server)
    ns = SimpleNamespace(payloads=payloads, skript=_script_of(seite),
                         lauf=None, z=None, lauf_ohne=None, z_ohne=None)
    if ns.skript is not None and len(payloads) == 2:
        rumpf = _body(ns.skript)
        laeufer = projekt / "run-v2.js"
        laeufer.write_text(VOR_SKRIPT + V2_DETAILS + rumpf + "\n" + V2_TREIBER,
                           encoding="utf-8")
        ns.lauf = _node(laeufer, json.dumps(payloads))
        if ns.lauf.returncode == 0:
            ns.z = _last_json(ns.lauf)
        laeufer_b = projekt / "run-v2-ohne.js"
        laeufer_b.write_text(
            VOR_SKRIPT + V2_DETAILS + rumpf + "\n" + V2_TREIBER_OHNE,
            encoding="utf-8")
        ns.lauf_ohne = _node(laeufer_b, json.dumps(payloads))
        if ns.lauf_ohne.returncode == 0:
            ns.z_ohne = _last_json(ns.lauf_ohne)
    return ns


def test_v2_both_payloads_came_back_the_second_with_the_third_topic(v2):
    assert (len(v2.payloads) == 2
            and 'data-key="thema-3"' in v2.payloads[-1].get("protokoll", "")), \
        str([p.get("protokoll", "")[:80] for p in v2.payloads])


def test_the_page_carries_its_script_v2_run(v2):
    assert v2.skript is not None


def test_v2_the_page_script_runs_without_throwing(v2):
    assert v2.lauf is not None
    assert v2.lauf.returncode == 0, _out(v2.lauf)


def test_v2_first_render_only_the_newest_topic_open_raw_closed(v2):
    assert v2.z["vorher"] == {"thema-1": False, "thema-2": True, "roh": False}, \
        str(v2.z["vorher"])


def test_v2_the_newly_arrived_topic_comes_in_open(v2):
    # An unseen key keeps the server default.
    assert v2.z["nachher"].get("thema-3") is True, str(v2.z["nachher"])


def test_v2_the_topic_the_reader_closed_stays_closed(v2):
    assert v2.z["nachher"].get("thema-2") is False, str(v2.z["nachher"])


def test_v2_the_raw_transcript_the_reader_opened_stays_open_by_key(v2):
    nach = v2.z["nachher"]
    assert nach.get("roh") is True and nach.get("thema-1") is False, str(nach)


def test_v2_no_interaction_the_page_script_runs_without_throwing(v2):
    assert v2.lauf_ohne is not None
    assert v2.lauf_ohne.returncode == 0, _out(v2.lauf_ohne)


def test_v2_no_interaction_first_render_has_thema_2_open(v2):
    assert v2.z_ohne["vorher"].get("thema-2") is True, str(v2.z_ohne["vorher"])


def test_v2_no_interaction_after_a_third_topic_only_the_newest_is_open(v2):
    assert v2.z_ohne["nachher"] == {"thema-1": False, "thema-2": False,
                                    "thema-3": True, "roh": False}, \
        str(v2.z_ohne["nachher"])


# --------------------------------------------- structure above the raw transcript
# Live feedback: "the interesting part is below" -- the raw spoken
# transcript sat above the structured result in the stage main area. With a
# structured topic block (a coach result / call result) and a raw-transcript
# section both present, the protocol column must show the structured block
# BEFORE the raw transcript in DOM order, the transcript collapsed by
# default -- and a reader's own open/closed choice for it must survive a
# real page reload (localStorage), not only the 3 s poll that already keeps
# it across live swaps (see the v2 details tests above).
#
# acceptance-scenario: stage-structure-above-transcript
# A fake `details` list parsed from the real, server-rendered
# `#protokoll-content` HTML -- seeded into the fake DOM BEFORE the real
# script runs, so the bootstrap finds the same element a browser's first
# paint would already have in place (the script tag sits at the end of
# <body>).
ROH_PRE = (
    "var gespeichert=%GESPEICHERT%;\n"
    "global.localStorage={getItem:function(k){ "
    "return gespeichert[k]===undefined?null:gespeichert[k]; },\n"
    "  setItem:function(k,v){ gespeichert[k]=String(v); }};\n"
    "els['protokoll-content']._html=%INITIAL_PROTOKOLL%;\n"
    "var dcache6=null;\n"
    "function detailsVon6(){\n"
    "  var e=els['protokoll-content'];\n"
    "  if(!dcache6 || dcache6.html!==e._html){\n"
    "    var list=[], re=/<details([^>]*)>/g, m;\n"
    "    while((m=re.exec(e._html))){\n"
    "      var km=/data-key=\"([^\"]*)\"/.exec(m[1]);\n"
    "      list.push({open:/\\sopen(?=[\\s>=]|$)/.test(m[1]), "
    "_key:km?km[1]:null,\n"
    "                 getAttribute:function(k){ "
    "return k==='data-key'?this._key:null; },\n"
    "                 hasAttribute:function(k){ return false; }});\n"
    "    }\n"
    "    dcache6={html:e._html, list:list};\n"
    "  }\n"
    "  return dcache6.list;\n"
    "}\n"
    "global.document.querySelectorAll=function(sel){\n"
    "  return sel==='details' ? detailsVon6() : [];\n"
    "};\n"
    "function rohVon6(){\n"
    "  var ds=detailsVon6();\n"
    "  for(var i=0;i<ds.length;i++){ if(ds[i]._key==='roh') "
    "return ds[i]; }\n"
    "  return null;\n"
    "}\n"
)


@pytest.fixture(scope="module")
def roh(tmp_path_factory):
    projekt = tmp_path_factory.mktemp("cothinker-js-roh")
    (projekt / "buehne.json").write_text(
        json.dumps({**StageState(now=Clock().now).content(), "status": STATUS},
                   ensure_ascii=False), encoding="utf-8")
    (projekt / "protokoll-2026-10-20.md").write_text(v2_protokoll(1),
                                                     encoding="utf-8")
    server, port = _start_stage(projekt)
    try:
        seite = _get(port) or ""
    finally:
        _stop(server)
    ns = SimpleNamespace(seite=seite, html="", skript=_script_of(seite), runs={},
                         results={})
    inhalt = re.search(r'<div id="protokoll-content">(.*)</div></aside>',
                       seite, re.S)
    ns.html = inhalt.group(1) if inhalt else ""
    if ns.skript is None:
        return ns

    def lauf(gespeichert: dict, treiber: str):
        datei = projekt / f"run-roh-{len(gespeichert)}.js"
        pre = (ROH_PRE
               .replace("%GESPEICHERT%", json.dumps(gespeichert))
               .replace("%INITIAL_PROTOKOLL%", json.dumps(ns.html)))
        datei.write_text(VOR_SKRIPT + pre + ns.skript + "\n" + treiber,
                         encoding="utf-8")
        run = _node(datei, "[]", timeout=30)
        return datei.name, run, (_last_json(run) if run.returncode == 0 else {})

    # B: a reader opened the raw transcript on an earlier visit --
    # localStorage already carries that choice before the page is even
    # fetched. The bootstrap must honour it, not just the server's collapsed
    # default.
    name, run, res = lauf(
        {"cothinker-roh-auf": "1"},
        "console.log(JSON.stringify({offen:(function(){"
        "var r=rohVon6(); return r?r.open:null;})()}));")
    ns.runs["b"], ns.results["b"] = (name, run), res
    # C: the reader toggles the transcript open for the first time -- the
    # choice must be written to localStorage so it survives the next reload
    # (the toggle event doesn't bubble, the page listens on the document in
    # the capturing phase).
    name, run, res = lauf(
        {},
        "var r=rohVon6(); if(r){ r.open=true; "
        "(listeners['toggle']||[]).forEach(function(fn){ "
        "fn({target:r}); }); }\n"
        "console.log(JSON.stringify({gespeichert:gespeichert}));")
    ns.runs["c"], ns.results["c"] = (name, run), res
    return ns


def test_the_stage_answered_with_a_page_roh_order_run(roh):
    assert roh.seite


def test_structure_precedes_the_raw_transcript_in_dom_order(roh):
    pos_thema = roh.html.find('data-key="thema-1"')
    pos_roh = roh.html.find('data-key="roh"')
    assert pos_thema >= 0 and pos_roh >= 0 and pos_thema < pos_roh, \
        f"thema@{pos_thema} roh@{pos_roh}"


def test_the_raw_transcript_is_collapsed_by_default(roh):
    # No `open` on its details.
    assert '<details class="roh" data-key="roh">' in roh.html, roh.html[:200]


def test_the_page_carries_its_script_roh_persistence_run(roh):
    assert roh.skript is not None


@pytest.mark.parametrize("which,datei", [("b", "run-roh-1.js"),
                                         ("c", "run-roh-0.js")])
def test_roh_persistence_run_executes_without_throwing(roh, which, datei):
    assert which in roh.runs
    name, run = roh.runs[which]
    assert name == datei
    assert run.returncode == 0, _out(run)


def test_a_stored_open_choice_is_applied_to_the_raw_transcript_on_bootstrap(roh):
    # Before any poll.
    assert roh.results["b"].get("offen") is True, str(roh.results["b"])


def test_opening_the_raw_transcript_persists_the_choice_to_localstorage(roh):
    ergebnis = roh.results["c"]
    assert ergebnis.get("gespeichert", {}).get("cothinker-roh-auf") == "1", \
        str(ergebnis)


# ------------------------------------------------------ graph.js, the pure half
GRAPH_JS = (ROOT / "stage" / "static" / "graph.js").read_text(encoding="utf-8")
PRUEFUNG = GRAPH_JS + r"""
var g={seed:'a.md',
       nodes:[{id:'a.md',label:'a',ring:0,seed:true,grace:false},
              {id:'b.md',label:'b',ring:1,seed:false,grace:true}],
       edges:[{id:'w',source:'a.md',target:'b.md',kind:'wikilink'},
              {id:'t',source:'a.md',target:'b.md',kind:'tag'}]};
var el=grafElemente(g);
var klassen=el.map(function(e){return e.classes;});

// grafZiel(): the tap handler's actual target. `window` does not exist at
// all under plain node, exactly the "graph.js loaded, page script hasn't
// run yet" moment grafZiel's lazy read is meant to survive.
var none = grafZiel('a.md');
global.window = {STAGE_TOKEN: 'graph-tok'};
var some = grafZiel('a.md');
global.window.STAGE_TOKEN = null;
var gone = grafZiel('a.md');

console.log(JSON.stringify({klassen:klassen, pfad:grafPfad('x/y z.md'),
                            hoch:grafPfad('../../etc/passwd'),
                            raus:grafPfad('../parts'),
                            anzahl:el.length,
                            grafZielNoToken:none,
                            grafZielWithToken:some,
                            grafZielTokenCleared:gone,
                            grafPfadUnchanged:grafPfad('a.md')}));
"""


@pytest.fixture(scope="module")
def graph_js(tmp_path_factory):
    pruef = tmp_path_factory.mktemp("graph-js") / "graph-check.js"
    pruef.write_text(PRUEFUNG, encoding="utf-8")
    roh = _node(pruef, timeout=30)
    ns = SimpleNamespace(run=roh, ergebnis=(
        json.loads(roh.stdout.strip().splitlines()[-1]) if roh.stdout else {}))
    return ns


def test_nodes_and_edges_are_converted_for_cytoscape(graph_js):
    assert graph_js.ergebnis.get("anzahl") == 4, \
        f"{graph_js.run.stdout} {graph_js.run.stderr[:200]}"


def test_both_edge_kinds_and_the_seed_are_distinguishable_by_class(graph_js):
    klassen = graph_js.ergebnis.get("klassen", [])
    assert ("knoten saat" in klassen and "kante wikilink" in klassen
            and "kante tag" in klassen and "knoten gnade" in klassen), \
        str(klassen)


# The id comes out of a payload nobody trusts, so the click has to stay
# under vault/ whatever the id says. Dot segments are the one class that
# `encodeURIComponent` does NOT stop -- the browser resolves them before the
# request, so "../../etc/passwd" would leave the mount entirely.
def test_the_click_target_is_the_vault_path_encoded_and_unable_to_climb(graph_js):
    e = graph_js.ergebnis
    assert (e.get("pfad") == "vault/x/y%20z.md"
            and e.get("hoch") == "vault/etc/passwd"
            and e.get("raus") == "vault/parts"), \
        f'{e.get("pfad")} / {e.get("hoch")} / {e.get("raus")}'


def test_grafziel_appends_no_query_when_no_token_is_published(graph_js):
    assert graph_js.ergebnis.get("grafZielNoToken") == "vault/a.md", \
        graph_js.ergebnis.get("grafZielNoToken")


def test_grafziel_appends_the_token_when_window_stage_token_is_set(graph_js):
    assert graph_js.ergebnis.get("grafZielWithToken") == "vault/a.md?t=graph-tok", \
        graph_js.ergebnis.get("grafZielWithToken")


def test_grafziel_appends_nothing_once_the_token_is_cleared_again(graph_js):
    assert graph_js.ergebnis.get("grafZielTokenCleared") == "vault/a.md", \
        graph_js.ergebnis.get("grafZielTokenCleared")


def test_grafpfads_own_output_is_unchanged_by_grafziels_introduction(graph_js):
    assert graph_js.ergebnis.get("grafPfadUnchanged") == "vault/a.md", \
        graph_js.ergebnis.get("grafPfadUnchanged")


# ------------------------------- where a topic click from the graph tab lands
# The pure half of the stage's `cothinker-topic` listener, taken from the
# served page's script as shipped (a top-level function, column-0 braces).
def _topic_target_fn(history):
    skript = history.skript or ""
    return re.search(r"^function stageTopicTarget\(.*?^\}", skript, re.S | re.M)


ZIEL_PRUEFUNG_BODY = r"""
function El(tag, text){ return {tagName:tag, textContent:text, id:tag+':'+text}; }
var lang='Ein sehr langer Thementitel, der ueber die achtzig Zeichen hinausgeht und abgeschnitten wird';
var kurz=lang.slice(0,79).replace(/\s+$/,'')+'…';
var els=[El('H1','Protokoll'), El('H2','Entscheidungen'), El('LI','10:05 '+lang),
         El('H2','Rohtranskript'), El('P','10:40 Kurzer Titel und mehr'), El('LI','11:11 etwas')];
function id(e){ return e ? e.id : null; }
console.log(JSON.stringify({
  lang:id(stageTopicTarget(els, kurz, '')),
  zeitRoh:id(stageTopicTarget(els, 'gibt es nicht', '11:11')),
  titelRoh:id(stageTopicTarget(els, 'Kurzer Titel', '')),
  zeit:id(stageTopicTarget(els, 'gibt es nicht', '10:05')),
  nichts:id(stageTopicTarget(els, '…', ''))
}));
"""


@pytest.fixture(scope="module")
def topic_target(history, tmp_path_factory):
    ziel_fn = _topic_target_fn(history)
    ns = SimpleNamespace(fn=ziel_fn, run=None, zt={})
    pruef = tmp_path_factory.mktemp("topic-target") / "topic-target-check.js"
    pruef.write_text((ziel_fn.group(0) if ziel_fn else "") + ZIEL_PRUEFUNG_BODY,
                     encoding="utf-8")
    ns.run = _node(pruef, timeout=30)
    try:
        ns.zt = json.loads(ns.run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        ns.zt = {}
    return ns


def test_the_stage_script_carries_stagetopictarget(history):
    assert _topic_target_fn(history) is not None, (history.skript or "")[:200]


def test_a_truncated_title_still_finds_its_topic_by_prefix(topic_target):
    zt = topic_target.zt
    assert (zt.get("lang") is not None
            and zt.get("lang", "").startswith("LI:10:05")), \
        f"{zt} {topic_target.run.stderr[-300:]}"


def test_nothing_below_the_rohtranskript_heading_is_a_jump_target(topic_target):
    zt = topic_target.zt
    assert zt.get("zeitRoh") is None and zt.get("titelRoh") is None, str(zt)


def test_the_time_fallback_still_finds_a_protocol_line_above_it(topic_target):
    zt = topic_target.zt
    assert (zt.get("zeit") or "").startswith("LI:10:05"), str(zt)


def test_a_bare_ellipsis_matches_nothing(topic_target):
    assert topic_target.zt.get("nichts") is None, str(topic_target.zt)


# --------------------- the #block-<id> anchor jump for a directly opened protocol
ANKER_PRUEFUNG_BODY = r"""
function El(tag, text){ return {tagName:tag, textContent:text, id:tag+':'+text,
  scrolled:0, scrollIntoView:function(){ this.scrolled++; }}; }
var els=[El('H1','Protokoll'), El('H3','10:00 Erstes Thema'), El('H3','10:30 Zweites Thema')];
function fakeDoc(list){
  return { getElementById:function(id){ return id==='content' ?
    { querySelectorAll:function(){ return list; } } : null; } };
}
var out={};
// no SG_BLOCKS at all (any other page): nothing happens, nothing thrown
global.window={SG_BLOCKS:null};
global.location={hash:'#block-T:a'};
global.document=fakeDoc(els);
out.ohneBlocks=sgBlockAnchorJump();

// SG_BLOCKS set (the /file/ route, running protocol), hash matches a
// known id: scrolls to the matching heading, same rule stageTopicTarget
// already applies for the live-opener postMessage path.
global.window={SG_BLOCKS:{'T:a':{title:'Erstes Thema', time:'10:00'},
                          'T:b':{title:'Zweites Thema', time:'10:30'}}};
global.location={hash:'#block-'+encodeURIComponent('T:b')};
var gefunden=sgBlockAnchorJump();
out.gefundenId=gefunden?gefunden.id:null;
out.scrolled=els.map(function(e){return e.scrolled;});

// a hash without the #block- prefix: no-op
global.location={hash:'#sonstwas'};
out.ohneHash=sgBlockAnchorJump();

// a hash id SG_BLOCKS doesn't carry: no-op, nothing thrown
global.location={hash:'#block-unbekannt'};
out.unbekannt=sgBlockAnchorJump();

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def block_anchor(history, tmp_path_factory):
    skript = history.skript or ""
    ziel_fn = _topic_target_fn(history)
    anker_fn = re.search(r"^function sgBlockAnchorJump\(\).*?^\}", skript,
                         re.S | re.M)
    ns = SimpleNamespace(fn=anker_fn, run=None, ak={})
    pruef = tmp_path_factory.mktemp("block-anchor") / "block-anchor-check.js"
    pruef.write_text((ziel_fn.group(0) if ziel_fn else "") + "\n"
                     + (anker_fn.group(0) if anker_fn else "")
                     + ANKER_PRUEFUNG_BODY, encoding="utf-8")
    ns.run = _node(pruef, timeout=30)
    try:
        ns.ak = json.loads(ns.run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        ns.ak = {}
    return ns


def test_the_stage_script_carries_sgblockanchorjump(block_anchor, history):
    assert block_anchor.fn is not None, (history.skript or "")[:200]


def test_without_window_sg_blocks_the_jump_is_a_no_op(block_anchor):
    # Any page but the running protocol's own /file/ view.
    assert block_anchor.ak.get("ohneBlocks") is None, \
        f"{block_anchor.ak} {block_anchor.run.stderr[-300:]}"


def test_a_block_hash_sg_blocks_knows_scrolls_to_the_matching_heading(block_anchor):
    ak = block_anchor.ak
    assert (ak.get("gefundenId") == "H3:10:30 Zweites Thema"
            and ak.get("scrolled") == [0, 0, 1]), str(ak)


def test_a_hash_without_the_block_prefix_is_a_no_op(block_anchor):
    assert block_anchor.ak.get("ohneHash") is None, str(block_anchor.ak)


def test_a_block_id_sg_blocks_does_not_carry_is_a_no_op_nothing_thrown(block_anchor):
    assert block_anchor.ak.get("unbekannt") is None, str(block_anchor.ak)


# --------------- session_graph.js: diff, kanban, flash, clicks -- against a fake cy
# The fake cy implements exactly the subset session_graph.js documents at its
# top (SG_CY_API): if the script reaches for anything else, it throws here
# instead of silently passing. Positions are plain {x, y} copies, so "kept
# nodes keep their position" is a value comparison, not an identity.
SESSION_GRAPH_JS = (ROOT / "stage" / "static" / "session_graph.js"
                    ).read_text(encoding="utf-8")
SG_HARNESS = r"""
var zeitgeber=[];
global.setTimeout=function(fn,ms){ zeitgeber.push({fn:fn,ms:ms}); return zeitgeber.length; };
global.setInterval=function(){ return 0; };
function timeoutsAusfuehren(){ var z=zeitgeber; zeitgeber=[]; z.forEach(function(t){ t.fn(); }); return z; }

var konstruktionen=0, layoutLaeufe=[], ereignisse={};
function FakeEle(cy, spec){
  this.cy=cy; this.group=spec.group; this._d=JSON.parse(JSON.stringify(spec.data));
  this._cls={}; this.classes(spec.classes||'');
  this._pos=spec.position?{x:spec.position.x,y:spec.position.y}:{x:0,y:0};
  this.length=1;
}
FakeEle.prototype={
  id:function(){ return this._d.id; },
  data:function(k){ if(k===undefined) return this._d;
    if(typeof k==='object'){ for(var x in k) this._d[x]=k[x]; return this; }
    return this._d[k]; },
  position:function(p){ if(p===undefined) return {x:this._pos.x,y:this._pos.y};
    this._pos={x:p.x,y:p.y}; return this; },
  addClass:function(c){ this._cls[c]=true; return this; },
  removeClass:function(c){ delete this._cls[c]; return this; },
  hasClass:function(c){ return !!this._cls[c]; },
  classes:function(s){ if(s===undefined) return Object.keys(this._cls);
    this._cls={}; String(s).split(/\s+/).forEach(function(c){ if(c) this._cls[c]=true; }, this);
    return this; },
  remove:function(){ this.cy._entfernen(this); return this; }
};
function FakeCy(opts){ konstruktionen++; this.opts=opts; this.els={}; this.ord=[]; }
FakeCy.prototype={
  add:function(spec){ var liste=Array.isArray(spec)?spec:[spec], self=this;
    liste.forEach(function(s){
      if(self.els[s.data.id]) throw new Error('duplicate id '+s.data.id);
      if(s.group==='edges' && !(self.els[s.data.source] && self.els[s.data.target]))
        throw new Error('edge with a missing end '+s.data.id);
      var e=new FakeEle(self,s); self.els[e.id()]=e; self.ord.push(e.id()); }); },
  _entfernen:function(e){ var self=this; if(!self.els[e.id()]) return;
    delete self.els[e.id()]; self.ord=self.ord.filter(function(i){ return i!==e.id(); });
    if(e.group==='nodes') self.edges().forEach(function(k){
      if(k.data('source')===e.id()||k.data('target')===e.id()) self._entfernen(k); }); },
  getElementById:function(id){ return this.els[id]||{length:0}; },
  nodes:function(){ var s=this; return s.ord.map(function(i){return s.els[i];})
    .filter(function(e){return e.group==='nodes';}); },
  edges:function(){ var s=this; return s.ord.map(function(i){return s.els[i];})
    .filter(function(e){return e.group==='edges';}); },
  layout:function(o){ return {run:function(){ layoutLaeufe.push(o.name); }}; },
  on:function(ev,sel,fn){ ereignisse[ev+' '+sel]=fn; },
  fits:0,
  fit:function(){ this.fits++; },
  animationen:0,
  animate:function(o){ if(o && o.fit) this.fits++; this.animationen++; },
  elements:function(){ var s=this; return s.ord.map(function(i){return s.els[i];}); },
  // The visible model-coordinate box; wide open unless a test narrows it.
  ext:{x1:-1e6,y1:-1e6,x2:1e6,y2:1e6},
  extent:function(){ var e=this.ext; return {x1:e.x1,y1:e.y1,x2:e.x2,y2:e.y2}; }
};

function N(id,kind,extra){ var n={id:id,label:id,kind:kind,ring:0,href:'',time:'',
  tag:'',parent:'',hidden:false,grace:false}; for(var k in (extra||{})) n[k]=extra[k]; return n; }
function E(id,s,t,kind,ring){ return {id:id,source:s,target:t,kind:kind,ring:ring||0}; }

var g1={seed:'file:s.md', hidden:1, nodes:[
  N('file:s.md','session',{href:'file/s.md'}),
  N('t1','topic',{parent:'file:s.md',time:'10:00'}),
  N('b1','entry',{parent:'t1',tag:'beschluss'}),
  N('b2','entry',{parent:'t1',tag:'beschluss'}),
  N('a1','entry',{parent:'t1',tag:'aufgabe'}),
  N('vault:n.md','note',{ring:1,parent:'t1',href:'vault/n.md'}),
  N('vault:m.md','note',{ring:2,parent:'vault:n.md',href:'vault/m.md',grace:true}),
  N('vault:x.md','note',{ring:2,parent:'vault:n.md',href:'vault/x.md',hidden:true})],
 edges:[E('b:t1','file:s.md','t1','block'), E('e:b1','t1','b1','entry'),
  E('e:b2','t1','b2','entry'), E('e:a1','t1','a1','entry'),
  E('w:t1|vault:n.md','t1','vault:n.md','wikilink',1),
  E('n:w:vault:m.md|vault:n.md','vault:m.md','vault:n.md','wikilink',2),
  E('n:t:vault:m.md|vault:x.md','vault:m.md','vault:x.md','tag',2)]};

var out={};
var el=sgElemente(g1,{});
var ids=el.map(function(e){return e.data.id;});
out.elIds=ids;
out.moreLabel=(el.filter(function(e){return e.data.id==='more:vault:n.md';})[0]||{data:{}}).data.label;
out.moreClasses=(el.filter(function(e){return e.data.id==='more:vault:n.md';})[0]||{}).classes;
out.ring2Edge=(el.filter(function(e){return e.data.id==='n:w:vault:m.md|vault:n.md';})[0]||{}).classes;
out.graceCls=(el.filter(function(e){return e.data.id==='vault:m.md';})[0]||{}).classes;
out.entryCls=(el.filter(function(e){return e.data.id==='b1';})[0]||{}).classes;
out.hiddenEdgeDrawn=ids.indexOf('n:t:vault:m.md|vault:x.md')>=0;
out.cyParentKey=el.some(function(e){ return e.data.parent!==undefined; });
out.zaehler=sgZaehler(g1,{});

// sgStart against a fake page: first poll draws, second poll diffs.
var payloads=[], abrufe=[];
var geoeffnet=[], gepostet=[], gespeichert={};
var knoepfe={}; ['graph','kanban'].forEach(function(l){
  knoepfe[l]={attrs:{'data-layout':l}, handler:null, cls:{},
    getAttribute:function(k){return this.attrs[k];},
    setAttribute:function(k,v){this.attrs[k]=v;},
    classList:{_c:{}, toggle:function(c,on){ if(on) this._c[c]=1; else delete this._c[c]; },
               contains:function(c){ return !!this._c[c]; }},
    addEventListener:function(ev,fn){ this.handler=fn; }}; });
var zaehlEl={textContent:''};
var buehneLink={handler:null, addEventListener:function(ev,fn){ this.handler=fn; }};
global.document={hidden:false,
  getElementById:function(id){ return id==='sg-count'?zaehlEl:(id==='sg-buehne'?buehneLink:null); },
  querySelectorAll:function(sel){ return sel.indexOf('sg-toggle')>=0?[knoepfe.graph,knoepfe.kanban]:[]; },
  addEventListener:function(){}};
global.location={origin:'http://stage.test'};
global.localStorage={getItem:function(k){ return gespeichert[k]===undefined?null:gespeichert[k]; },
  setItem:function(k,v){ gespeichert[k]=String(v); }};
var opener={closed:false, fokus:0, postMessage:function(m,o){ gepostet.push({m:m,o:o}); },
  focus:function(){ this.fokus++; }};
global.window={STAGE_TOKEN:'sg tok', cytoscape:function(o){ return new FakeCy(o); },
  cytoscapeFcose:function(){}, opener:opener,
  open:function(u,t){ geoeffnet.push([u,t]); }};
global.fetch=function(u){ abrufe.push(u); var p=payloads.shift();
  return Promise.resolve({json:function(){ return Promise.resolve({graph:p}); }}); };

var g2=JSON.parse(JSON.stringify(g1));
g2.nodes.splice(2,0,N('t2','topic',{parent:'file:s.md',time:'10:30',label:'Neues Thema'}));
g2.edges.push(E('b:t2','file:s.md','t2','block'));
var g3=JSON.parse(JSON.stringify(g2));
g3.nodes=g3.nodes.filter(function(n){ return n.id!=='a1'; });
g3.edges=g3.edges.filter(function(e){ return e.target!=='a1'; });
var g4=JSON.parse(JSON.stringify(g3));
g4.nodes.forEach(function(n){ if(n.id==='vault:m.md') n.hidden=true; });
g4.hidden=2;

function positionen(cy){ var p={}; cy.nodes().forEach(function(n){ p[n.id()]=n.position(); }); return p; }

(async function(){
  var box={textContent:'', clientWidth:1000, style:{cursor:''}};
  payloads=[g1, g1, g2, g3, g4];
  var st=sgStart(box);
  await st.tick();
  var cy=st.cy;
  out.ersteLaeufe=layoutLaeufe.slice();
  out.ersteKnoten=cy.nodes().map(function(n){return n.id();});
  out.ersteNeu=cy.nodes().filter(function(n){return n.hasClass('neu');}).length;
  out.zaehlText=zaehlEl.textContent;
  out.abruf=abrufe[0];
  timeoutsAusfuehren();
  await st.tick();                        // same payload: no diff at all
  out.gleichLetzt=st.letztesErgebnis; out.gleichDiffs=st.diffs;
  var vorher=positionen(cy);
  await st.tick();                        // g2: one topic + one edge more
  var nachher=positionen(cy);
  out.diff2=st.letztesErgebnis;
  out.keptGleich=Object.keys(vorher).every(function(k){
    return nachher[k] && nachher[k].x===vorher[k].x && nachher[k].y===vorher[k].y; });
  out.t2Neu=cy.getElementById('t2').hasClass('neu');
  out.sessionNeu=cy.getElementById('file:s.md').hasClass('neu');
  out.t1Neu=cy.getElementById('t1').hasClass('neu');
  var pt2=cy.getElementById('t2').position(), ps=cy.getElementById('file:s.md').position();
  out.t2Naehe=Math.sqrt(Math.pow(pt2.x-ps.x,2)+Math.pow(pt2.y-ps.y,2));
  var laufende=zeitgeber.map(function(t){return t.ms;});
  timeoutsAusfuehren();
  out.flashMs=laufende;
  out.t2NeuDanach=cy.getElementById('t2').hasClass('neu');
  out.zeitgeberDanach=zeitgeber.length;
  await st.tick();                        // g3: a1 gone
  out.diff3=st.letztesErgebnis;
  out.nachEntfernen=cy.nodes().map(function(n){return n.id();});
  var pmVor=cy.getElementById('vault:m.md').position();
  await st.tick();                        // g4: vault:m.md pushed over the cap
  var mNach=cy.getElementById('vault:m.md');
  out.seiteBleibt={da:mNach.length, gleich:!!(mNach.length
    && mNach.position().x===pmVor.x && mNach.position().y===pmVor.y)};
  out.konstruktionen=konstruktionen;
  out.laeufeNachUpdates=layoutLaeufe.slice();

  // Expanding: vault:x.md is hidden under vault:n.md.
  var aus={};
  var fcy=new FakeCy({});
  sgDiff(fcy, g1, 'graph', {ausgeklappt:aus, breite:1000});
  out.xVorher=fcy.getElementById('vault:x.md').length;
  sgAusklappen(aus, 'more:vault:n.md');
  var d=sgDiff(fcy, g1, 'graph', {ausgeklappt:aus, breite:1000});
  out.xNachher=fcy.getElementById('vault:x.md').length;
  out.xKanteNachher=fcy.getElementById('n:t:vault:m.md|vault:x.md').length;
  out.moreNachher=fcy.getElementById('more:vault:n.md').length;
  out.ausklappDiff=d;

  // Kanban arrangement.
  var kp=sgKanbanPositionen(g1, 1000, {});
  out.kanban=kp;

  // Clicks.
  var tap=ereignisse['tap node'];
  tap({target:cy.getElementById('vault:n.md')});
  tap({target:cy.getElementById('t1')});
  window.opener={closed:true, postMessage:function(){ gepostet.push('closed!'); }};
  tap({target:cy.getElementById('t1')});          // closed opener: direct jump
  window.opener=undefined;
  tap({target:cy.getElementById('t2')});          // no opener at all: same jump
  window.opener=opener;
  tap({target:cy.getElementById('b1')});         // no href: nothing

  // Cursor: a hover handler, not a CSS rule cytoscape has no hook for.
  // Before the expander tap below -- that un-hides its children and the
  // expander itself goes away, so its node would be gone by then.
  var hover=ereignisse['mouseover node'], hoverAus=ereignisse['mouseout node'];
  hover({target:cy.getElementById('b1')});        // entry, no handler: default
  out.cursorEntry=box.style.cursor;
  hover({target:cy.getElementById('t1')});        // sg-topic: pointer
  out.cursorTopic=box.style.cursor;
  hoverAus({target:cy.getElementById('t1')});
  out.cursorAus=box.style.cursor;
  hover({target:cy.getElementById('vault:n.md')}); // note with href: pointer
  out.cursorNote=box.style.cursor;
  hover({target:cy.getElementById('more:vault:n.md')}); // expander: pointer
  out.cursorMehr=box.style.cursor;
  hoverAus({target:cy.getElementById('more:vault:n.md')});
  out.klickbar=[sgKlickbar(cy.getElementById('b1')), sgKlickbar(cy.getElementById('t1')),
    sgKlickbar(cy.getElementById('vault:n.md')), sgKlickbar(cy.getElementById('more:vault:n.md')),
    sgKlickbar(cy.getElementById('file:s.md'))];

  var ausVor=cy.getElementById('vault:x.md').length;
  tap({target:cy.getElementById('more:vault:n.md')});
  out.tapAusklappen=[ausVor, cy.getElementById('vault:x.md').length];
  out.geoeffnet=geoeffnet; out.gepostet=gepostet;

  // Toggle.
  var laeufeVor=layoutLaeufe.length;
  knoepfe.kanban.handler({preventDefault:function(){}});
  out.nachKanban={gespeichert:gespeichert['cothinker.graph.layout'],
    laeufe:layoutLaeufe.slice(laeufeVor), kopf:cy.getElementById('col:beschluss').length,
    kopfLabel:cy.getElementById('col:beschluss').length?cy.getElementById('col:beschluss').data('label'):'',
    pressed:knoepfe.kanban.attrs['aria-pressed'], an:knoepfe.kanban.classList.contains('an'),
    graphPressed:knoepfe.graph.attrs['aria-pressed']};
  var nnKante='n:w:vault:m.md|vault:n.md', tnKante='w:t1|vault:n.md';
  out.nachKanban.nnVerdeckt=cy.getElementById(nnKante).hasClass('verdeckt');
  out.nachKanban.nnNurgraf=cy.getElementById(nnKante).hasClass('nurgraf');
  out.nachKanban.tnVerdeckt=cy.getElementById(tnKante).hasClass('verdeckt');
  laeufeVor=layoutLaeufe.length;
  knoepfe.graph.handler({preventDefault:function(){}});
  out.nachGraph={gespeichert:gespeichert['cothinker.graph.layout'],
    laeufe:layoutLaeufe.slice(laeufeVor), kopf:cy.getElementById('col:beschluss').length,
    nnVerdeckt:cy.getElementById(nnKante).hasClass('verdeckt'),
    nnDa:cy.getElementById(nnKante).length};
  var stil=JSON.stringify(cy.opts.style||[]);
  out.stil={font:stil.indexOf('VT323')>=0 && stil.indexOf('font-family')>=0,
    hintergrund:stil.indexOf('"text-background-color":"#050a06"')>=0
      && stil.indexOf('"text-background-opacity":0.75')>=0,
    minZoom:stil.indexOf('"min-zoomed-font-size":6')>=0,
    verdeckt:(cy.opts.style||[]).some(function(s){
      return s.selector==='edge.verdeckt' && s.style.display==='none'; })};

  // Buehne link: a plain link (new tab). Cross-tab focus() is ignored by
  // Chromium, so a handler that prevented the default did nothing at all.
  out.buehneHandler=buehneLink.handler!==null;

  // Stored layout is read at start; a throwing localStorage falls back.
  gespeichert['cothinker.graph.layout']='kanban';
  out.gelesen=sgLayoutLesen();
  global.localStorage={getItem:function(){ throw new Error('blocked'); }};
  out.gelesenKaputt=sgLayoutLesen();

  // No cytoscape: the German fallback text, nothing thrown.
  var leer={textContent:''};
  var cyVor=window.cytoscape; window.cytoscape=undefined;
  sgStart(leer); window.cytoscape=cyVor;
  out.ohneCy=leer.textContent;

  // --- review fixes: kanban live updates place against what is DRAWN ---
  function ueberlappt(cy, ausser){ // any two nodes on (nearly) the same spot
    var ns=cy.nodes(), treffer=[];
    for(var i=0;i<ns.length;i++) for(var j=i+1;j<ns.length;j++){
      var a=ns[i].position(), b=ns[j].position();
      if(Math.abs(a.x-b.x)<50 && Math.abs(a.y-b.y)<25) treffer.push([ns[i].id(),ns[j].id()]); }
    return treffer; }
  var basis={seed:'s', hidden:0, nodes:[N('s','session'), N('k1','topic',{parent:'s'}),
      N('kb1','entry',{parent:'k1',tag:'beschluss'}),
      N('vault:p.md','note',{ring:1,parent:'k1'}), N('vault:q.md','note',{ring:1,parent:'k1'}),
      N('vault:r.md','note',{ring:1,parent:'k1'})],
    edges:[E('b:k1','s','k1','block'), E('e:kb1','k1','kb1','entry'),
      E('w:p','k1','vault:p.md','wikilink',1), E('w:q','k1','vault:q.md','wikilink',1),
      E('w:r','k1','vault:r.md','wikilink',1)]};
  var kcy=new FakeCy({}), kg=JSON.parse(JSON.stringify(basis));
  sgDiff(kcy, kg, 'kanban', {ausgeklappt:{}, breite:1000, flash:false});
  // (1) five topics, one diff at a time, newest first in the payload
  for(var ti=2; ti<=6; ti++){
    kg.nodes.splice(1,0,N('k'+ti,'topic',{parent:'s'}));
    kg.edges.push(E('b:k'+ti,'s','k'+ti,'block'));
    sgDiff(kcy, kg, 'kanban', {ausgeklappt:{}, breite:1000, flash:false});
  }
  var tops=kcy.nodes().filter(function(n){return n.data('kind')==='topic';}), minAbst=1e9;
  for(var i=0;i<tops.length;i++) for(var j=i+1;j<tops.length;j++){
    var a=tops[i].position(), b=tops[j].position();
    minAbst=Math.min(minAbst, Math.sqrt(Math.pow(a.x-b.x,2)+Math.pow(a.y-b.y,2))); }
  out.kTopics={anzahl:tops.length, minAbst:minAbst};
  // (2) three entries into the deepest column, one diff each
  for(var ei=2; ei<=4; ei++){
    kg.nodes.push(N('kb'+ei,'entry',{parent:'k1',tag:'beschluss'}));
    kg.edges.push(E('e:kb'+ei,'k1','kb'+ei,'entry'));
    sgDiff(kcy, kg, 'kanban', {ausgeklappt:{}, breite:1000, flash:false});
  }
  var bxs={}; ['kb2','kb3','kb4'].forEach(function(id){ bxs[kcy.getElementById(id).position().x]=1; });
  out.kSpalte={ueberlappt:ueberlappt(kcy), einX:Object.keys(bxs).length===1,
    xGleichKopf:kcy.getElementById('kb4').position().x===kcy.getElementById('col:beschluss').position().x};
  // (3) a strip note inserted mid-list
  var idx=kg.nodes.map(function(n){return n.id;}).indexOf('vault:q.md');
  kg.nodes.splice(idx,0,N('vault:mitte.md','note',{ring:1,parent:'k1'}));
  kg.edges.push(E('w:mitte','k1','vault:mitte.md','wikilink',1));
  sgDiff(kcy, kg, 'kanban', {ausgeklappt:{}, breite:1000, flash:false});
  out.kStreifen={ueberlappt:ueberlappt(kcy), da:kcy.getElementById('vault:mitte.md').length};

  // MINOR 1: unanchored new nodes in one diff fan out; children of an
  // expanded parent that is itself not shown stay hidden.
  var ucy=new FakeCy({});
  sgDiff(ucy, {nodes:[N('u0','topic')], edges:[]}, 'graph', {flash:false});
  sgDiff(ucy, {nodes:[N('u0','topic'),N('u1','topic'),N('u2','topic'),N('u3','topic')], edges:[]},
         'graph', {flash:false});
  out.unverankert=ueberlappt(ucy);
  var tief={nodes:[N('r','session'), N('h','topic',{parent:'r',hidden:true}),
                   N('hk','entry',{parent:'h',hidden:true,tag:'frage'})], edges:[], hidden:2};
  out.tiefZu=sgElemente(tief,{h:true}).map(function(e){return e.data.id;});
  out.tiefAuf=sgElemente(tief,{r:true,h:true}).map(function(e){return e.data.id;});
  out.tiefZaehler=sgZaehler(tief,{h:true});

  // MINOR 2: graph mode fits once when an added node lands outside view.
  var fcy2=new FakeCy({}); fcy2.ext={x1:-100,y1:-100,x2:100,y2:100};
  sgDiff(fcy2, {nodes:[N('f0','topic')], edges:[]}, 'graph', {flash:false});
  var fitVor=fcy2.fits;
  sgDiff(fcy2, {nodes:[N('f0','topic'),N('f1','topic')], edges:[E('x1','f0','f1','block')]},
         'graph', {einpassen:true});
  out.fitDrinnen=fcy2.fits-fitVor;
  fcy2.getElementById('f0').position({x:500,y:500});
  fcy2.getElementById('f1').position({x:560,y:500});
  sgDiff(fcy2, {nodes:[N('f0','topic'),N('f1','topic'),N('f2','topic')],
                edges:[E('x1','f0','f1','block'),E('x2','f0','f2','block')]},
         'graph', {einpassen:true});
  out.fitDraussen=fcy2.fits-fitVor;
  sgDiff(fcy2, {nodes:[N('f0','topic'),N('f1','topic'),N('f2','topic')],
                edges:[E('x1','f0','f1','block'),E('x2','f0','f2','block')]},
         'graph', {einpassen:true});
  out.fitOhneNeues=fcy2.fits-fitVor;
  out.fitAnimiert=fcy2.animationen;

  // Kanban: an entry growing below the view edge fits once, animated.
  var kcy2=new FakeCy({}), kg2=JSON.parse(JSON.stringify(basis));
  kcy2.ext={x1:-1e6,y1:-1e6,x2:1e6,y2:400};
  sgDiff(kcy2, kg2, 'kanban', {ausgeklappt:{}, breite:1000, flash:false});
  var kfits=[];
  for(var ki=2; ki<=3; ki++){
    kg2.nodes.push(N('kb'+ki,'entry',{parent:'k1',tag:'beschluss'}));
    kg2.edges.push(E('e:kb'+ki,'k1','kb'+ki,'entry'));
    sgDiff(kcy2, kg2, 'kanban', {ausgeklappt:{}, breite:1000, flash:false, einpassen:true});
    kfits.push([kcy2.getElementById('kb'+ki).position().y, kcy2.animationen, kcy2.fits]);
  }
  out.kanbanFit=kfits;

  // IMPORTANT 1: the server's cap flips `hidden` by insertion order, so a
  // new topic pushes an already drawn note over it. Drawn once, it stays
  // while it is in the payload; the cap only restrains what is not drawn.
  var gz={}, gcy=new FakeCy({});
  var pa={seed:'s', hidden:0, nodes:[N('s','session'), N('gt1','topic',{parent:'s'}),
      N('vault:nn.md','note',{ring:1,parent:'gt1'})],
    edges:[E('b:gt1','s','gt1','block'), E('w:nn','gt1','vault:nn.md','wikilink',1)]};
  sgDiff(gcy, pa, 'graph', {ausgeklappt:{}, gezeigt:gz, flash:false});
  var posNn=gcy.getElementById('vault:nn.md').position();
  var pb=JSON.parse(JSON.stringify(pa));
  pb.nodes.splice(1,0,N('gt2','topic',{parent:'s'}));
  pb.edges.push(E('b:gt2','s','gt2','block'));
  pb.nodes.forEach(function(n){ if(n.id==='vault:nn.md') n.hidden=true; });
  pb.nodes.push(N('vault:neu.md','note',{ring:1,parent:'gt1',hidden:true}));
  pb.edges.push(E('w:neu','gt1','vault:neu.md','wikilink',1));
  pb.hidden=2;
  var db=sgDiff(gcy, pb, 'graph', {ausgeklappt:{}, gezeigt:gz, flash:false});
  var nn=gcy.getElementById('vault:nn.md'), mehr=gcy.nodes().filter(function(n){
    return n.id().indexOf('more:')===0; });
  out.gezeigt={da:nn.length, removed:db.removed,
    gleichePos:!!(nn.length && nn.position().x===posNn.x && nn.position().y===posNn.y),
    mehr:mehr.map(function(n){return n.id();}),
    mehrLabel:mehr.length?mehr[0].data('label'):'',
    neuDa:gcy.getElementById('vault:neu.md').length,
    zaehler:sgZaehler(pb,{},gz)};
  var pc=JSON.parse(JSON.stringify(pb));
  pc.nodes=pc.nodes.filter(function(n){ return n.id!=='vault:nn.md'; });
  pc.edges=pc.edges.filter(function(e){ return e.target!=='vault:nn.md'; });
  var dc=sgDiff(gcy, pc, 'graph', {ausgeklappt:{}, gezeigt:gz, flash:false});
  out.gezeigt.wegRemoved=dc.removed.indexOf('vault:nn.md')>=0;
  sgDiff(gcy, pb, 'graph', {ausgeklappt:{}, gezeigt:gz, flash:false});
  out.gezeigt.zurueckDa=gcy.getElementById('vault:nn.md').length;
  console.log(JSON.stringify(out));
})().catch(function(e){ console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def sg_run(tmp_path_factory):
    pruef = tmp_path_factory.mktemp("session-graph") / "session-graph-check.js"
    pruef.write_text(GRAPH_JS + "\n" + SESSION_GRAPH_JS + "\n" + SG_HARNESS,
                     encoding="utf-8")
    roh = _node(pruef, timeout=30)
    try:
        sg = json.loads(roh.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        sg = {}
    return SimpleNamespace(run=roh, sg=sg)


@pytest.fixture
def sg(sg_run):
    return sg_run.sg


def test_the_session_graph_js_harness_runs_without_throwing(sg_run):
    assert sg_run.run.returncode == 0, \
        (sg_run.run.stderr or sg_run.run.stdout)[-600:]


def test_sgelemente_draws_the_visible_nodes_and_no_hidden_one(sg):
    ids = sg.get("elIds", [])
    assert "vault:x.md" not in ids and all(i in ids for i in
        ("file:s.md", "t1", "b1", "b2", "a1", "vault:n.md", "vault:m.md")), \
        str(ids)


def test_one_plus_n_expander_per_parent_with_hidden_children(sg):
    ids = sg.get("elIds", [])
    assert ([i for i in ids if i.startswith("more:")] == ["more:vault:n.md"]
            and sg.get("moreLabel") == "+1"
            and "mehr" in (sg.get("moreClasses") or "")), \
        f'{ids} {sg.get("moreLabel")} {sg.get("moreClasses")}'


def test_ring_2_edges_carry_ring2_and_kante_plus_their_kind(sg):
    assert set((sg.get("ring2Edge") or "").split()) >= {"kante", "wikilink", "ring2"}, \
        str(sg.get("ring2Edge"))


def test_node_classes_carry_kind_ring_tag_and_grace(sg):
    assert (set((sg.get("entryCls") or "").split())
            >= {"sg-entry", "ring0", "tag-beschluss"}
            and set((sg.get("graceCls") or "").split())
            >= {"sg-note", "ring2", "gnade"}), \
        f'{sg.get("entryCls")} / {sg.get("graceCls")}'


def test_an_edge_to_a_hidden_node_is_not_drawn(sg):
    assert sg.get("hiddenEdgeDrawn") is False, str(sg.get("elIds"))


def test_no_element_carries_cytoscapes_compound_parent_key(sg):
    assert sg.get("cyParentKey") is False


def test_the_count_line_says_shown_and_hidden_in_german(sg):
    assert sg.get("zaehler") == "7 Knoten · 1 verborgen", str(sg.get("zaehler"))


def test_the_poll_asks_parts_g_1_with_the_encoded_token(sg):
    assert sg.get("abruf") == "parts?g=1&t=sg%20tok", str(sg.get("abruf"))


def test_the_first_draw_runs_fcose_exactly_once_and_flashes_nothing(sg):
    assert sg.get("ersteLaeufe") == ["fcose"] and sg.get("ersteNeu") == 0, \
        f'{sg.get("ersteLaeufe")} {sg.get("ersteNeu")}'


def test_the_count_element_is_filled(sg):
    assert sg.get("zaehlText") == "7 Knoten · 1 verborgen", str(sg.get("zaehlText"))


def test_an_unchanged_payload_skips_the_diff(sg):
    assert sg.get("gleichLetzt") is None and sg.get("gleichDiffs") == 1, \
        f'{sg.get("gleichLetzt")} {sg.get("gleichDiffs")}'


def test_the_diff_adds_exactly_the_new_topic_and_its_edge(sg):
    d2 = sg.get("diff2") or {}
    assert sorted(d2.get("added", [])) == ["b:t2", "t2"] and d2.get("removed") == [], \
        str(d2)


def test_cytoscape_is_constructed_once_across_all_polls(sg):
    assert sg.get("konstruktionen") == 1, str(sg.get("konstruktionen"))


def test_updates_never_run_a_layout(sg):
    assert sg.get("laeufeNachUpdates") == ["fcose"], str(sg.get("laeufeNachUpdates"))


def test_kept_nodes_keep_their_exact_position(sg):
    assert sg.get("keptGleich") is True


def test_the_new_topic_is_placed_next_to_the_node_it_hangs_on(sg):
    assert 0 < (sg.get("t2Naehe") or 0) < 200, str(sg.get("t2Naehe"))


def test_the_added_node_and_the_node_its_new_edge_touches_flash(sg):
    assert (sg.get("t2Neu") is True and sg.get("sessionNeu") is True
            and sg.get("t1Neu") is False), \
        f'{sg.get("t2Neu")} {sg.get("sessionNeu")} {sg.get("t1Neu")}'


def test_one_flash_one_2500_ms_timeout_class_gone_after_nothing_re_armed(sg):
    assert (sg.get("flashMs") == [2500] and sg.get("t2NeuDanach") is False
            and sg.get("zeitgeberDanach") == 0), \
        f'{sg.get("flashMs")} {sg.get("t2NeuDanach")} {sg.get("zeitgeberDanach")}'


def test_a_removed_node_removes_only_that_node_and_its_edge(sg):
    d3 = sg.get("diff3") or {}
    assert (sorted(d3.get("removed", [])) == ["a1", "e:a1"] and d3.get("added") == []
            and "a1" not in sg.get("nachEntfernen", [])
            and len(sg.get("nachEntfernen", [])) == 8), \
        f'{d3} {sg.get("nachEntfernen")}'


def test_expanding_a_parent_makes_its_hidden_children_and_their_edges_drawable(sg):
    assert (sg.get("xVorher") == 0 and sg.get("xNachher") == 1
            and sg.get("xKanteNachher") == 1 and sg.get("moreNachher") == 0), \
        (f'{sg.get("xVorher")} {sg.get("xNachher")} {sg.get("xKanteNachher")} '
         f'{sg.get("moreNachher")} {sg.get("ausklappDiff")}')


def test_kanban_one_column_per_tag_in_tags_order_topics_above_notes_below(sg):
    kp = sg.get("kanban") or {}
    try:
        xb = {kp["b1"]["x"], kp["b2"]["x"]}
        spalten = [kp["col:" + t]["x"] for t in
                   ("beschluss", "aufgabe", "frage", "idee", "recherche")]
        kanban_ok = (len(xb) == 1 and kp["a1"]["x"] not in xb
                     and kp["b1"]["x"] == kp["col:beschluss"]["x"]
                     and kp["a1"]["x"] == kp["col:aufgabe"]["x"]
                     and spalten == sorted(spalten) and len(set(spalten)) == 5
                     and kp["b1"]["y"] != kp["b2"]["y"]
                     and kp["t1"]["y"] < kp["col:beschluss"]["y"] < kp["b1"]["y"]
                     and kp["file:s.md"]["y"] < kp["t1"]["y"]
                     and kp["file:s.md"]["x"] == 500
                     and kp["vault:n.md"]["y"] > max(kp["b1"]["y"], kp["b2"]["y"]))
    except (KeyError, TypeError):
        kanban_ok = False
    assert kanban_ok, json.dumps(kp)[:600]


def test_tap_on_a_note_opens_its_href_with_the_token_in_a_new_tab(sg):
    geo = sg.get("geoeffnet") or []
    assert (len(geo) >= 1 and geo[0][0] == "vault/n.md?t=sg%20tok"
            and geo[0][1] == "_blank"), str(geo)


def test_tap_on_a_topic_posts_cothinker_topic_to_a_live_opener_only(sg):
    assert sg.get("gepostet") == [{"m": {"type": "cothinker-topic", "id": "t1",
                                         "title": "t1", "time": "10:00"},
                                   "o": "http://stage.test"}], \
        str(sg.get("gepostet"))


def test_tap_on_a_topic_without_a_live_opener_opens_the_protocol_directly(sg):
    # Closed, or missing entirely: anchored on #block-<id>.
    geo = sg.get("geoeffnet") or []
    assert (len(geo) == 3
            and geo[1] == ["file/s.md?t=sg%20tok#block-t1", "_blank"]
            and geo[2] == ["file/s.md?t=sg%20tok#block-t2", "_blank"]), str(geo)


def test_tap_on_the_expander_un_hides_the_children_at_once(sg):
    assert sg.get("tapAusklappen") == [0, 1], str(sg.get("tapAusklappen"))


def test_cursor_pointer_only_on_nodes_the_tap_handler_actually_acts_on(sg):
    assert (sg.get("cursorEntry") == "" and sg.get("cursorTopic") == "pointer"
            and sg.get("cursorAus") == "" and sg.get("cursorNote") == "pointer"
            and sg.get("cursorMehr") == "pointer"), \
        (f'{sg.get("cursorEntry")!r} {sg.get("cursorTopic")!r} '
         f'{sg.get("cursorAus")!r} {sg.get("cursorNote")!r} '
         f'{sg.get("cursorMehr")!r}')


def test_sgklickbar_agrees_entry_no_topic_note_expander_seed_yes(sg):
    assert sg.get("klickbar") == [False, True, True, True, True], \
        str(sg.get("klickbar"))


def test_kanban_toggle_stored_preset_run_heads_drawn_in_german_button_marked(sg):
    nk = sg.get("nachKanban") or {}
    assert (nk.get("gespeichert") == "kanban" and nk.get("laeufe") == ["preset"]
            and nk.get("kopf") == 1 and nk.get("kopfLabel") == "Beschluss"
            and nk.get("pressed") == "true" and nk.get("an") is True
            and nk.get("graphPressed") == "false"), str(nk)


def test_graph_toggle_stored_fcose_run_once_column_heads_gone(sg):
    ng = sg.get("nachGraph") or {}
    assert (ng.get("gespeichert") == "graph" and ng.get("laeufe") == ["fcose"]
            and ng.get("kopf") == 0), str(ng)


def test_the_buehne_link_is_a_plain_link_no_click_handler_on_it(sg):
    assert sg.get("buehneHandler") is False, str(sg.get("buehneHandler"))


def test_important_1_a_drawn_node_that_turns_hidden_true_stays_same_place(sg):
    gz = sg.get("gezeigt") or {}
    assert (gz.get("da") == 1 and gz.get("gleichePos") is True
            and gz.get("removed") == []), str(gz)


def test_important_1_no_expander_counts_a_drawn_node_a_new_hidden_one_waits(sg):
    gz = sg.get("gezeigt") or {}
    assert (gz.get("mehr") == ["more:gt1"] and gz.get("mehrLabel") == "+1"
            and gz.get("neuDa") == 0), str(gz)


def test_important_1_the_count_line_counts_only_what_is_really_not_shown(sg):
    gz = sg.get("gezeigt") or {}
    assert gz.get("zaehler") == "4 Knoten · 1 verborgen", str(gz.get("zaehler"))


def test_important_1_a_drawn_node_leaving_the_payload_is_removed_and_stays_gone(sg):
    gz = sg.get("gezeigt") or {}
    assert gz.get("wegRemoved") is True and gz.get("zurueckDa") == 0, str(gz)


def test_important_1_the_page_keeps_drawn_nodes_across_polls_sgstart(sg):
    assert sg.get("seiteBleibt") == {"da": 1, "gleich": True}, \
        str(sg.get("seiteBleibt"))


def test_the_stored_layout_is_read_a_blocked_localstorage_falls_back_to_graph(sg):
    assert sg.get("gelesen") == "kanban" and sg.get("gelesenKaputt") == "graph", \
        f'{sg.get("gelesen")} {sg.get("gelesenKaputt")}'


def test_without_cytoscape_the_tab_says_so_in_german(sg):
    assert sg.get("ohneCy") == "Graph nicht verfügbar.", str(sg.get("ohneCy"))


def test_kanban_live_five_topics_added_one_diff_at_a_time_keep_their_distance(sg):
    kt = sg.get("kTopics") or {}
    assert kt.get("anzahl") == 6 and kt.get("minAbst", 0) >= 100, str(kt)


def test_kanban_live_new_entries_in_the_deepest_column_never_land_on_a_strip_note(sg):
    ks = sg.get("kSpalte") or {}
    assert (ks.get("ueberlappt") == [] and ks.get("einX") is True
            and ks.get("xGleichKopf") is True), str(ks)


def test_kanban_live_a_strip_note_inserted_mid_list_takes_no_kept_notes_spot(sg):
    kst = sg.get("kStreifen") or {}
    assert kst.get("ueberlappt") == [] and kst.get("da") == 1, str(kst)


def test_unanchored_new_nodes_of_one_diff_fan_out_instead_of_stacking(sg):
    assert sg.get("unverankert") == [], str(sg.get("unverankert"))


def test_children_of_an_expanded_parent_stay_hidden_while_the_parent_is_hidden(sg):
    assert (sg.get("tiefZu") == ["r", "more:r", "more-e:r"]
            and sg.get("tiefZaehler") == "1 Knoten · 2 verborgen"
            and sg.get("tiefAuf") == ["r", "h", "hk"]), \
        f'{sg.get("tiefZu")} {sg.get("tiefAuf")} {sg.get("tiefZaehler")}'


def test_graph_mode_fits_once_only_when_an_added_node_lands_outside_the_view(sg):
    assert (sg.get("fitDrinnen") == 0 and sg.get("fitDraussen") == 1
            and sg.get("fitOhneNeues") == 1 and sg.get("fitAnimiert") == 1), \
        (f'{sg.get("fitDrinnen")} {sg.get("fitDraussen")} '
         f'{sg.get("fitOhneNeues")} {sg.get("fitAnimiert")}')


def test_kanban_fits_once_animated_when_a_new_entry_lands_below_the_view(sg):
    kf = sg.get("kanbanFit") or []
    assert (len(kf) == 2 and kf[0][0] <= 400 and kf[0][1] == 0
            and kf[1][0] > 400 and kf[1][1] == 1 and kf[1][2] == 1), str(kf)


def test_kanban_hides_note_to_note_edges_graph_shows_them_again(sg):
    nk2 = sg.get("nachKanban") or {}
    ng2 = sg.get("nachGraph") or {}
    assert (nk2.get("nnNurgraf") is True and nk2.get("nnVerdeckt") is True
            and nk2.get("tnVerdeckt") is False
            and ng2.get("nnVerdeckt") is False and ng2.get("nnDa") == 1), \
        f'{nk2} {ng2}'


def test_labels_page_font_stack_dark_label_background_drop_out_at_tiny_zoom(sg):
    st_ = sg.get("stil") or {}
    assert (st_.get("font") and st_.get("hintergrund") and st_.get("minZoom")
            and st_.get("verdeckt")), str(st_)


# ------------------------------------------------------- the announcement line
# It morphs once per id, flashes out, leaks nothing.
#
# The plain HARNESS above has no `createElement` and timers that never fire,
# and `#pending` is not one of its elements -- which is itself covered: the
# page script ran there without throwing. This one fakes just enough more:
# child lists, and timers the driver can see and fire, so "an animation
# started", "it stopped" and "the line was removed after the flash" are
# observed, not assumed.
PENDING_HARNESS = r"""
function CL(el){ this.el=el; this.set={}; }
CL.prototype = {
  add:function(c){ this.set[c]=true; },
  remove:function(c){ delete this.set[c]; },
  contains:function(c){ return !!this.set[c]; },
  toggle:function(c,on){ if(on) this.add(c); else this.remove(c); }
};
function El(id){ this.id=id; this._html=''; this._text=''; this.classList=new CL(this);
  this.attrs={}; this.children=[]; this.parentNode=null; }
El.prototype = {
  get innerHTML(){ return this._html; },
  set innerHTML(v){ this._html=String(v); },
  get textContent(){ return this._text + this.children.map(function(c){
    return c.textContent; }).join(''); },
  set textContent(v){ this._text=String(v); this.children=[]; },
  set className(v){ var s=this; String(v).split(/\s+/).forEach(function(c){
    if(c) s.classList.add(c); }); },
  get className(){ return Object.keys(this.classList.set).join(' '); },
  getAttribute:function(k){ return this.attrs[k]===undefined?null:this.attrs[k]; },
  setAttribute:function(k,v){ this.attrs[k]=String(v); },
  hasAttribute:function(k){ return this.attrs[k]!==undefined; },
  toggleAttribute:function(k,on){ if(on) this.attrs[k]=''; else delete this.attrs[k]; },
  appendChild:function(c){ c.parentNode=this; this.children.push(c); return c; },
  removeChild:function(c){ var i=this.children.indexOf(c);
    if(i>=0) this.children.splice(i,1); c.parentNode=null; return c; },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  get offsetWidth(){ return 1; }
};
var els={};
function el(id){ if(!els[id]) els[id]=new El(id); return els[id]; }
['verlauf','content','collapsed','links','punkt','clock','warn','ops-text',
 'mirror','decisions','notices','headline','pending'].forEach(el);
var listeners={};
global.document = {
  getElementById:function(id){ return els[id]||null; },
  createElement:function(){ return new El(''); },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  addEventListener:function(t,fn){ (listeners[t]=listeners[t]||[]).push(fn); },
  activeElement:null, hidden:false, body:{scrollHeight:1000}
};
var REDUCED = %REDUCED%;
global.window = {
  scrollY:0, innerHeight:800,
  getSelection:function(){ return {isCollapsed:true}; },
  scrollTo:function(){}, location:{search:'', origin:'http://buehne.example'}
};
if(REDUCED){
  global.window.matchMedia=function(q){
    return {matches: /prefers-reduced-motion:\s*reduce/.test(q)}; };
}
global.location = global.window.location;
// Timers the driver can see. The page's own two intervals (paint, tick)
// are registered before the first poll; everything after is ours.
var intervals={}, timeouts={}, nextId=1;
global.setInterval=function(fn,ms){ var i=nextId++; intervals[i]={fn:fn,ms:ms}; return i; };
global.clearInterval=function(i){ delete intervals[i]; };
global.setTimeout=function(fn,ms){ var i=nextId++; timeouts[i]={fn:fn,ms:ms}; return i; };
global.clearTimeout=function(i){ delete timeouts[i]; };
function activeIntervals(){ return Object.keys(intervals).map(Number); }
function runTimeouts(){ var ks=Object.keys(timeouts); ks.forEach(function(k){
  var t=timeouts[k]; delete timeouts[k]; t.fn(); }); }

var payloads = JSON.parse(process.argv[2]);
var shot = 0;
global.fetch = async function(){
  var p = payloads[Math.min(shot, payloads.length-1)];
  shot++;
  return { json: async function(){ return p; } };
};

%SCRIPT%

process.on('uncaughtException', function(e){
  console.error('UNCAUGHT', (e && e.stack) || e); process.exit(3); });
var pend=els['pending'];
function lines(){ return pend.children.map(function(c){
  return {id:c.getAttribute('data-id'), text:c.textContent,
          glyph:c.children[0] ? c.children[0].textContent : '',
          morph:c.classList.contains('morphing'), resting:c.classList.contains('resting'),
          leaving:c.classList.contains('leaving')}; }); }
var out={};
(async function(){
  var base=activeIntervals();
  function ours(){ return activeIntervals().filter(function(i){
    return base.indexOf(i)<0; }); }
  await tickOnce();                                    // P1 announced
  out.first=lines(); out.firstTimers=ours().length;
  out.morphMs=ours().length ? intervals[ours()[0]].ms : 0;
  out.firstEl=pend.children[0];
  // Run the morph to its end; record the frames it showed.
  var frames=[], guard=0;
  while(ours().length && guard++<500){
    ours().forEach(function(i){ intervals[i] && intervals[i].fn(); });
    frames.push(pend.children[0] ? pend.children[0].children[0].textContent : '');
  }
  out.frames=frames.length; out.midFrame=frames[Math.floor(frames.length/3)]||'';
  out.frameTexts=frames;
  out.afterMorph=lines(); out.afterMorphTimers=ours().length;
  await tickOnce();                                    // the same list again
  out.second=lines(); out.secondTimers=ours().length;
  out.sameElement=(pend.children[0]===out.firstEl);
  delete out.firstEl;
  await tickOnce();                                    // P1 arrived
  out.leaving=lines();
  // The flash's own removal timer, told apart from the pulse dot's by delay.
  out.leavingTimeouts=Object.keys(timeouts).filter(function(k){
    return timeouts[k].ms===400; }).length;
  runTimeouts();
  out.afterFlash=lines();
  await tickOnce();                                    // P2, then gone mid-morph
  out.p2=lines(); out.p2Timers=ours().length;
  await tickOnce();
  out.p2GoneTimers=ours().length;
  runTimeouts();
  await tickOnce();                                    // four at once
  out.four=lines();
  out.allText=pend.textContent;
  // Settle the three morphs so the revive below starts from resting lines.
  var g2=0;
  while(ours().length && g2++<500){
    ours().forEach(function(i){ intervals[i] && intervals[i].fn(); }); }
  // The revive branch: P12 leaves, and comes back inside its flash.
  var p12=pend.children[2];
  await tickOnce();                                    // P10, P11
  out.reviveLeaving=lines();
  await tickOnce();                                    // P10, P11, P12 again
  out.revived=lines(); out.reviveSame=(pend.children[2]===p12);
  out.reviveFlashTimers=Object.keys(timeouts).filter(function(k){
    return timeouts[k].ms===400; }).length;
  out.reviveTimers=ours().length;
  runTimeouts();
  out.afterReviveFlush=lines();
  // The cap counts flashing lines too.
  await tickOnce();                                    // P10, P11 (P12 flashing)
  await tickOnce();                                    // P10, P11, P13
  out.capMixed=lines();
  await tickOnce();                                    // P20, P21, P22 (long)
  out.capAll=lines();
  // Every morph interval: its tick and how often it fired until done.
  var fired={};
  ours().forEach(function(i){ fired[i]={ms:intervals[i].ms, n:0}; });
  var g3=0;
  while(ours().length && g3++<500){
    ours().forEach(function(i){ if(intervals[i]){ fired[i].n++; intervals[i].fn(); } }); }
  out.capMorphs=Object.keys(fired).map(function(k){ return fired[k]; });
  out.capSettled=lines();
  console.log(JSON.stringify(out));
})();
"""

SENTENCE_P = "Wir verdoppeln heimlich das Budget fuer Ortsgedaechtnis"
LONG = ("Recherche zu Ortsgedaechtnis und Stadtarchiv, Teil zwei "
        "von drei")[:60]


@pytest.fixture(scope="module")
def pending(tmp_path_factory):
    c = Clock()
    pstate = StageState(now=c.now)
    projekt = tmp_path_factory.mktemp("cothinker-pending")

    def publish(extra_pending: list | None = None) -> None:
        content = dict(pstate.content())
        content["status"] = STATUS
        # A synthetic session sentence planted where a careless writer might
        # put one: next to the entry, and in fields the page must never read.
        content["pending"] = [dict(p, text=SENTENCE_P, quote=SENTENCE_P)
                              for p in content.get("pending", [])]
        if extra_pending is not None:
            content["pending"] = extra_pending
        content["mirror"] = ""
        (projekt / "buehne.json").write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    server, port = _start_stage(projekt)
    ppayloads: list[dict] = []
    page = ""

    def fetch() -> dict:
        return _parts(port) or {}

    try:
        first_id = pstate.announce("main", "Auftrag angenommen")
        publish()
        ppayloads.append(fetch())                  # P1
        ppayloads.append(fetch())                  # P1 again
        pstate.settle(first_id)
        publish()
        ppayloads.append(fetch())                  # gone
        second_id = pstate.announce("research", "Recherche zu Ortsgedaechtnis")
        publish()
        ppayloads.append(fetch())                  # P2
        pstate.settle(second_id)
        publish()
        ppayloads.append(fetch())                  # gone before its morph ended
        # Four in the file (a hand-edited one): the page must show three.
        publish([{"kind": "topic", "label": f"Thema {i}", "id": f"P{i}"}
                 for i in range(10, 14)])
        ppayloads.append(fetch())
        # The rest is client-side reconciliation only: the same payload with
        # a different announcement list, no second trip through the server.
        long_entry_id = "P22"

        def with_pending(ids: list[str], long_label: str = "") -> dict:
            d = dict(ppayloads[5])
            d["pending"] = [{"id": i, "kind": "topic",
                             "label": (long_label if i == long_entry_id
                                       else f"Thema {i[1:]}")}
                            for i in ids]
            return d

        ppayloads.append(with_pending(["P10", "P11"]))
        ppayloads.append(with_pending(["P10", "P11", "P12"]))
        ppayloads.append(with_pending(["P10", "P11"]))
        ppayloads.append(with_pending(["P10", "P11", "P13"]))
        ppayloads.append(with_pending(["P20", "P21", "P22"], LONG))
        page = _get(port) or ""
    finally:
        _stop(server)

    ns = SimpleNamespace(payloads=ppayloads, page=page, skript=_script_of(page),
                         runs={}, v={}, r={})
    if ns.skript is not None and ppayloads:
        body = _body(ns.skript)
        for reduced in (False, True):
            runner = projekt / f"run-pending-{int(reduced)}.js"
            runner.write_text(
                PENDING_HARNESS.replace("%SCRIPT%", body)
                               .replace("%REDUCED%",
                                        "true" if reduced else "false"),
                encoding="utf-8")
            run = _node(runner, json.dumps(ppayloads))
            ns.runs[reduced] = run
            result = _last_json(run) if run.returncode == 0 else {}
            if reduced:
                ns.r = result
            else:
                ns.v = result
    return ns


def test_six_parts_polls_for_the_announcement_answered_plus_five_variants(pending):
    assert len(pending.payloads) == 11 and all(pending.payloads[:6]), \
        str(len(pending.payloads))


def test_everything_the_page_received_for_pending_is_id_kind_label_only(pending):
    all_pending = [e for p in pending.payloads for e in p.get("pending", [])]
    assert bool(all_pending) and all(set(e) == {"id", "kind", "label"}
                                     for e in all_pending), \
        json.dumps(all_pending, ensure_ascii=False)[:300]


def test_the_planted_session_sentence_is_nowhere_in_the_pending_payload(pending):
    all_pending = [e for p in pending.payloads for e in p.get("pending", [])]
    assert SENTENCE_P not in json.dumps(all_pending, ensure_ascii=False)


def test_the_fingerprint_does_not_move_while_only_pending_changes(pending):
    assert len({p.get("mark") for p in pending.payloads[:5]}) == 1, \
        str([p.get("mark") for p in pending.payloads])


def test_the_page_carries_the_announcement_container(pending):
    assert '<div id="pending" aria-live="polite"></div>' in pending.page


@pytest.mark.parametrize("reduced", [False, True])
def test_the_pending_harness_runs_without_throwing(pending, reduced):
    assert reduced in pending.runs
    assert pending.runs[reduced].returncode == 0, _out(pending.runs[reduced])


def test_the_page_carries_its_script_pending_run(pending):
    assert pending.skript is not None


def test_after_the_first_poll_pending_holds_one_line_for_p1(pending):
    first = pending.v["first"]
    assert [l["id"] for l in first] == ["P1"], str(first)


def test_and_its_morph_has_started_animating_class_one_live_timer(pending):
    v = pending.v
    assert v["first"][0]["morph"] and v["firstTimers"] == 1, str(v)[:300]


def test_the_morph_shows_glyphs_on_the_way_not_the_label_straight_away(pending):
    v = pending.v
    assert v["midFrame"] != "Auftrag angenommen"[:len(v["midFrame"])], v["midFrame"]


def test_the_morph_settles_within_about_1_2_s_frames_x_interval(pending):
    v = pending.v
    assert 0 < v["frames"] * v["morphMs"] <= 1250, \
        f'{v["frames"]} frames x {v["morphMs"]} ms'


def test_the_morph_ends_on_the_label_pulses_and_stops_its_timer(pending):
    v = pending.v
    am = v["afterMorph"][0]
    assert (am["glyph"] == "Auftrag angenommen" and am["resting"]
            and not am["morph"] and v["afterMorphTimers"] == 0), str(v)[:300]


def test_a_second_poll_with_the_same_list_keeps_the_same_element(pending):
    v = pending.v
    assert (v["sameElement"] is True and v["secondTimers"] == 0
            and v["second"][0]["resting"]), str(v["second"])


def test_p1_gone_the_line_flashes_end_class_and_is_still_there(pending):
    v = pending.v
    assert ([l["id"] for l in v["leaving"]] == ["P1"]
            and v["leaving"][0]["leaving"] and v["leavingTimeouts"] >= 1), \
        str(v["leaving"])


def test_and_is_removed_after_the_flash(pending):
    assert pending.v["afterFlash"] == [], str(pending.v["afterFlash"])


def test_p2_starts_its_own_morph(pending):
    v = pending.v
    assert [l["id"] for l in v["p2"]] == ["P2"] and v["p2Timers"] == 1, str(v["p2"])


def test_removed_mid_morph_its_timer_is_cleared_no_leak(pending):
    assert pending.v["p2GoneTimers"] == 0, str(pending.v["p2GoneTimers"])


def test_four_in_the_file_three_lines_on_the_page(pending):
    v = pending.v
    assert [l["id"] for l in v["four"]] == ["P10", "P11", "P12"], str(v["four"])


def test_the_planted_sentence_never_reaches_the_dom(pending):
    v = pending.v
    assert SENTENCE_P not in v["allText"], v["allText"][:200]


# Halfwidth Katakana and block elements only -- a full-width glyph is two
# columns wide and makes the edge jump.
def _allowed_glyph(ch: str) -> bool:
    o = ord(ch)
    return 0xFF66 <= o <= 0xFF9D or 0x2580 <= o <= 0x259F


def test_the_morph_glyphs_are_halfwidth_katakana_or_block_elements_only(pending):
    foreign = {ch for f in pending.v["frameTexts"] for ch in f
               if ch not in "Auftrag angenommen"}
    assert bool(foreign) and all(_allowed_glyph(ch) for ch in foreign), \
        repr(sorted(foreign))[:200]


def test_and_no_frame_is_longer_than_the_label_the_edge_does_not_jump(pending):
    assert all(len(f) <= len("Auftrag angenommen")
               for f in pending.v["frameTexts"]), \
        str([len(f) for f in pending.v["frameTexts"]])


# No tick under 40 ms, the whole morph <= ~1.2 s -- also for a 60-character
# label, which gets fewer, bigger steps.
def test_the_morph_tick_is_at_least_40_ms(pending):
    assert pending.v["morphMs"] >= 40, str(pending.v["morphMs"])


def test_three_morphs_ran_for_the_three_new_lines_one_of_them_long(pending):
    assert len(pending.v["capMorphs"]) == 3, str(pending.v["capMorphs"])


def test_every_morph_ticks_at_40_ms_or_more_and_finishes_within_about_1_2_s(pending):
    # The 60-character one included.
    runs = pending.v["capMorphs"]
    assert all(m["ms"] >= 40 and 0 < m["ms"] * m["n"] <= 1250 for m in runs), \
        str(runs)


def test_the_long_label_settles_whole(pending):
    v = pending.v
    assert any(l["id"] == "P22" and l["glyph"] == LONG and l["resting"]
               for l in v["capSettled"]), str(v["capSettled"])[:300]


# The revive branch -- back inside the flash.
def test_p12_leaving_it_flashes_the_other_two_stay(pending):
    v = pending.v
    assert ([(l["id"], l["leaving"]) for l in v["reviveLeaving"]]
            == [("P10", False), ("P11", False), ("P12", True)]), \
        str(v["reviveLeaving"])


def test_p12_back_within_its_flash_the_same_element_resting_on_its_whole_label(pending):
    v = pending.v
    rv = v["revived"]
    assert (v["reviveSame"] is True and rv[2]["id"] == "P12"
            and not rv[2]["leaving"] and rv[2]["resting"]
            and rv[2]["glyph"] == "Thema 12"), str(rv)


def test_its_removal_timer_is_cancelled_and_no_second_morph_starts(pending):
    v = pending.v
    assert v["reviveFlashTimers"] == 0 and v["reviveTimers"] == 0, \
        f'{v["reviveFlashTimers"]} / {v["reviveTimers"]}'


def test_and_it_is_still_there_once_all_pending_timeouts_have_run(pending):
    v = pending.v
    assert [l["id"] for l in v["afterReviveFlush"]] == ["P10", "P11", "P12"], \
        str(v["afterReviveFlush"])


# Never more than three lines, flashing ones included.
def test_two_live_one_flashing_one_new_the_flashing_one_goes_at_once(pending):
    v = pending.v
    assert ([(l["id"], l["leaving"]) for l in v["capMixed"]]
            == [("P10", False), ("P11", False), ("P13", False)]), \
        str(v["capMixed"])


def test_three_live_replaced_by_three_new_exactly_the_three_new_lines(pending):
    v = pending.v
    assert ([(l["id"], l["leaving"]) for l in v["capAll"]]
            == [("P20", False), ("P21", False), ("P22", False)]), \
        str(v["capAll"])


def test_reduced_motion_the_label_stands_at_once_with_dots(pending):
    r = pending.r
    assert (r["first"] and r["first"][0]["glyph"] == "Auftrag angenommen …"
            and not r["first"][0]["morph"] and r["firstTimers"] == 0), \
        str(r["first"])


def test_reduced_motion_gone_means_gone_no_flash(pending):
    r = pending.r
    assert r["leaving"] == [] and r["leavingTimeouts"] == 0, str(r["leaving"])


# ------------------------------------------------- the speaker queue / share
# Issue #37: `#queue`/`#share` ride the fingerprint (unlike `#agenda`/
# `#pending`), so `queueApply()`/`shareApply()` only run from inside
# `tick()`'s mark-gated block -- this harness checks the markup they build
# from a REAL `queue_part()`/`share_part()` payload, straight off a running
# `stage.py`, not a hand-written stand-in for it.
QUEUE_SHARE_HARNESS = r"""
// --- the same small DOM stand-in as the history/link harness above ------
function El(id){ this.id=id; this._html=''; this._text=''; this.classList=new CL(this);
  this.attrs={}; }
El.prototype = {
  get innerHTML(){ return this._html; },
  set innerHTML(v){ this._html=String(v); },
  get textContent(){ return this._text; },
  set textContent(v){ this._text=String(v); },
  getAttribute:function(k){ return this.attrs[k]===undefined?null:this.attrs[k]; },
  setAttribute:function(k,v){ this.attrs[k]=v; },
  hasAttribute:function(k){ return this.attrs[k]!==undefined; },
  toggleAttribute:function(k,on){
    if(on) this.attrs[k]=''; else delete this.attrs[k];
    return on;
  },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  get offsetWidth(){ return 1; }
};
function CL(el){ this.el=el; this.set={}; }
CL.prototype = {
  add:function(c){ this.set[c]=true; },
  remove:function(c){ delete this.set[c]; },
  contains:function(c){ return !!this.set[c]; },
  toggle:function(c,on){ if(on) this.add(c); else this.remove(c); }
};

var els={};
function el(id){ if(!els[id]) els[id]=new El(id); return els[id]; }
['verlauf','content','collapsed','links','punkt','clock','warn','ops-text',
 'mirror','decisions','notices','headline','queue','share',
 'protokoll-col','protokoll-content','protokoll-toggle'].forEach(el);

var listeners={};
global.document = {
  getElementById:function(id){ return els[id]||null; },
  querySelector:function(){ return null; },
  querySelectorAll:function(){ return []; },
  addEventListener:function(t,fn){ (listeners[t]=listeners[t]||[]).push(fn); },
  activeElement:null, hidden:false, body:{scrollHeight:1000}
};
global.window = {
  scrollY:0, innerHeight:800,
  getSelection:function(){ return {isCollapsed:true}; },
  scrollTo:function(){},
  location:{search:'', origin:'http://buehne.example'}
};
global.location = global.window.location;
global.setInterval=function(){ return 0; };
global.setTimeout=function(){ return 0; };

var payloads = JSON.parse(process.argv[2]);
var shot = 0;
global.fetch = async function(){
  var p = payloads[Math.min(shot, payloads.length-1)];
  shot++;
  return { json: async function(){ return p; } };
};

%SCRIPT%

process.on('uncaughtException', function(e){
  console.error('UNCAUGHT', (e && e.stack) || e); process.exit(3); });

var out={};
(async function(){
  await tickOnce();                        // P1: no queue/share key at all
  out.hiddenQueue=els['queue'].innerHTML;
  out.hiddenShare=els['share'].innerHTML;
  await tickOnce();                        // P2: both keys fed
  out.queueHtml=els['queue'].innerHTML;
  out.shareHtml=els['share'].innerHTML;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def queue_share(tmp_path_factory):
    """A real `stage.py`, polled twice: once before `queue`/`share` exist
    in `buehne.json`, once after -- with a markup-bearing name in each, the
    same posture as the `pending` fixture's planted session sentence
    above, to check escaping end to end rather than trust `queue_part()`'s
    own unit tests alone."""
    projekt = tmp_path_factory.mktemp("cothinker-queue-share")

    def publish(extra: dict | None) -> None:
        content = {"version": 2, "status": STATUS, "mirror": "",
                   "headline": "", "main": {"markdown": ""},
                   "decisions": [], "notices": [], "links": []}
        if extra:
            content.update(extra)
        (projekt / "buehne.json").write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    server, port = _start_stage(projekt)
    payloads: list[dict] = []
    page = ""
    try:
        publish(None)
        payloads.append(_parts(port))
        publish({
            "queue": {"mode": "round_robin", "speaking": ["<b>Eva</b>"],
                      "waiting": ["Finn", "Greta"], "round": 2},
            "share": {"shares": [{"person": "<i>Hugo</i>", "share": 0.6},
                                  {"person": "Ida", "share": 0.4}],
                      "total_words": 500},
        })
        payloads.append(_parts(port))
        page = _get(port) or ""
    finally:
        _stop(server)

    ns = SimpleNamespace(payloads=payloads, page=page, skript=_script_of(page),
                         out={})
    if ns.skript is not None and all(payloads):
        body = _body(ns.skript)
        runner = projekt / "run-queue-share.js"
        runner.write_text(QUEUE_SHARE_HARNESS.replace("%SCRIPT%", body),
                          encoding="utf-8")
        run = _node(runner, json.dumps(payloads))
        if run.returncode == 0:
            ns.out = _last_json(run)
    return ns


def test_queue_and_share_start_hidden_until_the_key_arrives(queue_share):
    assert queue_share.out.get("hiddenQueue") == "", queue_share.out
    assert queue_share.out.get("hiddenShare") == "", queue_share.out


def test_queue_waiting_renders_in_the_order_the_file_gave_it(queue_share):
    html = queue_share.out.get("queueHtml", "")
    assert html.index("Finn") < html.index("Greta"), html


def test_queue_and_share_names_are_escaped_not_double_escaped(queue_share):
    queue_html = queue_share.out.get("queueHtml", "")
    share_html = queue_share.out.get("shareHtml", "")
    assert "&lt;b&gt;Eva&lt;/b&gt;" in queue_html, queue_html
    assert "&lt;i&gt;Hugo&lt;/i&gt;" in share_html, share_html
    assert "&amp;lt;" not in queue_html + share_html, queue_html + share_html


def test_share_bar_widths_reflect_the_normalised_shares(queue_share):
    html = queue_share.out.get("shareHtml", "")
    assert "width:60%" in html and "width:40%" in html, html
