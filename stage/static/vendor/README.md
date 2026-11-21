# Vendored browser libraries

The meeting machine may have no network, so every runtime dependency is
committed here as a UMD bundle and loaded with a plain `<script>` tag.
No CDN, no bundler, no `npm install` on site.

These are the published UMD builds, copied unchanged. They are NOT part
of CoThinker (AGPL-3.0-only); each keeps its own licence.

| File | Package | Version | Global | Licence |
|---|---|---|---|---|
| `cytoscape.min.js` | cytoscape | 3.x | `cytoscape` | MIT |
| `layout-base.js` | layout-base | 2.0.1 | `layoutBase` | MIT |
| `cose-base.js` | cose-base | 2.2.0 | `coseBase` | MIT |
| `cytoscape-fcose.js` | cytoscape-fcose | 2.2.0 | `cytoscapeFcose` | MIT |
| `cytoscape-layout-utilities.js` | cytoscape-layout-utilities | 1.1.1 | `cytoscapeLayoutUtilities` | MIT |
| `qrcode.js` | qrcode-generator (Kazuhiko Arase) | 1.4.4 | `qrcode` | MIT |

**Load order matters** — these UMD bundles resolve their dependencies off
`window` when there is no module loader:
`cytoscape` → `layout-base` → `cose-base` → `cytoscape-fcose` →
`cytoscape-layout-utilities`. `qrcode.js` stands alone (share-link QR code on
the stage page; upstream https://github.com/kazuhikoarase/qrcode-generator).

Refresh with `npm pack <package>` and copy `package/<name>.js` out of the
tarball.
