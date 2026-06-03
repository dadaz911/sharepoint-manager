#!/bin/bash
# health-check.sh — reporte de salud de sharepoint-manager. Uso manual o timer semanal.
# Mismo formato que rpi3/maintenance/health-check.sh (ok/warn/crit, exit = nº de issues).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=/dev/null
[ -f "$REPO_DIR/config.env" ] && source "$REPO_DIR/config.env"

CDP_PORT="${CDP_PORT:-9222}"
TOKEN_FILE="${TOKEN_FILE:-/home/daniel/Desktop/Cargue a Onedrive/.token}"
TOKEN_WARN_MIN=20   # avisar si al token le quedan menos de estos minutos

ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
crit() { echo "  [CRIT] $*"; ISSUES=$((ISSUES+1)); }

ISSUES=0
echo "=== Health Check: sharepoint-manager @ $(hostname) $(date '+%Y-%m-%d %H:%M') ==="

# Units
for u in sharepoint-xvfb.service sharepoint-chrome.service sharepoint-daemon.service; do
  if systemctl --user is-active --quiet "$u"; then ok "Unit activa: $u"
  else crit "Unit caída: $u"; fi
done
for t in sharepoint-watchdog.timer sharepoint-healthcheck.timer; do
  if systemctl --user is-active --quiet "$t"; then ok "Timer activo: $t"
  else warn "Timer inactivo: $t"; fi
done

# Chrome CDP
if curl -fsS -m 3 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; then
  ok "Chrome CDP :$CDP_PORT responde"
else
  crit "Chrome CDP :$CDP_PORT no responde"
fi

# Token
if [ -f "$TOKEN_FILE" ]; then
  MIN=$(python3 - "$TOKEN_FILE" <<'PY' 2>/dev/null || echo -99999
import sys, json, base64, datetime
try:
    t = open(sys.argv[1]).read().strip()
    p = t.split('.')[1]; p += '=' * (-len(p) % 4)
    exp = json.loads(base64.urlsafe_b64decode(p))['exp']
    print(int((datetime.datetime.fromtimestamp(int(exp)) - datetime.datetime.now()).total_seconds() / 60))
except Exception:
    print(-99999)
PY
)
  if   [ "$MIN" -le 0 ]; then crit "Token VENCIDO (corre: spm.sh login si persiste)"
  elif [ "$MIN" -lt "$TOKEN_WARN_MIN" ]; then warn "Token expira en ${MIN} min (el daemon debería refrescarlo)"
  else ok "Token válido (${MIN} min restantes)"
  fi
else
  crit "Token no encontrado: $TOKEN_FILE"
fi

echo ""
echo "Resultado: $ISSUES problemas críticos"
[ "$ISSUES" -gt 0 ] && notify-send --urgency=critical "SharePoint Manager" "Health check: $ISSUES problemas críticos" 2>/dev/null
exit "$ISSUES"
