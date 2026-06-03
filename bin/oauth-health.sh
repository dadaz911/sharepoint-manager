#!/bin/bash
# oauth-health.sh — Vigila la salud del token OAuth (lo refresca el Pi) y notifica SOLO si
# falla de verdad de forma SOSTENIDA: el refresh token murió y hace falta re-login device-code
# ("ojalá no suceda"). Con settling + cooldown para no ser ruidoso ante baches transitorios.
# Pensado como timer de usuario en gold (cada 15 min). Reemplaza al watchdog del navegador.
set -uo pipefail

PI_HOST="${PI_HOST:-raspberrypi3}"
PI_TOKEN="${PI_TOKEN:-/home/daniel/.cache/spm/.token}"
RUN="${XDG_RUNTIME_DIR:-/tmp}"
FAILC="$RUN/spm-oauth.fail-count"
ALERTED="$RUN/spm-oauth.alerted"
SETTLE=3            # ~45 min de fallo sostenido antes de alertar (evita transitorios)
COOLDOWN=21600     # 6 h entre alertas

notif() { notify-send --urgency=critical "SharePoint OAuth" "$1" 2>/dev/null || true; }

# Minutos de validez del token que mantiene el Pi (lectura remota, sin escribir nada local).
mins=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_HOST" "cat '$PI_TOKEN' 2>/dev/null" 2>/dev/null | python3 -c '
import sys, json, base64, datetime
try:
    t=sys.stdin.read().strip(); p=t.split(".")[1]; p+="="*(-len(p)%4)
    exp=json.loads(base64.urlsafe_b64decode(p))["exp"]
    print(int((datetime.datetime.fromtimestamp(int(exp))-datetime.datetime.now()).total_seconds()/60))
except Exception:
    print(-99999)
')

if [ "${mins:-X}" -gt 0 ] 2>/dev/null; then
    rm -f "$FAILC" "$ALERTED"     # OAuth sano: limpiar estado
    exit 0
fi

# Fallo (token vencido o Pi inalcanzable): contar, esperar a que sea sostenido.
c=$(( $(cat "$FAILC" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$FAILC"
[ "$c" -lt "$SETTLE" ] && exit 0

# Fallo sostenido: alertar una sola vez por cooldown.
if [ -f "$ALERTED" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$ALERTED" 2>/dev/null || echo 0) ))
    [ "$age" -lt "$COOLDOWN" ] && exit 0
fi
notif "El token de SharePoint dejó de refrescarse. Re-login (device-code): ssh ${PI_HOST} 'python3 ~/sharepoint-token/token_refresher.py login'"
touch "$ALERTED"
exit 0
