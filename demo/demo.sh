#!/usr/bin/env bash
# Start / inspect / stop the CoThinker demo stack (Jitsi + STT + tap + stage).
#
#   demo/demo.sh start  [--all]   --all also (re)starts the Jitsi containers
#   demo/demo.sh status
#   demo/demo.sh stop   [--all]   --all also takes the Jitsi containers down
#
# Non-secret settings come from ${DEMO_ENV:-$HOME/.config/cothinker-demo/demo.env}
# (KEY=value lines): DEMO_HOME, JITSI_DIR, optional DEMO_REPO, DEMO_UNITS.
# Secrets stay in secrets.env and are only read by the units (and, for
# minting the bot's join URL, by this script). Nothing secret is printed.
set -uo pipefail

usage() {
  cat <<'EOF'
usage: demo.sh start [--all] | status | stop [--all] | --help
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  start|status|stop) CMD=$1; shift ;;
  *) usage >&2; exit 2 ;;
esac
ALL=0
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    *) usage >&2; exit 2 ;;
  esac
done

CONF_DIR=${DEMO_CONF_DIR:-$HOME/.config/cothinker-demo}
DEMO_ENV=${DEMO_ENV:-$CONF_DIR/demo.env}
if [ ! -r "$DEMO_ENV" ]; then
  echo "demo.sh: cannot read $DEMO_ENV" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
. "$DEMO_ENV"
set +a
: "${DEMO_HOME:?DEMO_HOME missing in demo.env}"
: "${JITSI_DIR:?JITSI_DIR missing in demo.env}"
DEMO_REPO=${DEMO_REPO:-$DEMO_HOME/repo}
# Directory that holds this demo/ kit (may differ from the runtime checkout).
DEMO_KIT=${DEMO_KIT:-$DEMO_REPO}
DEMO_UNITS=${DEMO_UNITS:-"session stage stt chrome tap consumer contract"}
JITSI_WEB=${JITSI_WEB:-http://127.0.0.1:8000/}
RUN_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/cothinker-demo
LOG_DIR=$DEMO_HOME/logs
# 8092 is the contract unit (DEMO_UNITS, stopped with the rest); 8093 is
# contract-fixtures, which runs always and is never started/stopped here --
# it must stay out of PORT_RE or `stop` would wrongly flag it as leaking.
PORT_RE=':(5051|5053|8020|8091|8092|9223)\b'

fail=0
unit() { echo "cothinker-demo-$1.service"; }
sc() { systemctl --user "$@"; }

wait_for() { # <seconds> <description> <command...>
  local n=$1 what=$2
  shift 2
  while [ "$n" -gt 0 ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
    n=$((n - 1))
  done
  echo "FAIL: timeout waiting for $what" >&2
  return 1
}

web_up() { curl -sf -o /dev/null "$JITSI_WEB"; }
stt_up() { curl -sf -o /dev/null http://127.0.0.1:5051/status; }
cdp_up() { curl -sf -o /dev/null http://127.0.0.1:9223/json/version; }
unit_active() { sc is-active --quiet "$(unit "$1")"; }
tap_alive() { # active now, and still active after a short settle time
  unit_active tap && sleep 3 && unit_active tap
}

start_all() {
  if [ "$ALL" = 1 ] || ! web_up; then
    sudo -n docker compose --project-directory "$JITSI_DIR" up -d >/dev/null \
      || { echo "FAIL: docker compose up" >&2; return 1; }
    wait_for 120 "Jitsi web ($JITSI_WEB)" web_up || return 1
  fi

  mkdir -p "$RUN_DIR" "$LOG_DIR" || return 1
  chmod 700 "$RUN_DIR"
  # One timestamp for STT and tap: the sidecar path embeds it.
  # If STT is already running, keep its run.env: a later restart of STT or tap
  # must read the same timestamp as the process that is still alive.
  local run_ts d t
  if unit_active stt && [ -s "$RUN_DIR/run.env" ]; then
    echo "stt already running: keeping existing run.env"
  else
    run_ts=$(date +%Y%m%d-%H%M%S)
    d="${run_ts:0:4}-${run_ts:4:2}-${run_ts:6:2}"
    t="${run_ts:9:6}"
    mkdir -p "$DEMO_HOME/audio/$d" || return 1
    printf 'DEMO_RUN_TS=%s\nDEMO_SIDECAR=%s\n' "$run_ts" \
      "$DEMO_HOME/audio/$d/bbb-$t-tracks.json" >"$RUN_DIR/run.env" || return 1
  fi

  # The bot's join URL is minted now, never kept long-lived.
  (
    umask 077
    set -a
    # shellcheck disable=SC1091
    . "$CONF_DIR/secrets.env"
    set +a
    python3 "$DEMO_KIT/demo/session_server.py" --mint-robo "$RUN_DIR/robo.env"
  ) || { echo "FAIL: minting the bot join URL" >&2; return 1; }

  # COTHINKER_PROJECT_DIR mirrors the consumer unit's own environment (a
  # deployment may point the project at a separate repo, e.g. a wiki
  # clone) -- read it here too, with the same default the consumer falls
  # back to, so the git identity below is set on the SAME directory the
  # consumer is about to write into rather than always the plain project
  # folder.
  local project_dir=${COTHINKER_PROJECT_DIR:-$DEMO_HOME/project}

  git -C "$project_dir" config user.name >/dev/null 2>&1 \
    || git -C "$project_dir" config user.name "CoThinker Demo"
  git -C "$project_dir" config user.email >/dev/null 2>&1 \
    || git -C "$project_dir" config user.email "demo@example.invalid"

  # The contract unit (live mode) needs this run's session directory, but
  # only the consumer picks the name (date + title slug) when it opens it
  # -- after it has already started. Guessing it here (the newest
  # subfolder of sessions/ at the moment contract starts) could pick a
  # prepared future folder or an older one instead, so this names a
  # pointer file in RUN_DIR for the consumer to write its session's path
  # into (COTHINKER_SESSION_POINTER, cothinker-demo-consumer.service) and
  # has the contract unit read that, re-reading on change
  # (--live-pointer, gateway/consumer.py write_session_pointer, issue
  # #161) -- never a guess, and it follows a restart into a new session
  # without a restart of its own.
  printf 'DEMO_SESSION_POINTER=%s\n' "$RUN_DIR/session.path" >"$RUN_DIR/contract.env" || return 1

  local u
  for u in $DEMO_UNITS; do
    sc start "$(unit "$u")" || { echo "FAIL: start $u" >&2; return 1; }
    case "$u" in
      stt) wait_for 60 "STT /status" stt_up || return 1 ;;
      chrome) wait_for 60 "Chrome CDP" cdp_up || return 1 ;;
      # The tap only reports "listening" once somebody unmutes, so do NOT
      # wait for that: the process being up and the CDP answering is enough.
      tap) tap_alive || { echo "FAIL: tap not staying up" >&2; return 1; } ;;
      *) unit_active "$u" || sleep 2 ;;
    esac
  done
  echo "demo up in ${SECONDS}s"
}

