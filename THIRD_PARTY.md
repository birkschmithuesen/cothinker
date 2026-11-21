# Third-party material

CoThinker itself is AGPL-3.0-only (see `LICENSE`). This lists vendored
code under its own licence, found via `git ls-files "*LICENSE*"` plus
the vendor READMEs next to the bundles.

## Vendored code with its own licence

| Path | What | Licence | Upstream |
|---|---|---|---|
| `stt/fundusapps/stt_server/backends/_vendored_whisper_streaming/whisper_online.py` | whisper_online.py, streaming wrapper around OpenAI Whisper | MIT, Copyright (c) 2023 ÚFAL | whisper_streaming |
| `stt/fundusapps/stt_server/vad_models/` | Silero VAD model (`silero_vad.onnx`) | MIT, Copyright (c) 2020-present Silero Team | silero-vad |
| `stage/static/vendor/cytoscape.min.js` | cytoscape, graph rendering | MIT | cytoscape |
| `stage/static/vendor/layout-base.js` | layout-base | MIT | layout-base |
| `stage/static/vendor/cose-base.js` | cose-base | MIT | cose-base |
| `stage/static/vendor/cytoscape-fcose.js` | cytoscape-fcose layout plugin | MIT | cytoscape-fcose |
| `stage/static/vendor/cytoscape-layout-utilities.js` | cytoscape-layout-utilities | MIT | cytoscape-layout-utilities |
| `stage/static/vendor/qrcode.js` | qrcode-generator 1.4.4, QR code encoder for the share link, Copyright (c) 2009 Kazuhiko Arase | MIT | qrcode-generator |
| `workshop/static/icons/LICENSE-lucide.txt`, `stage/static/workshop/LICENSE-lucide.txt` | Lucide icon set | ISC | lucide |
| `stage/static/workshop/LICENSE-OFL-Archivo.txt` | Archivo font | SIL Open Font License 1.1 | Archivo |
| `stage/static/workshop/LICENSE-OFL-Limelight.txt` | Limelight font | SIL Open Font License 1.1 | Limelight |
| `stage/static/workshop/LICENSE-OFL-Unbounded.txt` | Unbounded font | SIL Open Font License 1.1 | Unbounded |

Each licence file above travels with its material and is the
authoritative text; this table is an index, not a copy.
