// stt/fundusapps/bbb_tap/join.js
// One idempotent join step per call. Python calls __roboJoinStep(name)
// about once a second and logs only the returned step word, never page
// text or the URL (the join link grants access to the room).
(function () {
  var w = window;
  var SEL = {
    consent: /Datenschutzerkl|privacy policy|Ich habe|I have read/i,
    nameInput: 'input[name*="name" i], input[id*="name" i], input[placeholder*="Name" i]',
    joinButton: /beitreten|teilnehmen|join|starten|start meeting|meeting starten|^\s*start\s*$/i,
    listenOnly: '[data-test="listenOnlyBtn"], button[aria-label*="Nur zuhören" i], button[aria-label*="Listen only" i]'
  };
  // Latch: once a click step fires on an element, don't click that same
  // element again for LATCH_MS. Python polls __roboJoinStep about once a
  // second; a control that stays visible across a page transition (e.g.
  // the consent button lingering while the next screen loads underneath)
  // was otherwise clicked repeatedly. WeakMap so latched elements can be
  // garbage-collected once the page navigates them away.
  var LATCH_MS = 5000;
  var lastClicked = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;

  function recentlyClicked(el) {
    if (!lastClicked) { return false; }
    var t = lastClicked.get(el);
    return t !== undefined && (Date.now() - t) < LATCH_MS;
  }
  function markClicked(el) {
    if (lastClicked) { lastClicked.set(el, Date.now()); }
  }

  function visible(el) { return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)); }
  function buttons() { return Array.prototype.slice.call(document.querySelectorAll('button, input[type=submit], a.btn')); }
  function byText(re) {
    return buttons().filter(function (b) { return visible(b) && re.test(b.innerText || b.value || b.getAttribute('aria-label') || ''); })[0];
  }
  function setValue(input, value) {
    var proto = Object.getPrototypeOf(input);
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    desc.set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // '' means "no visible error". Checked separately from the error-text
  // regex below: a visible alert/banner element is trusted on its own,
  // but a plain page (BBB's connection-limit error has no app shell at
  // all) is only treated as an error if its text actually looks like
  // one -- a bare substring match on the whole body would otherwise
  // false-positive on the normal join screen's own copy.
  var BODY_ERROR_RE = /limit of server connections exceeded|error\s*[:#]?\s*\d{3,4}/i;
  function pageErrorText() {
    var el = document.querySelector('[role="alert"], .alert, .error, [class*="Error" i], [id*="error" i]');
    var text = (el && visible(el)) ? (el.innerText || el.textContent || '') : '';
    if (!text) {
      var body = document.body ? (document.body.innerText || '') : '';
      if (BODY_ERROR_RE.test(body)) { text = body; }
    }
    return text.trim().slice(0, 500);
  }
  w.__roboPageError = pageErrorText;

  w.__roboJoinStep = function (name) {
    if (w.__roboTap && w.__roboTap.stats().tracks > 0) {
      // A tapped track is not enough: the tap's AudioContext must run
      // (see __roboTap.resume in hook.js). Until then keep polling.
      return w.__roboTap.resume() === 'running' ? 'listening' : 'resuming';
    }
    var listen = document.querySelector(SEL.listenOnly);
    if (visible(listen)) {
      if (recentlyClicked(listen)) { return 'waiting'; }
      markClicked(listen); listen.click(); return 'listen';
    }
    var consent = byText(SEL.consent);
    if (consent) {
      if (recentlyClicked(consent)) { return 'waiting'; }
      markClicked(consent); consent.click(); return 'consent';
    }
    var input = Array.prototype.slice.call(document.querySelectorAll(SEL.nameInput)).filter(visible)[0];
    if (input && input.value !== name) { setValue(input, name); return 'name'; }
    var join = byText(SEL.joinButton);
    if (join) {
      if (recentlyClicked(join)) { return 'waiting'; }
      markClicked(join); join.click(); return 'join';
    }
    return 'waiting';
  };
})();
