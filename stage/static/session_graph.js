// CoThinker - the graph tab: the running session as a graph (MM-J)
// Copyright (C) 2026 Birk Schmithuesen
//
// This program is free software under the GNU Affero General Public
// License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
//
// DEFAULT LAYOUT: `graph`, not `kanban`. Today's protocol material fills
// only three of the five tag columns (`idee` and `recherche` arrive with
// MM-C), the card's primary purpose is the live vault graph, and a session
// starts with zero entries -- the kanban would open as five empty columns
// while the graph already shows the session and its first topic. The
// choice a viewer makes is kept in localStorage['cothinker.graph.layout'].
//
// THE ROOM IS READING. The page polls `parts?g=1` every 3 s and DIFFS:
// cytoscape is created once, vanished elements are removed, new ones are
// added next to what they hang on, kept nodes never move, and no update
// runs a global layout (AGENTS.md: add, don't restructure). A global
// layout runs only on the first draw and on a toggle -- a user action.
//
// Split like graph.js: `sgElemente`, `sgKanbanPositionen`, `sgDiff`,
// `sgZaehler`, `sgAusklappen`, `sgZiel` and `sgLayoutLesen` are testable
// under plain node; `sgStart` needs a page. `sgDiff` touches cytoscape
// only through this subset, so the fake cy in tests/test_stage_js.py can
// stay small and honest (SG_CY_API):
//   cy.add(spec | [spec]), cy.getElementById(id) (.length 0 when absent),
//   cy.nodes() / cy.edges() (.forEach), cy.layout(opts).run(),
//   cy.on('tap' | 'mouseover' | 'mouseout', 'node', fn), cy.fit(eles, padding),
//   cy.extent() ({x1, y1, x2, y2}, the visible model box),
//   cy.elements(), cy.animate({fit: {eles, padding}, duration});
//   ele.id(), ele.data(key | obj), ele.position() / ele.position({x, y}),
//   ele.addClass / removeClass / hasClass, ele.classes(str), ele.remove().
//
// Cytoscape 3.x has no 'cursor' style property -- nodes are canvas pixels,
// not DOM elements a CSS rule can address. The pointer therefore comes from
// `el.style.cursor` (the container `sgStart(el)` already has in hand), set
// on 'mouseover'/'mouseout' and nowhere else.

var SG_TAGS = ['beschluss', 'aufgabe', 'frage', 'idee', 'recherche'];
var SG_KOEPFE = { beschluss: 'Beschluss', aufgabe: 'Aufgabe', frage: 'Frage',
                  idee: 'Idee', recherche: 'Recherche' };
var SG_LAYOUT_KEY = 'cothinker.graph.layout';
var SG_FLASH_MS = 2500;
var SG_POLL_MS = 3000;
var SG_ABSTAND = 90;          // distance of a new node from its anchor

function sgToken(s) {
  // Class names come out of a payload nobody trusts: one token, no spaces.
  return String(s == null ? '' : s).replace(/[^A-Za-z0-9_-]/g, '');
}

function sgSichtbar(graph, ausgeklappt, gezeigt) {
  // Shown: not hidden, drawn on this page before (`gezeigt`), or hidden
  // under an expanded parent that is itself shown. Repeated until stable,
  // so the payload's order does not matter.
  //
  // `gezeigt` exists because the server's `hidden` is a cap by insertion
  // order: every new topic or entry pushes the last notes over it. Taken
  // literally, a note the room has been looking at would vanish on the
  // next poll -- "kept nodes stay where they are" means they stay at all.
  // So the cap only restrains what has not been drawn yet.
  var auf = ausgeklappt || {}, war = gezeigt || {}, zeigen = {};
  var knoten = graph.nodes || [], neu = true;
  knoten.forEach(function (n) { if (!n.hidden || war[n.id]) zeigen[n.id] = true; });
  while (neu) {
    neu = false;
    knoten.forEach(function (n) {
      if (!zeigen[n.id] && auf[n.parent] && zeigen[n.parent]) { zeigen[n.id] = true; neu = true; }
    });
  }
  return zeigen;
}

