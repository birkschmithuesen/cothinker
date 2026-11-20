# Workshop skin (table phones)

The look of the table phones (`workshop/static/table.html`). Card S8,
design `docs/superpowers/specs/2026-10-27-s8-workshop-look-design.md`.

- **Served by the table hub, not by `stage/stage.py`:** `/table/<NN>/skin/<name>`, by name,
  no token (like every asset), through `workshop/skin.py` `skin_file()`. Allowed: one path
  segment `[a-z0-9-]+` with `.css`, `.svg`, `.png`, `.webp` or `.woff2`. Never `.js`: a skin
  cannot add logic. The hub (and the fake hub) read the file per request.
- **One entry point:** `table.html` links `./skin/workshop.css` right after its functional
  `<style>`. A later skin (Don's) replaces `workshop.css`, or adds files here and pulls them in
  from `workshop.css` with relative `@import url(other.css)` / `url(sign.svg)`. No code change
  is needed. If the file is missing, the page falls back to the S7 functional look.
- **Style the seam only** (the comment at the top of `table.html`), never the logic in
  `table.js`. Seams that land later: setting round (PH2) `body[data-step="setting"]`,
  `body[data-setting-phase]`, `[data-setting-q][data-current]`, `[data-setting-q][data-status]`,
  `.setting-answer[data-revised]`, `body[data-setting-soon]`; release (PH4) `body[data-release]`,
  `body[data-release-mode]`, `.release-quote`, `[data-release-control]`. Style them once their
  markup is on main, in the same chain.

## Rules every skin keeps (checked by `tests/test_workshop_skin.py` and the demo shots)

- **R1 Hidden stays hidden.** The first rule is `[hidden] { display: none !important; }`.
  Without it a `display` on a button or panel unhides what `table.js` hid, e.g. a recording
  control on a display phone.
- **R2 No UI text.** `content:` is `""` or one non-alphanumeric glyph. Text comes from
  `strings*.js` only.
- **R3 Nothing external.** No `http:`, `https:` or `//` in `url()` or `@import`. Fonts, if
  any, are `.woff2` files in this folder.
- **R4 Tap targets ≥ 44 px, no horizontal scroll at 390×844.**
- **R5 Readable flat from ~60 cm.** Body ≥ 20 px, `#now-name` ≥ 40 px, `#timer` ≥ 96 px,
  feedback title/body ≥ 34/22 px. `--ink` on `--paper`/`--card` and `--felt-ink` on `--felt` ≥ 7:1,
  `--muted` on `--paper` ≥ 4.5:1.
- **R6 Calm motion.** No spinner, except `button[data-busy]` (operator, 02.11.:
  the busy ring on an action button from the tap until the hub answers or 3 s pass). Transitions
  ≤ 300 ms. An `@media (prefers-reduced-motion: reduce)` block turns animation off.
  **Exception (operator, 02.11.):** the designer's writing scene while Robo writes the prologue
  (`#ds-make`, `workshop/static/table.js` `renderWritingScene()` /
  `workshop/static/prologue-make.js`) moves on purpose -- a spinner on the running step,
  material chips flowing in and out, five lines growing, a longer fade on leave. The operator asked
  for it back explicitly after the GP round noticed its absence ("es kommt jetzt auch diese
  coole Animation vom Prolog-Schreiben, richtig?", 02.11.), so R6 is lifted
  for exactly that subtree -- `workshop/skin.py`'s `lint()` takes an `r6_exempt_ids` allow-list
  (`skin.R6_EXEMPT_IDS`) for this, scoped to `#ds-make` and its own children only. Every other
  id in this file still keeps the 300 ms cap and the no-spinner rule outside `button[data-busy]`
  and `#ds-make`. The reduced-motion block still applies here too: every element lands in its
  finished state right away, never a spinner, never a stagger.
- **R7 Cheap paint.** No `backdrop-filter`, no `filter: blur`. The latency gates measure two
  animation frames after every change.

Check a skin: `env -u PYTHONPATH python3 tests/test_workshop_skin.py`, then
`python3 scripts/acceptance-run.py demo-screens --out <dir> --profile workshop`.
