# Demo stack: one link, Jitsi and CoThinker side by side

One HTTPS link opens a self-hosted Jitsi meeting on the left and the
CoThinker stage on the right. A listen-only bot ("Robo") joins the same
room, feeds each participant's audio track into the STT server, and the
consumer writes the protocol with the Jitsi display names as speakers.

```
browser --> nginx --> /meeting/          demo/page/index.html (static, call only)
                      /meeting-split/    demo/page/split.html (static, call + stage)
                      /demo/api/        demo/session_server.py   (127.0.0.1:8091)
                      /demo/stage/      stage                    (127.0.0.1:8020)
                      /demo/contract/   surfaces.contract --live (127.0.0.1:8092)
                      /                 Jitsi web                (127.0.0.1:8000)
Robo: Chrome (CDP 9223) -> tap -> STT (5051, PCM 5053) -> consumer -> project dir
Design partner: surfaces.contract --fixtures meeting (127.0.0.1:8093, always on, outside demo.sh)
```

Two static pages, one session endpoint (ADR 0021). `demo/page/index.html`
is call-only, full screen; it is the link handed to participants.
`demo/page/split.html` is the call and the stage side by side, for
single-screen setups. Both ask for a display name once (cookie
`ct_demo_name`, `path=/` so it covers either page) and call the one
session endpoint (`api/session?t=<demo token>&name=<name>`), which
returns `{domain, room, jwt, stage_path}`. The JWT is minted server side;
nothing is minted in the browser. A wrong token gets an empty 404.
`split.html` is served from a different nginx location than the session
endpoint it calls, so it addresses it by absolute path rather than a
relative one, and resolves `stage_path` against that same prefix.

## Commands

```
demo/demo.sh start  [--all]     # --all also (re)starts the Jitsi containers
demo/demo.sh status
demo/demo.sh stop   [--all]     # --all also takes the Jitsi containers down
```

`start` writes `$XDG_RUNTIME_DIR/cothinker-demo/run.env` (one run timestamp
shared by STT and tap, because the per-track sidecar path embeds it),
`contract.env` (the current session directory, resolved as the newest
subfolder of `project/sessions/`, read by the contract unit as
`DEMO_SESSION_DIR`) and mints Robo's join URL into `robo.env` (mode 0600) at
that moment. Units are started in the order session, stage, stt, chrome,
tap, consumer, contract. The tap only reports `listening` after someone
unmutes, so the start check waits for the process and the CDP port, not for
that line. `stop` exits non-zero if a demo Chrome, a unit or a socket on the
demo ports is left over.

`cothinker-demo-contract-fixtures` (127.0.0.1:8093) is not in `DEMO_UNITS`:
it serves the fixture scenario for the design partner to poke at, runs
always (`Restart=always`), and `start`/`stop` never touch it.

## Install

`demo/systemd/*.service` are templates. Replace `@DEMO_HOME@` (state dir
with `repo/`, `project/`, `audio/`, `logs/`), `@REPO@` (CoThinker checkout)
`@KIT@` (directory that contains this `demo/` folder; set `DEMO_KIT` in
`demo.env` if it is not the checkout) and `@HERMES_PY@` (Python that can import the hermes worker), copy them to
`~/.config/systemd/user/`, run `systemctl --user daemon-reload`. Serve
`demo/page/index.html` under `/meeting/` and `demo/page/split.html` under
`/meeting-split/` (one nginx location each) and proxy the paths above. Run
`git init` once in `@DEMO_HOME@/project`.

## Environment files (names only, values never committed)

`~/.config/cothinker-demo/secrets.env` (mode 0600, read by the units):
`JITSI_JWT_SECRET`, `DEMO_TOKEN`, `STAGE_TOKEN`, `SPEECHMATICS_API_KEY`,
`OPENROUTER_API_KEY`.

`~/.config/cothinker-demo/demo.env` (not secret, `KEY=value` lines only;
read by `demo.sh` and the units): `DEMO_HOME`, `JITSI_DIR`, `DEMO_DOMAIN`,
`DEMO_ROOM`, `DEMO_PUBLIC_URL` (https origin of the link), `HERMES_PYTHONPATH`;
optional: `DEMO_REPO`, `DEMO_UNITS`, `JWT_ISS`, `ROBO_TTL_H`,
`CHROME_EXTRA_ARGS` (e.g. `--ignore-certificate-errors` while the
certificate is self-signed), `HERMES_PROFILE_DIR`, `LANE_MODEL`,
`LANE_PROVIDER`.

Jitsi needs `config.disableInitialGUM=false` (the image default is true, so
nobody would get a microphone track); both pages set it in `configOverwrite`.

## Acceptance helpers (`demo/acceptance/`)

```
python3 make_tts.py --out-dir OUT           # alex.wav, sam.wav, script.json
python3 participant.py --url-file URLFILE --name Alex --wav OUT/alex.wav --seconds 300 --cdp-port 9301
python3 participant.py --url-file URLFILE --name Sam  --wav OUT/sam.wav  --seconds 300 --cdp-port 9302
python3 score.py --transcript <project>/sessions/<date>-<project>/protokoll.md --script OUT/script.json
```

`score.py` reads the lines `- **HH:MM** *Speaker:* text` of the
`## Rohtranskript` section and exits 0 when at least 90 % of the lines that
contain a script keyword carry the right speaker. Start both participants
at the same moment, each with its own `--cdp-port`; the WAVs begin with a silent lead-in for join jitter.

## Rollback

```
demo/demo.sh stop --all
sudo docker compose -f <jitsi dir>/docker-compose.yml down
sudo rm /etc/nginx/sites-enabled/<demo vhost>      # the symlink only
sudo nginx -t
sudo systemctl reload nginx
sudo sed -i '/# cothinker-demo$/d' /etc/hosts      # remove the hosts override line
```