function sgVerborgen(graph, zeigen) {
  // parent id -> number of its children still hidden (parent shown only:
  // an expander hangs on something visible, deeper levels open in turn).
  var je = {};
  (graph.nodes || []).forEach(function (n) {
    if (!zeigen[n.id] && n.parent && zeigen[n.parent]) je[n.parent] = (je[n.parent] || 0) + 1;
  });
  return je;
}

function sgElemente(graph, ausgeklappt, gezeigt) {
  var zeigen = sgSichtbar(graph, ausgeklappt, gezeigt), elemente = [];
  (graph.nodes || []).forEach(function (n) {
    if (!zeigen[n.id]) return;
    // NOT `parent` in data: that key makes cytoscape nest compound nodes.
    elemente.push({ group: 'nodes',
      data: { id: n.id, label: n.label || '', kind: n.kind || '', href: n.href || '',
              time: n.time || '', tag: n.tag || '', eltern: n.parent || '' },
      classes: 'sg-' + sgToken(n.kind) + ' ring' + sgToken(n.ring || 0) +
               (n.tag ? ' tag-' + sgToken(n.tag) : '') + (n.grace ? ' gnade' : '') });
  });
  // Only edges whose both ends are shown: the payload also carries the
  // edges of hidden nodes, so that expanding finds them.
  var art = {};
  (graph.nodes || []).forEach(function (n) { art[n.id] = n.kind; });
  (graph.edges || []).forEach(function (e) {
    if (!zeigen[e.source] || !zeigen[e.target]) return;
    // Note-to-note edges only mean something in the graph: in the kanban
    // all notes share a strip and those edges would lie on top of it.
    var nurGraf = art[e.source] === 'note' && art[e.target] === 'note';
    elemente.push({ group: 'edges',
      data: { id: e.id, source: e.source, target: e.target },
      classes: 'kante ' + sgToken(e.kind) + ' ring' + sgToken(e.ring || 0) +
               (nurGraf ? ' nurgraf' : '') });
  });
  var je = sgVerborgen(graph, zeigen);
  Object.keys(je).forEach(function (p) {
    elemente.push({ group: 'nodes',
      data: { id: 'more:' + p, label: '+' + je[p], kind: 'mehr', href: '',
              time: '', tag: '', eltern: p }, classes: 'mehr' });
    elemente.push({ group: 'edges',
      data: { id: 'more-e:' + p, source: p, target: 'more:' + p },
      classes: 'kante mehr' });
  });
  return elemente;
}

function sgKoepfe() {
  return SG_TAGS.map(function (t) {
    return { group: 'nodes', data: { id: 'col:' + t, label: SG_KOEPFE[t], kind: 'kopf',
             href: '', time: '', tag: t, eltern: '' }, classes: 'kopf' };
  });
}

function sgKanbanPositionen(graph, breite, ausgeklappt, gezeigt) {
  // Session centred on top, topics in a row (oldest left), one column per
  // tag in SG_TAGS order with its German head, notes in a strip below.
  var b = breite > 0 ? breite : 1000, spalte = b / SG_TAGS.length;
  var pos = {}, zeigen = sgSichtbar(graph, ausgeklappt, gezeigt);
  var themen = [], stapel = {}, streifen = [];
  SG_TAGS.forEach(function (t, i) {
    pos['col:' + t] = { x: spalte * (i + 0.5), y: 180 };
    stapel[t] = 0;
  });
  (graph.nodes || []).forEach(function (n) {
    if (!zeigen[n.id]) return;
    if (n.kind === 'session') pos[n.id] = { x: b / 2, y: 0 };
    else if (n.kind === 'topic') themen.unshift(n.id);   // payload: newest first
    else if (n.kind === 'entry' && stapel[n.tag] !== undefined) {
      pos[n.id] = { x: pos['col:' + n.tag].x, y: 240 + stapel[n.tag] * 55 };
      stapel[n.tag]++;
    } else streifen.push(n.id);
  });
  themen.forEach(function (id, i) { pos[id] = { x: b * (i + 1) / (themen.length + 1), y: 90 }; });
  Object.keys(sgVerborgen(graph, zeigen)).forEach(function (p) { streifen.push('more:' + p); });
  var tiefe = Math.max.apply(null, SG_TAGS.map(function (t) { return stapel[t]; }));
  var y0 = 240 + tiefe * 55 + 60, jeZeile = Math.max(1, Math.floor(b / 130));
  streifen.forEach(function (id, i) {
    pos[id] = { x: (i % jeZeile + 0.5) * (b / jeZeile), y: y0 + Math.floor(i / jeZeile) * 55 };
  });
  return pos;
}

