#!/bin/bash
# chrome-xvfb.sh — Chrome REAL (no headless) sobre un display Xvfb. Fuente de token vía CDP.
#
# MODELO KEEP-ALIVE: M365 (Conditional Access del tenant SHD) NO re-autentica en silencio
# un Chrome reiniciado — ni headless ni real (verificado empíricamente). Por eso este Chrome
# se mantiene vivo y el login se hace EN SITIO vía VNC (spm.sh login), sin relanzarlo.
# Se usa Chrome real (UA normal, no "HeadlessChrome") para que el tenant lo trate como
# navegador legítimo. ExecStart de sharepoint-chrome.service (foreground/exec).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=/dev/null
[ -f "$REPO_DIR/config.env" ] && source "$REPO_DIR/config.env"

CDP_PORT="${CDP_PORT:-9222}"
CHROME_PROFILE="${CHROME_PROFILE:-$HOME/.config/chrome-sharepoint}"
ONEDRIVE_URL="${ONEDRIVE_URL:-https://shdgov-my.sharepoint.com}"
export DISPLAY="${SPM_DISPLAY:-:77}"   # provisto por sharepoint-xvfb.service

mkdir -p "$CHROME_PROFILE"
# Limpiar locks stale (perfil copiado entre hosts / PID muerto).
rm -f "$CHROME_PROFILE"/SingletonLock \
      "$CHROME_PROFILE"/SingletonCookie \
      "$CHROME_PROFILE"/SingletonSocket

exec google-chrome \
  --ozone-platform=x11 \
  --remote-debugging-port="$CDP_PORT" \
  --remote-allow-origins="http://localhost:$CDP_PORT,http://127.0.0.1:$CDP_PORT" \
  --user-data-dir="$CHROME_PROFILE" \
  --window-size=1280,1024 --window-position=0,0 \
  --no-first-run \
  --no-default-browser-check \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-features=Translate \
  "$ONEDRIVE_URL"
