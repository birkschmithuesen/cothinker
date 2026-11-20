#!/usr/bin/env bash
# Publishes the stage: <domain>/buehne/
#
# Run as ONE command on the web server as a user with sudo rights:
#
#   BUEHNE_ZIEL=http://<stage-server-ip>:8020/ sudo -E bash setup-nginx.sh
#
# Without BUEHNE_ZIEL the script asks for the address interactively.
# Idempotent: running it more than once changes nothing if the block is
# already there.
set -euo pipefail

# Path to the nginx site — set via BUEHNE_CONF.
CONF="${BUEHNE_CONF:-}"
if [[ -z $CONF ]]; then
  read -rp "Path to the nginx site (e.g. /etc/nginx/sites-available/my-site): " CONF
fi
MARKER="location /buehne/"

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo -E bash $0" >&2
  exit 1
fi

# Target address: from the environment or interactively. Deliberately NOT
# stored in the repo.
TARGET="${BUEHNE_ZIEL:-}"
if [[ -z $TARGET ]]; then
  read -rp "Address of the stage server (e.g. http://192.0.2.5:8020/): " TARGET
fi
[[ -n $TARGET ]] || { echo "ERROR: no target address." >&2; exit 1; }
[[ $TARGET == */ ]] || TARGET="$TARGET/"

[[ -f $CONF ]] || { echo "ERROR: $CONF not found." >&2; exit 1; }

if grep -q "$MARKER" "$CONF"; then
  echo "The block already exists — nothing to do."
  nginx -t && systemctl reload nginx
  echo "nginx reloaded."
  exit 0
fi

BACKUP="${CONF}.bak-$(date +%Y%m%d-%H%M%S)"
cp -a "$CONF" "$BACKUP"
echo "Backup: $BACKUP"

# Insert before the LAST closing brace of the file (end of the 443 block).
LAST_LINE=$(grep -n '^}' "$CONF" | tail -1 | cut -d: -f1)
[[ -n ${LAST_LINE:-} ]] || { echo "ERROR: end of server block not found." >&2; exit 1; }

BLOCK=$(cat <<EOF

    # --- Meeting stage (live protocol) ---
    location /buehne/ {
        proxy_pass $TARGET;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout    30s;
    }
EOF
)

TMP=$(mktemp)
head -n $((LAST_LINE - 1)) "$CONF" >  "$TMP"
printf '%s\n' "$BLOCK"             >> "$TMP"
tail -n +$LAST_LINE "$CONF"        >> "$TMP"
cp "$TMP" "$CONF"; rm -f "$TMP"

if ! nginx -t; then
  echo "nginx test FAILED — restoring backup." >&2
  cp -a "$BACKUP" "$CONF"
  nginx -t && systemctl reload nginx || true
  exit 1
fi

systemctl reload nginx
echo "nginx reloaded."

sleep 1
CHECK_URL="${BUEHNE_URL:-}"
if [[ -n $CHECK_URL ]]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$CHECK_URL" || echo 000)
else
  CODE=skipped
fi
echo
if [[ $CODE == skipped ]]; then
  echo "✅ nginx reconfigured. Check with: curl -sI <your-domain>/buehne/"
elif [[ $CODE == 200 ]]; then
  echo "✅ DONE:  $CHECK_URL   (HTTP $CODE)"
else
  echo "⚠️  nginx is running, but the stage answers with HTTP $CODE."
  echo "    Is the service running on the vServer?  ss -tlnp | grep 8020"
fi