function sgAusklappen(ausgeklappt, id) {
  // Accepts the expander id (`more:<parent>`) or the parent id itself.
  var p = String(id).indexOf('more:') === 0 ? String(id).slice(5) : String(id);
  ausgeklappt[p] = true;
  return p;
}

function sgPlatz(cy, id, nachbarn, mitte, zaehler) {
  // Next to the first connected node that already has a place, pushed
  // outward from the centroid; siblings fan out around that direction.
  // Nodes added earlier in the same diff count as placed already.
  var ankerId = (nachbarn[id] || []).filter(function (n) {
    return cy.getElementById(n).length;
  })[0];
  if (!ankerId) {
    // Nothing to hang on: fan around the centroid (the origin on an empty
    // canvas), the k-th such node of this diff on its own golden angle.
    var u = zaehler[''] = (zaehler[''] || 0) + 1;
    u -= 1;
    if (!mitte && u === 0) return { x: 0, y: 0 };
    var m = mitte || { x: 0, y: 0 }, r = SG_ABSTAND * (1 + Math.floor(u / 6) * 0.6);
    return { x: m.x + r * Math.cos(u * 2.4), y: m.y + r * Math.sin(u * 2.4) };
  }
  var anker = cy.getElementById(ankerId).position();
  var k = zaehler[ankerId] = (zaehler[ankerId] || 0) + 1;
  k -= 1;                                   // k-th new child of this anchor
  var dx = anker.x - (mitte ? mitte.x : anker.x), dy = anker.y - (mitte ? mitte.y : anker.y);
  var winkel;
  if (Math.abs(dx) + Math.abs(dy) < 1) {
    winkel = k * 2.4;                       // anchor is the centre: golden-angle spread
  } else {
    winkel = Math.atan2(dy, dx) + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.55;
  }
  return { x: anker.x + SG_ABSTAND * Math.cos(winkel), y: anker.y + SG_ABSTAND * Math.sin(winkel) };
}

function sgArt(d) {
  // Where a node lives in the kanban: session, topic row, a tag column,
  // a column head, or the strip below (notes, expanders, anything else).
  if (d.kind === 'session' || d.kind === 'topic' || d.kind === 'kopf') return d.kind;
  return d.kind === 'entry' && SG_KOEPFE[d.tag] ? 'spalte' : 'streifen';
}

