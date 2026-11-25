// CoThinker — the stage's live transcript side panel (issue #181)
// Copyright (C) 2026 Birk Schmithuesen
//
// This program is free software under the GNU Affero General Public
// License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
//
// The stage's FIRST panel built against the surface contract (ADR 0019)
// instead of reading buehne.json: GET /snapshot once, then `view-intent`
// SSE events from GET /events, same shape as
// surfaces/contract/examples/minimal/index.html (query param `?api=`,
// `JSON.parse(event.data)`). Written fresh for the contract events --
// not a port of any predecessor app's code, only of its on-screen
// behaviour (the issue's own spec).
//
// `tpMerge`/`tpRows`/`tpRenderRows` are pure (no fetch, no DOM globals
// beyond the `container` they are handed) so a plain node test can drive
// them against a mock element -- `tpBoot` is the only part that touches
// `window`/`document`/`fetch`/`EventSource`, and it waits for
// DOMContentLoaded so `window.STAGE_TOKEN` (set by the page's own inline
// script, stage/stage.py's SCRIPT) is already there however the two
// <script> tags happen to be ordered.
//
// State shape: `{lines: TranscriptLine[], partials: {[turn_id]: {speaker,
// text, startedAtMs}}}`. `lines` is the LATEST `transcript:recent`
// payload verbatim (oldest first, already capped at 10 by the
// live-transcript block); `partials` holds every currently open
// `transcript:partial` turn (cleared -- key removed -- the moment its
// `text` arrives empty).

var TP_MAX_LINES = 10;
var TP_DEFAULT_API = "/api/";
var TP_MAX_AGE_S = 120;
var TP_MIN_OPACITY = 0.25;
var TP_TENTATIVE_OPACITY = 0.65;

function tpNewState() {
  return { lines: [], partials: {} };
}

// One step of the merge: `intent` is a parsed `view-intent` SSE payload
// (or the matching field of a `/snapshot` response) -- `{kind, ...}`.
// Mutates and returns `state`.
function tpMerge(state, intent) {
  if (!intent) return state;
  if (intent.kind === "transcript:recent") {
    state.lines = intent.lines || [];
  } else if (intent.kind === "transcript_partial") {
    var turnId = intent.turn_id;
    if (!turnId) return state;
    if (!intent.text) {
      delete state.partials[turnId];
    } else {
      state.partials[turnId] = {
        speaker: intent.speaker || "",
        text: intent.text,
        startedAtMs: Date.parse(intent.started_at) || 0,
      };
    }
  }
  return state;
}

// A finished `TranscriptLine`'s `started_at` carries the same ISO wall-clock
// label a partial's does.
function tpLineStartedAtMs(line) {
  return Date.parse(line.started_at) || 0;
}

// Newest first, capped at TP_MAX_LINES, open partials always on top (the
// ones currently "in progress" are the newest thing happening) -- each
// row carries the opacity it should render at: 1.0 at the top down to
// TP_MIN_OPACITY at position TP_MAX_LINES-1, or 0 once older than
// TP_MAX_AGE_S regardless of position.
function tpRows(state, nowMs) {
  var openTurnIds = Object.keys(state.partials);
  openTurnIds.sort(function (a, b) {
    return state.partials[b].startedAtMs - state.partials[a].startedAtMs;
  });
  var open = openTurnIds.map(function (turnId) {
    var p = state.partials[turnId];
    return { speaker: p.speaker, text: p.text, confirmed: false, startedAtMs: p.startedAtMs };
  });
  var confirmed = state.lines.slice().reverse().map(function (line) {
    return { speaker: line.speaker, text: line.text, confirmed: true,
             startedAtMs: tpLineStartedAtMs(line) };
  });
  var combined = open.concat(confirmed).slice(0, TP_MAX_LINES);
  return combined.map(function (row, index) {
    var ageS = (nowMs - row.startedAtMs) / 1000;
    var positional = 1.0 - (index / (TP_MAX_LINES - 1)) * (1.0 - TP_MIN_OPACITY);
    var opacity = ageS > TP_MAX_AGE_S ? 0 : positional;
    return { speaker: row.speaker, text: row.text, confirmed: row.confirmed, opacity: opacity };
  });
}

function tpEscape(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Two spans per row (confirmed + tentative), exactly one of them filled --
// the porting instruction's "confirmed/tentative spans" structure, kept
// even though this contract never carries both halves of one row at once.
function tpRowHtml(row) {
  var confirmedSpan = '<span class="confirmed">' +
    (row.confirmed ? tpEscape(row.text) : "") + "</span>";
  var tentativeSpan = '<span class="tentative" style="opacity:' + TP_TENTATIVE_OPACITY + '">' +
    (row.confirmed ? "" : tpEscape(row.text)) + "</span>";
  return '<div class="transcript-line" style="opacity:' + row.opacity + '">' +
    "<b>" + tpEscape(row.speaker || "") + "</b> " + confirmedSpan + tentativeSpan + "</div>";
}

function tpRenderRows(container, rows) {
  container.innerHTML = rows.map(tpRowHtml).join("");
}

function tpRender(container, state, nowMs) {
  tpRenderRows(container, tpRows(state, nowMs));
}

// --- wiring (not exercised by the node test; needs a real page) ----------
function tpBoot() {
  var container = document.getElementById("transcript");
  if (!container) return;
  container.style.overflow = "hidden";       // no scrolling (the issue's spec)

  // The contract lives under /api/ behind the event server's proxy (same
  // path the stage's own listen toggle posts to); `?api=` overrides it for
  // a contract on another origin (tests, a local fixture server).
  var api = new URLSearchParams(window.location.search).get("api") || TP_DEFAULT_API;
  var token = window.STAGE_TOKEN;
  function withToken(u) {
    if (!token || /[?&]t=/.test(u)) return u;
    return u + (u.indexOf("?") < 0 ? "?" : "&") + "t=" + encodeURIComponent(token);
  }

  var state = tpNewState();
  function onIntent(intent) {
    tpMerge(state, intent);
    tpRender(container, state, Date.now());
  }

  fetch(withToken(api + "snapshot"))
    .then(function (response) { return response.json(); })
    .then(function (snapshot) {
      if (snapshot && snapshot.transcript_recent) {
        tpMerge(state, { kind: "transcript:recent", lines: snapshot.transcript_recent });
        tpRender(container, state, Date.now());
      }
    });

  var events = new EventSource(withToken(api + "events"));
  events.addEventListener("view-intent", function (event) {
    onIntent(JSON.parse(event.data));
  });
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tpBoot);
  } else {
    tpBoot();
  }
}
