#!/bin/bash
# sharepoint-watchdog.sh — mantiene vivo el servicio de token. Corre como timer de
# usuario en gold cada 5 minutos. Mismo espíritu que vpn/vpn-watchdog.sh.
#
# Máquina de estados (por corrida):
#   LOGIN-EN-CURSO  — existe el flag de login manual → no toca nada.
#   CHROME-DOWN     — CDP :9222 no responde → reinicia sharepoint-chrome.
#   DAEMON-DOWN     — sharepoint-daemon inactivo → lo reinicia.
#   TOKEN-OK        — token con margen → limpia contadores/flags, sale.
#   SETTLING        — token vencido con Chrome+daemon arriba, 1ª vez → cuenta y espera.
#   SESION-EXPIRADA — token vencido ≥ THRESHOLD ticks pese a todo sano → la sesión M365
#                     murió: notifica "login requerido" y aplica COOLDOWN para no thrashear.
#
# Invariantes:
#   - Nunca reinicia durante un login manual (flag).
#   - flock evita corridas solapadas.
#   - El flag de sesión-expirada suprime alertas repetidas durante COOLDOWN.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=/dev/null
[ -f "$REPO_DIR/config.env" ] && source "$REPO_DIR/config.env"

CDP_PORT="${CDP_PORT:-9222}"
TOKEN_FILE="${TOKEN_FILE:-/home/daniel/Desktop/Cargue a Onedrive/.token}"

RUN="${XDG_RUNTIME_DIR:-/tmp}"
LOGFILE="$RUN/sharepoint-watchdog.log"
LOCKFILE="$RUN/sharepoint-watchdog.lock"
LOGIN_FLAG="$RUN/spm-login.flag"
EXPIRED_FLAG="$RUN/sharepoint-watchdog.session-expired"
FAIL_COUNT_FILE="$RUN/sharepoint-watchdog.fail-count"

SETTLE_THRESHOLD=2     # ticks de token vencido (con todo sano) antes de declarar sesión muerta
COOLDOWN=21600         # 6 h — supresión de alertas repetidas de login

log()   { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOGFILE"; }
notif() { local u="$1"; shift; notify-send --urgency="$u" "SharePoint Manager" "$*" 2>/dev/null || true; }
uc()    { systemctl --user "$@"; }

_fail()       { cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0; }
_set_fail()   { echo "$1" > "$FAIL_COUNT_FILE"; }
_clear_fail() { rm -f "$FAIL_COUNT_FILE"; }

_cdp_up()     { curl -fsS -m 3 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; }
_daemon_up()  { uc is-active --quiet sharepoint-daemon.service; }

# Minutos restantes del token (entero). -99999 si no hay/no decodifica.
_token_minutes() {
  python3 - "$TOKEN_FILE" <<'PY' 2>/dev/null || echo -99999
import sys, json, base64, datetime
try:
    t = open(sys.argv[1]).read().strip()
    p = t.split('.')[1]; p += '=' * (-len(p) % 4)
    exp = json.loads(base64.urlsafe_b64decode(p))['exp']
    rem = (datetime.datetime.fromtimestamp(int(exp)) - datetime.datetime.now()).total_seconds() / 60
    print(int(rem))
except Exception:
    print(-99999)
PY
}

# ── LOGIN-EN-CURSO ────────────────────────────────────────────────────────────
if [[ -f "$LOGIN_FLAG" ]]; then
  log "LOGIN-EN-CURSO: login manual activo — watchdog en pausa"
  exit 0
fi

# Evitar corridas solapadas
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  log "Otra instancia del watchdog corre — saltando"
  exit 0
fi

# ── CHROME-DOWN ─────────────────────────────────────────────────────────────────
if ! _cdp_up; then
  log "CHROME-DOWN: CDP :$CDP_PORT no responde — reiniciando sharepoint-chrome"
  uc restart sharepoint-chrome.service 2>/dev/null || true
  exit 0   # darle un tick para estabilizar
fi

# ── DAEMON-DOWN ──────────────────────────────────────────────────────────────────
if ! _daemon_up; then
  log "DAEMON-DOWN: sharepoint-daemon inactivo — reiniciando"
  uc restart sharepoint-daemon.service 2>/dev/null || true
  exit 0
fi

# ── Salud del token ──────────────────────────────────────────────────────────────
mins="$(_token_minutes)"

if [[ "$mins" -gt 0 ]]; then
  # TOKEN-OK
  _clear_fail
  rm -f "$EXPIRED_FLAG"
  exit 0
fi

# Token vencido pese a Chrome+daemon sanos → o está por refrescar, o la sesión murió.
# Respetar cooldown de una alerta previa.
if [[ -f "$EXPIRED_FLAG" ]]; then
  mtime=$(stat -c %Y "$EXPIRED_FLAG" 2>/dev/null || echo 0)
  age=$(( $(date +%s) - mtime ))
  if [[ $age -lt $COOLDOWN ]]; then
    log "SESION-EXPIRADA: alerta enviada hace ${age}s (< ${COOLDOWN}s cooldown) — esperando login"
    exit 0   # el watchdog corrió bien; la señal al usuario es la notificación + flag
  fi
  rm -f "$EXPIRED_FLAG"   # cooldown vencido, reevaluar
fi

fail=$(( $(_fail) + 1 ))
_set_fail "$fail"
if [[ $fail -lt $SETTLE_THRESHOLD ]]; then
  # SETTLING — el daemon podría estar refrescando justo ahora
  log "SETTLING: token vencido #${fail}/${SETTLE_THRESHOLD} (Chrome+daemon OK) — esperando refresco"
  exit 0
fi

# SESION-EXPIRADA
log "SESION-EXPIRADA: token vencido ${fail} ticks con Chrome+daemon sanos — sesión M365 caída"
notif critical "Sesión SharePoint expirada — corre: spm.sh login"
touch "$EXPIRED_FLAG"
_clear_fail
exit 0   # el watchdog corrió bien; la señal al usuario es la notificación + flag