function sgKanbanNeu(cy, d, breite, preset) {
  // A live kanban update: kept nodes are frozen, so a new node is placed
  // against what is ALREADY DRAWN, not against a fresh arrangement of the
  // whole payload (which would no longer match the screen).
  var b = breite > 0 ? breite : 1000, jeZeile = Math.max(1, Math.floor(b / 130));
  var slot = b / jeZeile, art = sgArt(d), gezeichnet = [];
  cy.nodes().forEach(function (n) {
    gezeichnet.push({ art: sgArt(n.data()), tag: n.data('tag'), p: n.position() });
  });
  if (art === 'session' || art === 'kopf') return preset[d.id];
  var p, weiter;
  if (art === 'topic') {
    var rechts = null;
    gezeichnet.forEach(function (o) {
      if (o.art === 'topic' && (!rechts || o.p.x > rechts.x)) rechts = o.p;
    });
    p = rechts ? { x: rechts.x + 160, y: rechts.y } : (preset[d.id] || { x: b / 2, y: 90 });
    weiter = function (q) { return { x: q.x + 160, y: q.y }; };
  } else if (art === 'spalte') {
    var kopf = cy.getElementById('col:' + d.tag);
    var kp = kopf.length ? kopf.position() : preset['col:' + d.tag], tief = kp.y + 5;
    gezeichnet.forEach(function (o) {
      if (o.art === 'spalte' && o.tag === d.tag) tief = Math.max(tief, o.p.y);
    });
    p = { x: kp.x, y: tief + 55 };
    weiter = function (q) { return { x: q.x, y: q.y + 55 }; };
  } else {
    // The strip: after the last drawn strip item, and below whatever the
    // columns actually reach -- a new row when they grew into it.
    var boden = 180, letzt = null;
    gezeichnet.forEach(function (o) {
      if (o.art !== 'streifen') boden = Math.max(boden, o.p.y);
      else if (!letzt || o.p.y > letzt.y || (o.p.y === letzt.y && o.p.x > letzt.x)) letzt = o.p;
    });
    if (!letzt || letzt.y < boden + 60) p = { x: slot / 2, y: boden + 115 };
    else if (letzt.x + slot < b) p = { x: letzt.x + slot, y: letzt.y };
    else p = { x: slot / 2, y: letzt.y + 55 };
    weiter = function (q) {
      return q.x + slot < b ? { x: q.x + slot, y: q.y } : { x: slot / 2, y: q.y + 55 };
    };
  }
  // Never on top of something already drawn (e.g. a column that has
  // reached the strip steps past it).
  function belegt(q) {
    return gezeichnet.some(function (o) {
      return Math.abs(o.p.x - q.x) < 60 && Math.abs(o.p.y - q.y) < 30;
    });
  }
  for (var i = 0; i < 500 && belegt(p); i++) p = weiter(p);
  return p;
}

