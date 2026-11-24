// The switching rule of issue #173 (spec 3.7): one rest view; an automatic
// switch holds autoHoldMs, a manual choice pins pinMs; afterwards back to rest.
// While pinned, automatic switches are ignored. Timers only -- page.clock drives it in tests.
export function createSwitcher({ restView, autoHoldMs, pinMs, onChange }) {
  let view = restView;
  let mode = "rest";
  let until = 0;
  let timer = null;

  function set(nextView, nextMode, holdMs) {
    clearTimeout(timer);
    view = nextView;
    mode = nextMode;
    until = holdMs ? Date.now() + holdMs : 0;
    if (holdMs) timer = setTimeout(() => set(restView, "rest", 0), holdMs);
    onChange();
  }

  return {
    auto(key) { if (mode !== "pinned") set(key, "auto", autoHoldMs); },
    pin(key) { set(key, "pinned", pinMs); },
    current: () => view,
    mode: () => mode,
    remainingMs: () => (until ? Math.max(0, until - Date.now()) : 0),
  };
}
