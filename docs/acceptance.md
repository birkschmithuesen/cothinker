# Deployment acceptance: `python -m demo.acceptance.e2e`

ADR 0022 names four test levels; this is level 4, "Deployment" — the only one that
touches the network, and the only one that may spend money. **A deployment is
"ready" when this command's report has no FAIL and carries the same
fingerprint as the running deployment** (ADR 0025; ADR 0022 rule 5). Levels 1 to 3 (unit, contract, template) run in CI on
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
    --preflight-report <report.json written by preflight.py --out> \
    --out <dir for report.json, report.md, screenshots, audio>
```

`--preflight-report` binds this run to the host-side preflight (see "Before and
after a run" below). Without it the run records the SKIPPED check
`fingerprint.bound`, and the go/no-go gate will not accept it.

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

8. **Cleanup** — there is no automated cleanup tool for this deployment, and
   the tool says so plainly instead of naming one that does not exist: the
   run's checks exercise the deployment's own live, already-running session
   (its title is fixed when its consumer process starts, see
   `demo/systemd/cothinker-demo-consumer.service`), not a separate session
   created for this run, so there is nothing scoped to just this run to
   delete. **This tool never deletes server data itself.** If the run's
   synthetic probe lines ever need removing from the transcript, the
   operator locates today's session folder (`<date>-<title-slug>` under
   the deployment's configured sessions directory — see
   `gateway/consumer.py`'s `slugify()`) and edits or removes it by hand.

9. **Output** — `report.json` and `report.md`, one row per check with its
   status (`PASS`/`FAIL`/`SKIPPED`) and evidence; exit code 0 only if no
   check is `FAIL`.

## Before and after a run: preflight, fingerprint, go/no-go, acceptances

The run above has two halves. The outside half (`e2e.py`, the scenario
runner) behaves like a participant and cannot see the server. The host half
runs on the deployment's own machine, only reads (`/proc`, `systemctl --user
show`/`cat`, files), and never restarts, reloads or reconfigures a unit. Both
halves carry the same fingerprint of the running deployment, and a separate
gate on the host decides go or no-go from them.

### Preflight (host)

```
python demo/acceptance/preflight.py \
    --consumer-unit <consumer-unit> \
    --expect-eou <seconds> --expect-max-delay <seconds> \
    --unit <unit> [--unit <unit> ...] \
    --stt-log <stt log> --consumer-log <consumer log> --template <template> \
    --out <reports-dir>
```

Each line is `PASS|FAIL|SKIPPED <check id>: <evidence>`; the exit code is 1
if any check FAILs. Checks:

- `preflight.lane-profile.<main|coach|call>`: each lane's profile is checked
  against `demo/acceptance/invariants.yaml` (which toolsets the profile
  disables or enables, what its `SOUL.md` must not say, which path patterns it
  must not match, which other lane it must differ from). Toolsets are read from
  `agent.disabled_toolsets`, the key the runtime applies; a profile without it
  counts as having nothing disabled.
- `preflight.stt.eou` and `preflight.stt.max_delay`: the values in the last
  start line of the STT log must equal `--expect-eou` and
  `--expect-max-delay`. Both flags are required and have no default; the
  expected values come from a measurement, not from this tool.
- `preflight.blocks.registry`: every block of the template loads, and the
  consumer log since its last start has no "no block registered" line.
- `preflight.unit.pending`: FAIL when systemd reports a pending daemon reload
  or a unit file or drop-in is newer than the running process, meaning the
  files on disk are not what is running. It needs `--unit`; without one it is
  SKIPPED.
- `preflight.fingerprint`: records the digest and its parts. It FAILs when the
  repository cannot be determined or no lane profile was found, because such a
  digest could never match the gate's.

`--out` writes `preflight-<run id>/report.json` under `<reports-dir>`,
including the fingerprint. The outside machine has no access to the host's
files, so copy that one file to the outside machine by any means you trust
and pass its path to the outside run as `--preflight-report`. A preflight
report without a fingerprint makes the run record a FAIL `fingerprint.bound`.
Every FAIL in the preflight report also counts at the gate (see below).

### Getting the outside report to the gate

The gate reads `<reports-dir>/<name>/report.json`, exactly one directory level
below `--reports`, and the preflight report must sit in the same `<reports-dir>`
so the gate can resolve the run's `preflight_run_id`. After an outside run,
copy its whole `--out` directory to the host as
`<reports-dir>/<some name>/` (a name unique per run). Each report records the
`tool` that wrote it (`e2e` or `scenarios`).

### The fingerprint

The digest is a SHA-256 over a canonical JSON map of input name to the
SHA-256 of that input's bytes. The report keeps the per-input hashes, so a
mismatch names the input that changed. Only hashes are ever printed;
environment values are hashed, never shown.

Inputs:

- per demo unit: the running process's command line (this captures hand-written
  drop-in overrides), an allow-listed slice of its environment (the
  `HERMES_PROFILE_DIR*`, `LANE_*` and `COTHINKER_*` keys, except the session
  pointer), and `systemctl --user cat` (unit file plus drop-ins);
- per lane profile: `config.yaml`, `SOUL.md`, and every file under its
  `skills/` tree;
- the deployed repository: `HEAD`, the diff of tracked files against `HEAD`,
  and the untracked files that are not ignored (compiled `.pyc` files and
  `__pycache__` directories are left out, the runtime rewrites them);
- the deployment's `demo.env`.

Excluded, with the reason: `secrets.env` (rotating a key is not a behaviour
change, and a test tool must not read a secret); `run.env` and the session
pointer (they change on every start without changing behaviour); logs and
project content.

### Go/no-go gate (host)

```
python -m demo.acceptance.gate \
    --reports <reports-dir> --acceptances <acceptances-file> \
    --consumer-unit <consumer-unit> --marker <start marker> \
    [--agent-identity "<name> <<email>>" ...] [--allowed-signers <file>]