function sgDiff(cy, graph, layout, opts) {
  // opts: ausgeklappt (expanded parents), gezeigt (ids drawn on this page,
  // updated here -- see sgSichtbar), breite, flash, einpassen.
  opts = opts || {};
  var gezeigt = opts.gezeigt;
  if (gezeigt) {
    // A node that left the payload is forgotten: should it come back
    // over the cap, it waits behind an expander like any new one.
    var imPayload = {};
    (graph.nodes || []).forEach(function (n) { imPayload[n.id] = true; });
    Object.keys(gezeigt).forEach(function (id) { if (!imPayload[id]) delete gezeigt[id]; });
  }
  var soll = sgElemente(graph, opts.ausgeklappt, gezeigt);
  if (gezeigt) soll.forEach(function (s) {
    if (s.group === 'nodes' && s.data.kind !== 'mehr') gezeigt[s.data.id] = true;
  });
  if (layout === 'kanban') soll = soll.concat(sgKoepfe());
  var sollIds = {}, ergebnis = { added: [], removed: [], kept: 0 };
  soll.forEach(function (s) {
    sollIds[s.data.id] = s;
    // The layout decides what is shown: `verdeckt` hides a graph-only edge
    // in the kanban and comes off again with the next graph diff.
    if (layout === 'kanban' && /(^| )nurgraf( |$)/.test(s.classes)) s.classes += ' verdeckt';
  });

  // Out first: edges before nodes, so nothing disappears implicitly.
  var weg = [];
  cy.edges().forEach(function (e) { if (!sollIds[e.id()]) weg.push(e); });
  cy.nodes().forEach(function (n) { if (!sollIds[n.id()]) weg.push(n); });
  weg.forEach(function (e) { ergebnis.removed.push(e.id()); e.remove(); });

  var mitte = null, anzahl = 0, sx = 0, sy = 0;
  cy.nodes().forEach(function (n) { var p = n.position(); sx += p.x; sy += p.y; anzahl++; });
  if (anzahl) mitte = { x: sx / anzahl, y: sy / anzahl };
  var nachbarn = {};
  soll.forEach(function (s) {
    if (s.group !== 'edges') return;
    (nachbarn[s.data.target] = nachbarn[s.data.target] || []).push(s.data.source);
    (nachbarn[s.data.source] = nachbarn[s.data.source] || []).push(s.data.target);
  });
  var preset = layout === 'kanban'
    ? sgKanbanPositionen(graph, opts.breite, opts.ausgeklappt, gezeigt) : {};
  // The full preset only fits an empty kanban (first draw); once anything
  // but the heads is drawn, new nodes go next to what is on screen.
  var live = false;
  if (layout === 'kanban') cy.nodes().forEach(function (n) { if (n.data('kind') !== 'kopf') live = true; });
  var neu = [], zaehler = {}, neuePos = [];
  ['nodes', 'edges'].forEach(function (gruppe) {
    soll.forEach(function (s) {
      if (s.group !== gruppe) return;
      var da = cy.getElementById(s.data.id);
      if (da.length) {
        // Kept: new label/classes, same place, a running flash survives.
        // id/source/target are immutable in cytoscape -- never re-set.
        if (gruppe === 'nodes') {
          var frisch = {};
          Object.keys(s.data).forEach(function (k) { if (k !== 'id') frisch[k] = s.data[k]; });
          da.data(frisch);
        }
        da.classes(s.classes + (da.hasClass('neu') ? ' neu' : ''));
        ergebnis.kept++;
        return;
      }
      var spec = { group: gruppe, data: s.data, classes: s.classes };
      if (gruppe === 'nodes') {
        spec.position = (live ? sgKanbanNeu(cy, s.data, opts.breite, preset) : preset[s.data.id]) ||
          sgPlatz(cy, s.data.id, nachbarn, mitte, zaehler);
        neuePos.push(spec.position);
        if (s.data.kind !== 'kopf') neu.push(s.data.id);
      } else {
        neu.push(s.data.id, s.data.source, s.data.target);
      }
      cy.add(spec);
      ergebnis.added.push(s.data.id);
    });
  });

  // One flash: the class goes on once and comes off once. No loop, no
  // repeating CSS animation. MM-D's own "pending" look is not in this
  // repo yet -- align the .neu style with it on merge.
  if (opts.flash !== false && neu.length) {
    var gesehen = {};
    neu = neu.filter(function (id) { return !gesehen[id] && (gesehen[id] = true); });
    neu.forEach(function (id) { cy.getElementById(id).addClass('neu'); });
    setTimeout(function () {
      neu.forEach(function (id) { var e = cy.getElementById(id); if (e.length) e.removeClass('neu'); });
    }, SG_FLASH_MS);
  }

  // A new node placed outside the visible area would arrive unseen: fit
  // once, only when that happened -- a zoom, never a relayout.
  // Both layouts: kanban columns grow down, the topic row grows right.
  // Short and animated, so the room sees the view widen, not jump.
  if (opts.einpassen && neuePos.length) {
    var ext = cy.extent();
    if (neuePos.some(function (p) {
      return p.x < ext.x1 || p.x > ext.x2 || p.y < ext.y1 || p.y > ext.y2;
    })) cy.animate({ fit: { eles: cy.elements(), padding: 30 }, duration: 300 });
  }
  return ergebnis;
}

function sgZaehler(graph, ausgeklappt, gezeigt) {
  // "verborgen" counts only what is really not on screen: a drawn node
  // kept past the cap is shown, not hidden.
  var zeigen = sgSichtbar(graph, ausgeklappt, gezeigt), anzahl = Object.keys(zeigen).length;
  var verborgen = (graph.nodes || []).length - anzahl;
  return anzahl + ' Knoten' + (verborgen > 0 ? ' · ' + verborgen + ' verborgen' : '');
}

function sgTokenQuery(trenner) {
  var t = (typeof window !== 'undefined' && window.STAGE_TOKEN) ? window.STAGE_TOKEN : '';
  return t ? trenner + 't=' + encodeURIComponent(t) : '';
}

function sgZiel(href) {
  // `file/<rel>` or `vault/<rel>` only; dot segments dropped and segments
  // encoded for the same reason grafPfad gives in graph.js.
  var teile = String(href || '').split('/'), art = teile.shift();
  if (art !== 'file' && art !== 'vault') return '';
  teile = teile.filter(function (t) { return t !== '' && t !== '.' && t !== '..'; });
  if (!teile.length) return '';
  return art + '/' + teile.map(encodeURIComponent).join('/') + sgTokenQuery('?');
}

