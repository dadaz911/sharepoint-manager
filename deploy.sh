#!/bin/bash
# deploy.sh — despliegue LOCAL (gold) de sharepoint-manager como servicio de usuario.
#
# A diferencia del deploy del repo vpn (que empuja a una Pi remota), aquí "desplegar"
# es: symlinkear las units a ~/.config/systemd/user, recargar, y habilitar+arrancar.
# Idempotente: se puede correr varias veces.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

echo "→ Removiendo units obsoletas que este servicio reemplaza..."
for old in sharepoint-token.service sharepoint-wayland.service; do
  if systemctl --user list-unit-files "$old" >/dev/null 2>&1; then
    systemctl --user disable --now "$old" 2>/dev/null || true
  fi
  rm -f "$UNIT_DIR/$old" && echo "  - $old"
done

echo "→ Symlinkeando units del repo..."
for unit in "$REPO_DIR"/systemd/*.service "$REPO_DIR"/systemd/*.timer; do
  ln -sf "$unit" "$UNIT_DIR/$(basename "$unit")"
  echo "  + $(basename "$unit")"
done

echo "→ Asegurando scripts ejecutables..."
chmod +x "$REPO_DIR"/bin/*.sh "$REPO_DIR"/deploy.sh 2>/dev/null || true

echo "→ daemon-reload..."
systemctl --user daemon-reload

echo "→ Habilitando + arrancando servicio y timers..."
systemctl --user enable --now sharepoint-xvfb.service sharepoint-chrome.service sharepoint-daemon.service
systemctl --user enable --now sharepoint-watchdog.timer
systemctl --user enable --now sharepoint-healthcheck.timer

echo ""
systemctl --user --no-pager status sharepoint-daemon.service || true
echo ""
echo "✅ Desplegado. Verifica:  bin/spm.sh status"
echo "   Logs en vivo:          journalctl --user -u sharepoint-daemon -f"
echo "   ¿Sesión M365 vencida?  bin/spm.sh login"