```

`--marker` is required. `demo.sh start` writes the start marker, a JSON file
`{"at": <epoch seconds>}`, to `$DEMO_HOME/acceptance/last-start.json` after the
demo is up. The gate
recomputes the fingerprint of the running deployment and prints `GO`
(exit 0) or `NO-GO: <reason>` (exit 1). It says GO only when all of these hold:

1. for each tool that has an outside report with the current fingerprint, the
   newest such report names a preflight report with the same digest, and that
   preflight has a passing `preflight.fingerprint` check. A rerun of a tool
   can therefore clear that tool's own earlier FAIL, but a passing run of one
   tool cannot hide a failing run of another. A report without a `tool` is
   NO-GO;
2. each of those reports is well formed: no duplicate run ids, no unreadable or
   malformed report, at least one PASS other than `fingerprint.bound`, and its
   `fingerprint.bound` check is PASS (a report that is empty, only SKIPPED,
   or not bound to a preflight is NO-GO);
3. each started after the start marker, and the marker file is readable (a
   missing marker is NO-GO);
4. neither those reports nor their preflight reports have a FAIL, except a
   FAIL covered by an acceptance for its check id and the current
   fingerprint.

When the fingerprint does not match, the NO-GO names the changed inputs. An
acceptance for a different fingerprint does not count, so every change to the
deployment expires all acceptances. `--consumer-unit` is required; it also
locates the deployed repository.

### Human acceptances

An acceptance lets a person knowingly accept a specific FAIL for the current
fingerprint. The file is TOML, kept in a git repository the operator owns,
outside every run-output directory:

```toml
[[accept]]
check_id = "<check id>"
fingerprint = "<digest>"
accepted_by = "<human name>"
reason = "<why this FAIL is acceptable>"
```

No tool in `demo/acceptance/` writes this file or an `accepted_by` field.
The gate reads the file from `HEAD` and counts an entry only if all of these
hold:

- the entry is committed; uncommitted changes are ignored and reported;
- every line of the entry comes contiguously from one commit, so an entry
  edited across several commits is rejected and has to be re-committed whole;
- that commit's author and committer are both not an agent identity. Agent
  identities are matched case-insensitively by name or by email, ignoring
  `.mailmap`; they are the `commit_identity` of `FACTORY.yaml` plus every
  `--agent-identity`. If no agent identity is known (for instance
  `FACTORY.yaml` is unreadable), no acceptance counts;
- when `--allowed-signers` is given, the commit also carries a valid SSH
  signature from a listed key;
- `fingerprint` equals the current digest.

Each accepted FAIL is printed with its `verification`, which is
`author-checked` or `signature-checked`, and never anything stronger.
Rejected entries are printed with the reason.

### What this does not prevent

On a single-user host, where an agent runs as the same Unix user as the
operator, an agent can forge any author string, so the author check only
catches an agent following its normal workflow. If a signing key is unlocked
on that host the agent can sign too; the signature is a real barrier only if
the key is not usable by the agent's user, for example a passphrase-protected
or hardware key that the human unlocks. The gate therefore reports which check
it did and never calls the result "secure". The rule is that an agent never
writes the acceptance file, and a review that finds an agent-authored entry
treats the run as NO-GO.

## What a green report does not prove

- Human perception: a recogniser-friendly synthetic voice is not a human
  voice, headless Chrome with fake audio capture is not a real microphone in
  a real room.
- Load: this is a two-participant smoke run, not a capacity test.
- Anything outside the deployment fingerprint at run time — a report is only
  evidence for the deployment whose fingerprint it carries (ADR 0025).
