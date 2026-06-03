#!/bin/bash
# deploy.sh — despliegue GOLD del lado OAuth de sharepoint-manager.
#
# Arquitectura (browser-less):
#   - Pi (raspberrypi3): token_refresher.py (sharepoint-refresher.service) refresca el token
#     OAuth cada ~50 min. Ver pi/ y MANUAL.md.
#   - gold (este host): notificador de salud — avisa SOLO si el token deja de refrescarse.
#   - carbon / cualquier host de carga: jala el token del Pi y corre los uploaders.
#
# Este script instala la pieza de gold y RETIRA el stack de navegador obsoleto (cutover a OAuth).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

echo "→ Retirando stack de navegador obsoleto (cutover a OAuth)..."
for old in sharepoint-xvfb.service sharepoint-chrome.service sharepoint-daemon.service \
           sharepoint-watchdog.service sharepoint-watchdog.timer \
           sharepoint-healthcheck.service sharepoint-healthcheck.timer \
           sharepoint-token.service sharepoint-wayland.service; do
  systemctl --user disable --now "$old" 2>/dev/null || true
  rm -f "$UNIT_DIR/$old"
done

echo "→ Instalando notificador de salud OAuth..."
chmod +x "$REPO_DIR"/bin/*.sh 2>/dev/null || true
for unit in "$REPO_DIR"/systemd/sharepoint-oauth-health.service "$REPO_DIR"/systemd/sharepoint-oauth-health.timer; do
  ln -sf "$unit" "$UNIT_DIR/$(basename "$unit")"
done
systemctl --user daemon-reload
systemctl --user enable --now sharepoint-oauth-health.timer

echo ""
echo "✅ gold listo. El refresher OAuth corre en la Pi; el cargue en carbon."
echo "   Salud:     systemctl --user list-timers sharepoint-oauth-health.timer"
echo "   Re-login:  ssh ${PI_HOST:-raspberrypi3} 'python3 ~/sharepoint-token/token_refresher.py login'"
echo "   Ver MANUAL.md para el despliegue del refresher en la Pi y los cargues en carbon."
