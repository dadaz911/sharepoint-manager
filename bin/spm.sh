#!/bin/bash
# spm.sh — control de SharePoint Manager (SharePoint Manager control).
#
#   spm.sh start     arranca el servicio (Xvfb + Chrome real + daemon) vía systemd
#   spm.sh stop      lo detiene
#   spm.sh restart   reinicia SOLO el daemon (reiniciar Chrome perdería la sesión M365)
#   spm.sh status    estado de units + Chrome CDP + URL de la pestaña + token
#   spm.sh refresh   fuerza un refresco de token
#   spm.sh health    corre el health-check
#   spm.sh vnc       abre x11vnc contra el Chrome de larga vida (para mirar/depurar)
#   spm.sh login     LOGIN M365 vía VNC, EN EL MISMO Chrome (no lo reinicia → preserva sesión)
#
# MODELO KEEP-ALIVE: M365 no re-autentica en silencio un Chrome reiniciado, así que el Chrome
# real (bajo Xvfb) se mantiene vivo y el login se hace in situ vía VNC.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=/dev/null
[ -f "$REPO_DIR/config.env" ] && source "$REPO_DIR/config.env"

CDP_PORT="${CDP_PORT:-9222}"
CHROME_PROFILE="${CHROME_PROFILE:-$HOME/.config/chrome-sharepoint}"
ONEDRIVE_URL="${ONEDRIVE_URL:-https://shdgov-my.sharepoint.com}"
SPM_DISPLAY="${SPM_DISPLAY:-:77}"
VNC_PORT="${VNC_PORT:-5900}"

VENV_PY="$REPO_DIR/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3)"

uc() { systemctl --user "$@"; }
_cdp_up() { curl -fsS -m 3 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; }
_wait_cdp() { curl --retry 25 --retry-delay 1 --retry-all-errors -fsS -m 3 -o /dev/null "http://localhost:${CDP_PORT}/json/version" 2>/dev/null; }
_tab_url() { curl -fsS -m 3 "http://localhost:${CDP_PORT}/json" 2>/dev/null | "$VENV_PY" -c "import sys,json; ts=[t for t in json.load(sys.stdin) if t.get('type')=='page']; print(ts[0]['url'] if ts else '(sin pestaña)')" 2>/dev/null || echo "(sin CDP)"; }
_start_vnc() {
  pkill -f "x11vnc.*-rfbport ${VNC_PORT}" 2>/dev/null || true
  # WAYLAND_DISPLAY debe deshabilitarse: x11vnc detecta la sesión Wayland del sistema y
  # se niega a arrancar, aunque le apuntemos al display X11 (:77) de Xvfb.
  env -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 \
    setsid x11vnc -display "$SPM_DISPLAY" -localhost -rfbport "$VNC_PORT" -nopw -forever -quiet >/dev/null 2>&1 &
  local i
  for i in $(seq 1 10); do
    if ss -tlnH 2>/dev/null | grep -q ":${VNC_PORT}"; then return 0; fi
  done
  return 1
}
_stop_vnc() { pkill -f "x11vnc.*-rfbport ${VNC_PORT}" 2>/dev/null || true; }
_open_viewer() {
  local v
  for v in vncviewer gvncviewer xtigervncviewer; do
    if command -v "$v" >/dev/null 2>&1; then DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 setsid "$v" "localhost:${VNC_PORT}" >/dev/null 2>&1 & echo "$v"; return 0; fi
  done
  if command -v remmina >/dev/null 2>&1; then DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 setsid remmina -c "vnc://localhost:${VNC_PORT}" >/dev/null 2>&1 & echo "remmina"; return 0; fi
  return 1
}

case "${1:-}" in
  start)
    uc start sharepoint-xvfb.service sharepoint-chrome.service sharepoint-daemon.service
    uc --no-pager status sharepoint-daemon.service || true
    ;;
  stop)
    uc stop sharepoint-daemon.service sharepoint-chrome.service sharepoint-xvfb.service
    ;;
  restart)
    echo "Nota: reiniciar Chrome perdería la sesión M365 (requeriría 'spm.sh login')."
    echo "Reiniciando solo el daemon (refresca desde el Chrome ya logueado)..."
    uc restart sharepoint-daemon.service
    ;;
  status)
    echo "=== Units ==="
    for u in sharepoint-xvfb sharepoint-chrome sharepoint-daemon sharepoint-watchdog.timer sharepoint-healthcheck.timer; do
      printf "  %-28s %s / %s\n" "$u" "$(uc is-enabled "$u" 2>/dev/null)" "$(uc is-active "$u" 2>/dev/null)"
    done
    echo ""
    echo "=== Chrome CDP (:$CDP_PORT) ==="
    if _cdp_up; then echo "  ✅ activo  | pestaña: $(_tab_url)"; else echo "  ❌ inactivo"; fi
    echo "  (si la pestaña está en login.microsoftonline.com → corre: spm.sh login)"
    echo ""
    echo "=== Token ==="
    "$VENV_PY" "$REPO_DIR/token_daemon.py" --status 2>/dev/null || echo "  (no se pudo leer)"
    ;;
  refresh)
    "$VENV_PY" "$REPO_DIR/token_daemon.py" --once
    ;;
  health)
    exec "$SCRIPT_DIR/health-check.sh"
    ;;
  vnc)
    uc start sharepoint-xvfb.service sharepoint-chrome.service 2>/dev/null || true
    _start_vnc && echo "x11vnc en localhost:${VNC_PORT} (display $SPM_DISPLAY). Pará con: pkill -f 'x11vnc.*${VNC_PORT}'" || echo "no se pudo iniciar x11vnc"
    ;;
  login)
    echo "→ Login M365 vía VNC (necesario tras reboot, o ~cada 90 días)."
    echo "→ Asegurando que el Chrome de larga vida está arriba (NO se reinicia)..."
    uc start sharepoint-xvfb.service sharepoint-chrome.service 2>/dev/null || true
    _cdp_up || _wait_cdp || true

    if ! _start_vnc; then echo "  ⚠️ No se pudo iniciar x11vnc (display $SPM_DISPLAY)"; exit 1; fi
    # Cerrar el VNC pase lo que pase (EOF/Ctrl-C/cierre). NO tocamos Chrome.
    trap '_stop_vnc' EXIT
    trap 'exit 130' INT TERM HUP

    echo ""
    if viewer="$(_open_viewer)"; then
      echo "  Abrí '$viewer' apuntando a localhost:${VNC_PORT} — mira tu escritorio y haz login."
    else
      echo "  Conéctate con un visor VNC a  localhost:${VNC_PORT}  y haz login en SharePoint."
      echo "  (sin visor instalado: sudo apt install tigervnc-viewer  — o usa Remmina)"
    fi
    echo "  Remoto: en tu equipo →  ssh -L ${VNC_PORT}:localhost:${VNC_PORT} gold  y apunta el visor a localhost:${VNC_PORT}"
    echo ""
    read -rp "Cuando ya veas tus archivos en SharePoint, pulsa ENTER para capturar el token... " _ || true
    echo "→ Capturando token..."
    "$VENV_PY" "$REPO_DIR/token_daemon.py" --once || echo "  ⚠️  No se capturó. ¿Login completo (viste tus archivos)?"
    # Arrancar el daemon (sin reiniciar Chrome) para mantener el token fresco.
    uc start sharepoint-daemon.service 2>/dev/null || true
    echo ""
    "$VENV_PY" "$REPO_DIR/token_daemon.py" --status 2>/dev/null || true
    # _stop_vnc lo hace el trap EXIT.
    ;;
  *)
    grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -18
    exit 1
    ;;
esac
