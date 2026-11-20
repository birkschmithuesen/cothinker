# pegeltest — phone mic level vs. mouth distance

Static web page (no dependencies) that measures a phone's microphone input level (dBFS) at fixed
mouth distances: 10 s silence, then 15 s read-aloud each at 15 / 40 / 80 (flat on the table) /
150 cm. Use it to set a "too quiet / closer to the mouth" threshold for any phone-mic project.
First use: a workshop mic phone.

- Capture constraints match `workshop/static/table.js`: echoCancellation, noiseSuppression,
  autoGainControl all `false`. These are requests only; the result JSON records what the browser
  reports (`supported`, `settings`), because Safari/iOS does not support `autoGainControl` (MDN).
- Output per step: speech level median/p90, distance to the room level, speech share, clipping.
- "Warntest" mode: runs the SAME vad.js/closer-warning.js logic as the
  table mic page (verbatim copies here, pinned identical by `tests/test_pegeltest.py`) and shows live
  whether the "move closer" hint would fire, so the shipped rule (SNR/hold/hide) can be re-tested
  before a rehearsal. Logs SNR per 100 ms into the uploaded JSON (`mode: "warntest"`). Thresholds
  override the same way as the table mic page's URL params: `?closer_snr_db=`, `?closer_hold_s=`,
  `?closer_hide_s=`.
- At the end the page uploads the result JSON and the WAV to `./upload` (same origin), served by
  `upload_server.py` (stdlib, 127.0.0.1, JSON <= 64 KB, WAV <= 32 MB, files never overwritten,
  nothing served back). Share buttons stay as a fallback if the upload fails. The intro tells the
  tester that result and recording are sent; record only voices that agreed to it.
- Mic sensitivity differs per phone model: measure each model in use, or set the threshold relative
  to the phone's own near-mouth level.

Serve over https (getUserMedia needs a secure origin). Live copy on the operator's server,
served at a fixed path (nginx `location /pegeltest/ { alias /var/www/pegeltest/; }`).
Deploy: static files to `/var/www/pegeltest/`; receiver as user service
`pegeltest-upload.service` (`upload_server.py --out ~/pegeltest-uploads --port 8796`); nginx
`location = /pegeltest/upload { client_max_body_size 40m; proxy_pass http://127.0.0.1:8796; }`.
Uploads are measurement data: they are deleted with the server wipe.