function sgKlickbar(n) {
  // The three cases the tap handler below actually acts on: an expander,
  // a topic (live opener or the direct #block-<id> jump), or anything
  // carrying a usable href (the seed, a vault/project note).
  return n.hasClass('mehr') || n.hasClass('sg-topic') || !!n.data('href');
}

function sgLayoutLesen() {
  try {
    var l = localStorage.getItem(SG_LAYOUT_KEY);
    return l === 'kanban' ? 'kanban' : 'graph';
  } catch (e) { return 'graph'; }
}

// The page's own type (stage.py GRAPH_STYLE); canvas text does not inherit CSS.
var SG_SCHRIFT = '"VT323", "IBM Plex Mono", ui-monospace, monospace';

var SG_STIL = [
  // Every label (heads included) on a dark plate, so labels crossing
  // edges or each other stay readable; gone at tiny zoom, not mush.
  { selector: 'node', style: {
      'background-color': '#1f8f3f', 'width': 16, 'height': 16, 'label': 'data(label)',
      'font-family': SG_SCHRIFT, 'font-size': 9, 'color': '#c8f5d2',
      'text-valign': 'bottom', 'text-margin-y': 4,
      'text-max-width': 110, 'text-wrap': 'ellipsis',
      'text-background-color': '#050a06', 'text-background-opacity': 0.75,
      'text-background-padding': 2, 'text-background-shape': 'roundrectangle',
      'min-zoomed-font-size': 6 } },
  { selector: 'node.sg-session', style: {
      'background-color': '#39ff6a', 'width': 34, 'height': 34, 'font-size': 12 } },
  { selector: 'node.sg-topic', style: { 'background-color': '#2fcf58', 'width': 24, 'height': 24 } },
  { selector: 'node.sg-note', style: { 'background-color': '#16662d' } },
  { selector: 'node.sg-note.ring2', style: { 'color': '#6fae7f' } },   // dimmer than ring 1
  { selector: 'node.gnade', style: { 'opacity': 0.5 } },
  { selector: 'node.mehr', style: {
      'shape': 'round-rectangle', 'background-color': '#07110a', 'border-width': 1,
      'border-color': '#1f8f3f', 'width': 34, 'height': 20, 'text-valign': 'center',
      'text-margin-y': 0, 'color': '#39ff6a' } },
  { selector: 'node.kopf', style: {
      'shape': 'round-rectangle', 'background-opacity': 0, 'width': 120, 'height': 24,
      'text-valign': 'center', 'text-margin-y': 0, 'font-size': 12, 'color': '#39ff6a' } },
  { selector: 'edge', style: {
      'width': 1.4, 'line-color': '#1f8f3f', 'curve-style': 'bezier' } },
  { selector: 'edge.ring2', style: { 'line-style': 'dashed', 'opacity': 0.45 } },
  { selector: 'edge.tag', style: { 'line-style': 'dotted' } },
  { selector: 'edge.mehr', style: { 'line-style': 'dotted', 'opacity': 0.6 } },
  { selector: 'edge.verdeckt', style: { 'display': 'none' } },
  // The "pending" look: bright border plus a glow, for one flash only.
  { selector: 'node.neu', style: {
      'border-width': 3, 'border-color': '#39ff6a', 'underlay-color': '#39ff6a',
      'underlay-opacity': 0.35, 'underlay-padding': 8 } },
  { selector: 'edge.neu', style: { 'line-color': '#39ff6a', 'width': 2.4 } }
];

