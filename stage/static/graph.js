// CoThinker - drawing the vault graph
// Copyright (C) 2026 Birk Schmithuesen
//
// This program is free software under the GNU Affero General Public
// License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
//
// Obsidian's graph view is the MODEL for how this looks, not a part of
// it: it lives inside the Obsidian app and is neither embeddable nor
// remote-controllable. So: cytoscape + fcose, our own styling.
//
// Split in two on purpose. `grafElemente` and `grafPfad` are pure and are
// what the node test exercises; `grafZeichnen` is the half that needs a
// canvas and a browser.

function grafPfad(id) {
  // The stage may be mounted under /buehne/, so the URL is RELATIVE and
  // <base> resolves it -- the same rule every other link on the page
  // follows. Only the path segments are encoded; the slashes stay.
  //
  // Dot segments have to go FIRST. `.` and `..` are unreserved, so
  // `encodeURIComponent` passes them through untouched and the browser
  // resolves them before it ever sends the request -- measured: an id of
  // "../parts" turns the click into /buehne/parts, and "../../etc/passwd"
  // leaves the mount entirely. The id comes out of a payload nobody
  // trusts, and a real vault id never carries a dot segment, so dropping
  // them is free and keeps the click under vault/ where it belongs.
  var teile = String(id).split('/').filter(function (teil) {
    return teil !== '' && teil !== '.' && teil !== '..';
  });
  return 'vault/' + teile.map(encodeURIComponent).join('/');
}

function grafZiel(id) {
  // Read lazily, at click time: graph.js loads in <head>, the page script
  // (which sets window.STAGE_TOKEN) runs at the end of <body>, and the tap
  // handler below only ever runs on a click -- by then the assignment has
  // long happened, so the load order across the two <script> tags is safe.
  var t = (typeof window !== 'undefined' && window.STAGE_TOKEN)
    ? '?t=' + encodeURIComponent(window.STAGE_TOKEN) : '';
  return grafPfad(id) + t;
}

function grafElemente(graph) {
  var elemente = [];
  (graph.nodes || []).forEach(function (n) {
    elemente.push({
      data: { id: n.id, label: n.label || '', ring: n.ring || 0 },
      // A viewer has to see what he linked himself, what he tagged, and
      // -- later -- what the machine guessed. The class carries it.
      classes: 'knoten' + (n.seed ? ' saat' : '') + (n.grace ? ' gnade' : '')
    });
  });
  (graph.edges || []).forEach(function (e) {
    elemente.push({
      data: { id: e.id, source: e.source, target: e.target },
      classes: 'kante ' + e.kind
    });
  });
  return elemente;
}

function grafZeichnen(el, graph) {
  if (!window.cytoscape) { el.textContent = 'Graph nicht verfügbar.'; return; }
  var cy = cytoscape({
    container: el,
    elements: grafElemente(graph),
    style: [
      { selector: 'node', style: {
          'background-color': '#7f8a99', 'width': 14, 'height': 14,
          'label': 'data(label)', 'font-size': 9, 'color': '#c8d0da',
          'text-valign': 'bottom', 'text-margin-y': 3,
          'text-max-width': 90, 'text-wrap': 'ellipsis' } },
      { selector: 'node.saat', style: {
          'background-color': '#e0a33e', 'width': 26, 'height': 26,
          'font-size': 12, 'color': '#f2f4f7' } },
      { selector: 'node.gnade', style: { 'opacity': 0.55 } },
      // The two hard kinds, told apart at a glance: a link is a line
      // somebody drew, a shared tag is a dashed hint of a category.
      { selector: 'edge.wikilink', style: {
          'width': 1.6, 'line-color': '#8fa6c4', 'curve-style': 'bezier' } },
      { selector: 'edge.tag', style: {
          'width': 0.8, 'line-color': '#4d5a6b', 'line-style': 'dashed',
          'curve-style': 'bezier' } }
    ],
    layout: { name: window.cytoscapeFcose ? 'fcose' : 'cose',
              animate: false, randomize: true, nodeSeparation: 90 },
    autoungrabify: true, wheelSensitivity: 0.2
  });
  // Clicking a node opens the note. Mouse is the emergency exit, not the
  // operating concept (kurator-kernmotivation N3) -- the surface itself
  // arrives by talking on.
  cy.on('tap', 'node', function (ev) {
    window.location.href = grafZiel(ev.target.id());
  });
  return cy;
}

if (typeof window !== 'undefined') {
  window.grafPfad = grafPfad;
  window.grafZiel = grafZiel;
  window.grafElemente = grafElemente;
  window.grafZeichnen = grafZeichnen;
}