do_status() {
  local u
  for u in $DEMO_UNITS; do
    printf '%-10s %s\n' "$u" "$(sc is-active "$(unit "$u")" 2>&1)"
  done
  if stt_up; then echo "stt /status: ok"; else echo "stt /status: down"; fi
  if web_up; then echo "jitsi web: ok"; else echo "jitsi web: down"; fi
  # numbers only: chunks / tracks / rms of the last tap line
  grep '"event": "tap"' "$LOG_DIR/tap.log" 2>/dev/null | tail -n 1 \
    | grep -o -E '"(chunks|tracks|rms)": *[0-9.]+' | tr '\n' ' '
  echo
  echo "listening:"
  ss -tlnH 2>/dev/null | grep -E "$PORT_RE" | awk '{print $4}'
}

check_stopped() {
  sleep 2
  local u
  for u in $DEMO_UNITS; do
    if unit_active "$u"; then echo "FAIL: $u still active" >&2; fail=1; fi
  done
  if pgrep -f "$DEMO_HOME/chrome-tap" >/dev/null; then
    echo "FAIL: demo chrome still running; killing" >&2
    pkill -f "$DEMO_HOME/chrome-tap"
    fail=1
  fi
  local n
  n=$( { ss -tlnH; ss -tnH state established; } 2>/dev/null | grep -c -E "$PORT_RE")
  if [ "$n" != 0 ]; then
    echo "FAIL: $n sockets still open on demo ports" >&2
    fail=1
  fi
}

do_stop() {
  local rev="" u
  for u in $DEMO_UNITS; do rev="$u $rev"; done
  for u in $rev; do sc stop "$(unit "$u")" || fail=1; done
  # A unit that was SIGKILLed after its stop timeout stays "failed"; the
  # stack is stopped, so clear that state.
  for u in $DEMO_UNITS; do sc reset-failed "$(unit "$u")" 2>/dev/null || true; done
  check_stopped
  if [ "$ALL" = 1 ]; then
    sudo -n docker compose --project-directory "$JITSI_DIR" down >/dev/null \
      || { echo "FAIL: docker compose down" >&2; fail=1; }
  fi
}

case "$CMD" in
  start) start_all || fail=1 ;;
  status) do_status ;;
  stop) do_stop ;;
esac
exit "$fail"