// Layout numbers swept 2026-11-08 against the REAL
// 2026-09-26 production session graph -- the one the
// previous numbers (nodeRepulsion 40000, idealEdgeLength 160, carried
// over 2026-08-15 from an earlier, wall-sized graph surface and scaled
// down by guess) had never been measured against. Method: headless
// cytoscape+fcose (the vendored UMD bundles, no browser canvas -- so
// node dimensions stay at the plain marker size; nodeDimensionsIncludeLabels
// is therefore of no use here and left off), run against that session's
// real topic structure (ids only, no label/transcript text -- a local,
// uncommitted copy). Measured 20 topic nodes, not the 25 the card assumed
// (buehne.json's title history advanced more often than the protocol grew
// new topic headings for -- a real finding, not an exception). Overlap =
// axis-aligned intersection of each topic node's 16x16 marker plus its
// label box below (up to 110 wide, ~25 tall, fixed size regardless of the
// actual text -- the worst case, since there is no canvas here to measure
// it for real). fcose randomizes its start same as production, so every
// combination ran repeatedly with a fresh seed; "overlaps" below is the
// WORST count seen, not a lucky run. Before: 40000 / 160, unmeasured --
// measured worst case 11/20 topic nodes overlapping (5 seeds).
//
// 0 overlaps IS reachable (210000 / 520, 0/20 over 30 seeds) but turned
// out not to be the answer: a browserbroker screenshot of that result
// (card step 3) showed every label gone. Cause, found from the screenshot,
// not the bounding-box count: `fit:true` zooms the camera to fit the
// WHOLE graph, and label font-size is in graph units, so it shrinks with
// that zoom -- a layout wide enough for 0 overlaps zooms out far enough
// (~0.50x on this session's 1280x598 container) to push the 9px label
// below the style's own `min-zoomed-font-size:6`, and cytoscape stops
// drawing it. A graph with no readable labels is a worse regression than
// the occasional overlap it was meant to fix, so this is the
// "0 overlaps unreachable at an acceptable cost" case the card names: the
// chosen values are the smallest-overlap combination that ALSO keeps the
// post-fit label size at or above that 6px floor (checked the same way --
// bounding box of ALL nodes after layout, against the real 1280x598
// container, padding 30, `(1280-60)/bboxW` vs `(598-60)/bboxH`). Picked:
// 210000 / 320 -- worst case 1/20 topic nodes overlapping (30 seeds),
// post-fit label size ~6.2px (above the floor). Still a large improvement
// over the unmeasured 40000/160 (11/20 worst case) at a size that stays
// on screen.
function sgFcose(animiert) {
  if (!window.cytoscapeFcose) {
    return { name: 'cose', animate: !!animiert, randomize: true, fit: true, padding: 30 };
  }
  return { name: 'fcose', quality: 'proof', animate: !!animiert, randomize: true,
           fit: true, padding: 30, nodeDimensionsIncludeLabels: false,
           packComponents: true, uniformNodeDimensions: false,
           nodeRepulsion: 210000, idealEdgeLength: 320, numIter: 2500 };
}

