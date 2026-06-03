#!/bin/bash
# pull-token.sh — gold JALA el access token de SharePoint desde la Pi (raspberrypi3) por rsync.
# El refresher OAuth vive en la Pi (siempre encendida); gold corre esto como timer cada ~10 min.
# Se eligió pull (no push) porque gold->Pi SSH ya funciona y gold es una laptop intermitente:
# al despertar, jala el token más fresco; no hay pushes fallidos a una laptop apagada.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=/dev/null
[ -f "$REPO_DIR/config.env" ] && source "$REPO_DIR/config.env"

PI_HOST="${PI_HOST:-raspberrypi3}"
PI_TOKEN="${PI_TOKEN:-/home/daniel/.cache/spm/.token}"
TOKEN_FILE="${TOKEN_FILE:-/home/daniel/Desktop/Cargue a Onedrive/.token}"
RUN="${XDG_RUNTIME_DIR:-/tmp}"
STALE_FLAG="$RUN/spm-token-stale.flag"

notif() { notify-send --urgency="$1" "SharePoint Token" "$2" 2>/dev/null || true; }

mkdir -p "$(dirname "$TOKEN_FILE")"
if ! rsync -e "ssh -o BatchMode=yes -o ConnectTimeout=10" "$PI_HOST:$PI_TOKEN" "$TOKEN_FILE" 2>/dev/null; then
  notif critical "No pude jalar el token del Pi ($PI_HOST). ¿Pi caído / Tailscale?"
  echo "pull FALLÓ (rsync)"; exit 0
fi
chmod 600 "$TOKEN_FILE" 2>/dev/null || true

mins=$(python3 - "$TOKEN_FILE" <<'PY' 2>/dev/null || echo -99999
import sys, json, base64, datetime
try:
    t = open(sys.argv[1]).read().strip(); p = t.split('.')[1]; p += '=' * (-len(p) % 4)
    print(int((datetime.datetime.fromtimestamp(int(json.loads(base64.urlsafe_b64decode(p))['exp'])) - datetime.datetime.now()).total_seconds() / 60))
except Exception: print(-99999)
PY
)
if [ "$mins" -le 0 ] 2>/dev/null; then
  notif critical "Token vencido tras jalar del Pi. Re-login: ssh $PI_HOST 'python3 ~/sharepoint-token/token_refresher.py login'"
  touch "$STALE_FLAG"
  echo "token VENCIDO (${mins} min) — re-login requerido en el Pi"
else
  rm -f "$STALE_FLAG"
  echo "token OK (${mins} min restantes)"
fi
