#!/usr/bin/env bash
# Pre-Commit-Wächter: verhindert, dass Netz-Interna, Schlüssel oder
# vertrauliche Inhalte in ein öffentliches Repo rutschen.
#
# Einbau:  cp scripts/pre-commit-leak-guard.sh .git/hooks/pre-commit
#          chmod +x .git/hooks/pre-commit
#
# Umgehen (nur bewusst!):  git commit --no-verify
set -uo pipefail

# Muster, die in einem oeffentlichen Repo nichts zu suchen haben.
# Absichtlich generisch — nicht die konkreten Werte, die wir schuetzen wollen.
MUSTER=(
  '(^|[^0-9])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}' # Tailscale CGNAT
  'ssh-(ed25519|rsa|dss) AAAA'                       # SSH-Schluessel
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'               # private Schluessel
  '(api[_-]?key|secret|passwor[dt]|token)[[:space:]]*[:=][[:space:]]*[A-Za-z0-9/_+-]{16,}'
  'ssh [a-z_][a-z0-9_-]*@[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'  # ssh user@ip
  '/mnt/HC_Volume_[0-9]+'                            # Serverpfade
  '/home/[a-z_][a-z0-9_-]*/hermes-shared'
)

# Dateitypen, die nie ins Repo gehoeren.
# `vorschlag-<datum>.jsonl` steht hier aus demselben Grund wie das
# Protokoll: die Datei enthaelt Auftraege woertlich aus der Runde und
# Meldungstexte mit Betraegen, Fristen und Namen.
VERBOTEN='\.(wav|mp3|m4a|ogg|opus|flac|webm)$|(^|/)(rohmaterial|transkripte)/|(^|/)protokoll-.*\.md$|(^|/)vorschlag-.*\.jsonl$|(^|/)\.env$'

STAGED=$(git diff --cached --name-only --diff-filter=ACM)
[[ -z $STAGED ]] && exit 0

FEHLER=0

while IFS= read -r datei; do
  [[ -z $datei ]] && continue
  if printf '%s' "$datei" | grep -qEi "$VERBOTEN"; then
    echo "🔴 BLOCKIERT (Dateityp/Pfad): $datei" >&2
    FEHLER=1
    continue
  fi
  [[ -f $datei ]] || continue
  for m in "${MUSTER[@]}"; do
    if treffer=$(git diff --cached -U0 -- "$datei" | grep -E '^\+' | grep -nEi -e "$m"); then
      echo "🔴 BLOCKIERT (Muster) in $datei:" >&2
      printf '%s\n' "$treffer" | head -3 | sed 's/^/     /' >&2
      FEHLER=1
    fi
  done
done <<< "$STAGED"

if [[ $FEHLER -ne 0 ]]; then
  cat >&2 <<'HINWEIS'

Der Commit wurde gestoppt: oben stehende Zeilen enthalten Daten, die in einem
oeffentlichen Repo nichts zu suchen haben.

  - Adressen/IPs  -> als Variable oder Platzhalter, z.B. $BUEHNE_ZIEL
  - Schluessel    -> gar nicht ins Repo
  - Rohmaterial   -> in den Vault, nicht hierher

Bewusste Ausnahme:  git commit --no-verify
HINWEIS
  exit 1
fi
exit 0
