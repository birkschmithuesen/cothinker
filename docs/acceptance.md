# Deployment acceptance: `python -m demo.acceptance.e2e`

ADR 0022 names four test levels; this is level 4, "Deployment" — the only one that
touches the network, and the only one that may spend money. **A deployment is
"ready" when this command's report has no FAIL for the exact commit that is
deployed** (ADR 0022 rule 5). Levels 1 to 3 (unit, contract, template) run in CI on
every commit and never touch the network; this one is run by hand before every
live use, from a machine that is not the server.

## Why from outside the server

A browser running on the server itself tests a shortcut: it can reach the demo
over `localhost` or a hosts-file override, bypassing the public DNS name, the TLS
certificate, and the token gate a real participant's link depends on. This tool
only ever reaches the deployment the way a participant's browser does: a public
HTTPS host, the access token in the link, nothing resolved locally.

## Running it

From a laptop or any other machine outside the server, with network egress to
the deployment's host:

```
pip install -r demo/acceptance/requirements.txt
playwright install chromium        # once
python -m demo.acceptance.e2e \
    --base-url https://<host> \
    --token-file <file holding the access token, one line> \
    --out <dir for report.json, report.md, screenshots, audio>
```

Useful flags:

- `--minutes 4` — a floor on how long the two participants stay in the call
  (default 4); the actual connect time is whichever is longer, this or the
  generated script's own length plus the time the checks below need while
  the call is still up.
- `--keep-artifacts` — keep the rendered `alex.wav`/`sam.wav` in `--out`
  (deleted by default once the run is done; screenshots and reports are always
  kept).
- `--tts piper` (default) or `--tts espeak` — see "Speech" below.
- `--piper-voice-alex PATH` / `--piper-voice-sam PATH` — local Piper voice
  model files. Without them, give `--piper-voice-url-alex URL
  --piper-voice-sha256-alex HASH` (and the `-sam` pair) to auto-download and
  checksum-verify a voice into `--out/voices/`. There is no built-in default
  URL or hash — a voice this tool has not verified itself is not assumed
  trustworthy; the operator supplies one local path or one verified URL+hash
  per speaker.
- `--wiki-dir DIR` — only needed so the knowledge-hit check is not SKIPPED.

Exit code is 0 only if the report has no FAIL. `report.json` and `report.md` are
written to `--out`; `report.md` is also printed to stdout. **The token is read
from `--token-file` and never appears in either report, in a screenshot, or on
stdout** — every check's evidence string goes through redaction before it is
rendered or printed.

## What each check proves (and does not)

1. **Preflight** — every public route from ADR 0020/0021's path table
   (the call-only page `/meeting/`, the split page `/meeting-split/`, `/stage/`,
   `/monitor/`, the contract's `/api/snapshot` and `/api/events`, and
   `/api-fixtures/`) answers 200 with the token and 404 without it; the TLS
   handshake on the host succeeds. Proves the token gate and certificate are
   live for every surface. Does **not** prove the page behind a 200 renders
   correctly — later checks do that.

2. **Speech** — a script is generated: silent lead-in, alternating keyword
   sentences for two speakers (reused from `make_tts.sentences()`, scored
   later), the isolated line "Hey Robo, fass bitte kurz zusammen." spoken
   twice, one agenda-like line, one decision-like line. With `--tts piper`
   (default), each speaker gets a distinct local Piper voice. **`--tts
   espeak` is an explicit fallback**, not a silent default: espeak's robotic
   speech is not reliably recognised by the real speech recogniser, so the
   wake-phrase check (point 7) is reported `SKIPPED`, never `PASS`, whenever
   this flag is used — a skip here does not mean the call lane works, only
   that this run did not test it.

3. **Participants** — two headless Chrome instances join the real call
   through the public call-only page URL (token included, no host-resolver
   mapping, no `localhost`), each playing one rendered WAV as its fake
   microphone capture. Proves the join path participants actually use works.
   Does not prove two *human* browsers would behave identically (headless
   Chrome, fixed window size, fake audio device).