function sgStart(el) {
  if (!window.cytoscape) { el.textContent = 'Graph nicht verfügbar.'; return null; }
  var st = { cy: null, layout: sgLayoutLesen(), ausgeklappt: {}, gezeigt: {}, graph: null,
             letzt: null, gezeichnet: false, laufend: null, diffs: 0, letztesErgebnis: null };
  var cy = st.cy = window.cytoscape({ container: el, elements: [], style: SG_STIL,
                                      autoungrabify: true, wheelSensitivity: 0.2 });
  var breite = function () { return el.clientWidth || 1000; };
  var zaehlEl = document.getElementById('sg-count');
  var knoepfe = [].slice.call(document.querySelectorAll('#sg-toggle button[data-layout]'));

  function markieren() {
    knoepfe.forEach(function (k) {
      var an = k.getAttribute('data-layout') === st.layout;
      k.classList.toggle('an', an);
      k.setAttribute('aria-pressed', an ? 'true' : 'false');
    });
  }

  function anwenden(flash) {
    var erst = !st.gezeichnet;
    st.letztesErgebnis = sgDiff(cy, st.graph, st.layout, { ausgeklappt: st.ausgeklappt,
      gezeigt: st.gezeigt, breite: breite(), flash: flash && !erst, einpassen: !erst });
    st.diffs++;
    if (erst && (st.graph.nodes || []).length) {
      // The one global layout of a page's life that no user asked for.
      st.gezeichnet = true;
      if (st.layout === 'graph') cy.layout(sgFcose(false)).run(); else cy.fit(undefined, 30);
    }
    if (zaehlEl) zaehlEl.textContent = sgZaehler(st.graph, st.ausgeklappt, st.gezeigt);
  }

  st.tick = function () {
    if (st.laufend) return st.laufend;
    if (document.hidden) return Promise.resolve();
    st.laufend = fetch('parts?g=1' + sgTokenQuery('&'), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        st.letztesErgebnis = null;
        if (!j || !j.graph) return;
        var s = JSON.stringify(j.graph);
        if (s === st.letzt) return;             // unchanged: no diff at all
        st.letzt = s; st.graph = j.graph;
        anwenden(true);
      })
      .catch(function () { /* keep the last drawn state; next poll retries */ })
      .then(function () { st.laufend = null; });
    return st.laufend;
  };

  st.setzeLayout = function (l) {
    st.layout = l === 'kanban' ? 'kanban' : 'graph';
    try { localStorage.setItem(SG_LAYOUT_KEY, st.layout); } catch (e) { /* private mode */ }
    markieren();
    if (!st.graph || !st.gezeichnet) return;
    sgDiff(cy, st.graph, st.layout, { ausgeklappt: st.ausgeklappt, gezeigt: st.gezeigt,
                                      breite: breite(), flash: false });
    if (st.layout === 'kanban') {
      cy.layout({ name: 'preset', animate: true, animationDuration: 400, fit: true, padding: 30,
                  positions: sgKanbanPositionen(st.graph, breite(), st.ausgeklappt,
                                                st.gezeigt) }).run();
    } else {
      cy.layout(sgFcose(true)).run();
    }
  };

  knoepfe.forEach(function (k) {
    k.addEventListener('click', function (ev) {
      if (ev && ev.preventDefault) ev.preventDefault();
      st.setzeLayout(k.getAttribute('data-layout'));
    });
  });
  markieren();

  // Mouse is the emergency exit, not the operating concept -- but every
  // click here has a clear, single effect.
  cy.on('tap', 'node', function (ev) {
    var n = ev.target;
    if (n.hasClass('mehr')) {
      sgAusklappen(st.ausgeklappt, n.id());
      if (st.graph) anwenden(true);
    } else if (n.hasClass('sg-topic')) {
      var o = window.opener;
      if (o && !o.closed) {
        o.postMessage({ type: 'cothinker-topic', id: n.id(), title: n.data('label'),
                        time: n.data('time') }, location.origin);
      } else if (st.graph) {
        // Opened directly (no opener to post to, or it's gone): jump
        // straight to the protocol via the seed's own href, the same file
        // sgZiel() already builds for the seed node, with the topic's own
        // id as an anchor for stage.py's block-anchor script to find.
        var saat = cy.getElementById(st.graph.seed);
        var saatZiel = saat.length ? sgZiel(saat.data('href')) : '';
        if (saatZiel) window.open(saatZiel + '#block-' + encodeURIComponent(n.id()), '_blank');
      }
    } else if (n.data('href')) {
      var ziel = sgZiel(n.data('href'));
      if (ziel) window.open(ziel, '_blank');
    }
  });

  // Mouse is the emergency exit, not the operating concept -- but every
  // click here has a clear, single effect, so the cursor says so only on
  // the nodes `sgKlickbar()` lets the tap handler above act on.
  cy.on('mouseover', 'node', function (ev) {
    el.style.cursor = sgKlickbar(ev.target) ? 'pointer' : '';
  });
  cy.on('mouseout', 'node', function () {
    el.style.cursor = '';
  });

  // The "Bühne ↗" link is a plain link on purpose: focusing the opener
  // from here is ignored by Chromium, and a handler that prevented the
  // default then did nothing at all.

  setInterval(st.tick, SG_POLL_MS);
  st.tick();
  return st;
}

if (typeof window !== 'undefined') {
  window.sgElemente = sgElemente;
  window.sgKanbanPositionen = sgKanbanPositionen;
  window.sgDiff = sgDiff;
  window.sgAusklappen = sgAusklappen;
  window.sgKlickbar = sgKlickbar;
  window.sgZaehler = sgZaehler;
  window.sgZiel = sgZiel;
  window.sgLayoutLesen = sgLayoutLesen;
  window.sgStart = sgStart;
}
