# ADR 0023: Design tokens — one token file, generated CSS, the CoThinker default look

- **Status:** proposed
- **Date:** 2026-11-24

## Context

Source: issue #27 ("Design tokens: one place where a design system plugs into every surface"), closed by
issue #172. Colour and type choices live in several places today: `stage/static/workshop/workshop.css`
hand-writes its own `:root` palette, `stage/stage.py` hand-writes a second, unrelated palette inline, and
nothing stops a new surface from hard-coding a third. An instance that wants its own look has no seam but
editing surface code directly, which ADR 0015/0016 (public repo, private instances) rules out: a deployment's
own choices must not require a change to this repository's surface code.

## Decision

One token file, `surfaces/design/tokens.yaml`, is the single source for colour, type, spacing, radius and
motion, under semantic names (`surface`, `sheet`, `ink`, `muted`, `rule`, `grid`, `signal`, `accent`,
`accent-tint`, `ok`, `warn`, `danger`, plus type/space/radius/motion scales and room-display sizes measured
for 16:9 at 1920 px). A generator, `python -m surfaces.design.build`, turns it into `surfaces/design/tokens.css`
(CSS custom properties, prefix `--ct-`) and `surfaces/design/tokens.json` (the same values as data, for any
non-CSS consumer). A surface reads a token through the custom property or the JSON value; it never writes a
colour, font name or size literal of its own.

The default theme (`:root`) is the project website's light look (paper `#f3f1ea`, ink `#15171a`, …), typeset
in Fira Sans / Fira Mono. A `display` theme (selector `[data-theme="display"]`) is a dark variant for the
room display, carrying the same accent family. An instance overrides tokens by shipping its own
`instances/<name>/look/tokens.yaml`, merged over the default by the same generator — never by editing
surface code.

## Consequences

- `surfaces/design/` gets its own `tests/` (ADR 0022 level 1): contrast-ratio tests for every required
  text/background pair, a test that a surface stylesheet carries no hard-coded colour outside the generated
  file, and a test that an instance override wins over the default.
- `stage/static/workshop/workshop.css`'s product-accent properties (`--accent`, `--accent-tint` — already
  the same hex as the new `accent`/`accent-tint` tokens, since the tokens took that colour from this skin)
  read from `--ct-accent`/`--ct-accent-tint` with the current literal as fallback, so the rendered look is
  unchanged whether or not a page also loads `tokens.css`.
- Fira Sans/Fira Mono's `.woff2` files are not shipped by this change (they live in the website repo, not
  this one); `tokens.css` declares `@font-face` pointing at a path to be supplied later, documented as a
  known gap rather than invented or fetched.
- `docs/instance-format.md` gets one additive line for the `look/` subdirectory.
- Not every existing hard-coded palette is retrofitted by this ADR: `stage/stage.py`'s own inline `STYLE`
  block (its own palette, matching none of the fixed token values) is a separate, larger surface and is
  named as a follow-up, not silently rewritten here (see the PR for the conflict this would otherwise cause
  with "no visible change to the rendered look").

## Rejected options

- Let every surface keep its own palette and only document the semantic names: still N places to edit for
  one design change, and nothing catches a new hard-coded colour.
- A JSON-only token file with CSS written by hand from it: doubles the maintenance surface the generator is
  meant to remove, and drifts the moment one of the two is edited without the other.
- Ship the font binaries into this public repo now: provenance/licence of "the website repo's" exact files
  was not verified as part of this change; shipping them without that check risks shipping the wrong file
  under the wrong licence text into a public repository.