4. **Media path proof** — each participant's `RTCPeerConnection.getStats()`
   is read (via an init script `participant.py --stats-out` installs before
   navigation, so it captures connections Jitsi creates internally) and
   classified: the selected candidate pair's type (`host`/`srflx`/`relay`)
   and transport (`udp`/`tcp`), and inbound/outbound audio byte counters.
   FAILs if no audio bytes flow at all. Proves media actually moved and
   which path it took (direct, STUN-assisted, or relayed); does not prove
   audio *quality* — byte counters are blind to packet loss or jitter.

5. **Surfaces** — Playwright opens stage and monitor at 1920×1080 against
   `?api=` on the public contract route, connecting before the script
   finishes so their SSE subscriptions are live through the rest of it,
   then asserts: the two participants' real names appear on each surface
   (never a generic "Speaker N"), monitor's current agenda item
   (`agenda.current_text`, from the project's own `agenda.md` -- unrelated
   to the spoken script's probe lines; `SKIPPED` when no agenda is
   configured; stage was never expected to show it, see below), and no
   console errors were logged; screenshots are saved. Does not prove the
   layout is usable at other viewport sizes.

   Two things this check can legitimately report FAIL for today, not
   because the check is wrong but because the feature behind it is not
   wired up yet: monitor's own markup renders participant names from the
   `transcript:recent` view-intent (last three contributions), which
   `surfaces/contract/live_player.py` does not yet derive for a live
   session (only `StatusIntent` is); and `stage/stage.py`'s own protocol
   rendering never includes `agenda.current_text` as a label, only the
   written protocol's own topics -- the current agenda item is monitor's
   job (ADR 0012), not stage's.

6. **Contract** — `surfaces/contract/check.py`'s own conformance check
   (ADR 0019) runs against the live monitor URL, exactly as
   `python -m surfaces.contract.check --surface <url>` would from the
   command line. Proves the deployed monitor still keeps the surface
   contract (fetches `/snapshot`, subscribes to `/events`, requests nothing
   outside the contract, handles `planned` fields). This tool's own
   `contract/report.json` artifact is redacted of the token before anything
   else touches it, the same way the top-level report is.

7. **Results** — speaker attribution via `score.py`, reusing it unchanged:
   the stage's own rendered "Rohtranskript" lines are read back from the DOM
   and turned back into the `- **HH:MM** *Speaker:* text` bullets `score.py`
   already parses, then scored against the generated script (≥ 90% by
   default). The wake-phrase check polls the public `/api/snapshot`'s
   `call_answer` field (the contract's own description: "the last
   call-and-answer line, if any") for up to 60 s after the probe audio and is
   `SKIPPED`, never `PASS`, under the espeak fallback (point 2). A topic
   event and a view-intent event are checked for in the same snapshot's
   history. The knowledge-hit check is `SKIPPED` without `--wiki-dir`; with
   it, it looks for a notice whose `kind` names knowledge or wiki — a
   heuristic on the raising block's own naming, not a guarantee every
   knowledge block uses that exact word.

8. **Cleanup** — the run's session is marked with the title prefix `e2e-`
   and the tool prints the exact operator command to remove it afterwards.
   **This tool never deletes server data itself** — the operator runs that
   command by hand, on the server, when ready.

9. **Output** — `report.json` and `report.md`, one row per check with its
   status (`PASS`/`FAIL`/`SKIPPED`) and evidence; exit code 0 only if no
   check is `FAIL`.

## What a green report does not prove

- Human perception: a recogniser-friendly synthetic voice is not a human
  voice, headless Chrome with fake audio capture is not a real microphone in
  a real room.
- Load: this is a two-participant smoke run, not a capacity test.
- Anything outside the exact commit deployed at run time — a report is only
  evidence for the commit it was taken against (ADR 0022 rule 5).
