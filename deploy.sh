#!/bin/bash
# deploy.sh — despliegue por host del lado OAuth de sharepoint-manager. Idempotente.
#
# ARQUITECTURA (2026-08-18): UN productor, N consumidores.
#
#   raspberrypi3  PRODUCTOR. token_refresher.py (sharepoint-refresher.service) refresca el
#                 access token y lo deja en ~/.cache/spm/.token. Ver pi/ y MANUAL.md.
#   carbon        CONSUMIDOR. Host de datos: corre subir_masivo.py y crm-preview.
#   silver        CONSUMIDOR. Servidor de aplicaciones: el CRM lee el token.
#   gold          CONSUMIDOR + VIGILANTE. Workstation intermitente.
#
# Los consumidores JALAN (no la Pi empuja): gold se apaga, así que un push fallaría contra una
# máquina ausente; y empujar exigiría darle a la Pi claves de escritura hacia carbon, el almacén
# de 1,6 TB — un camino de movimiento lateral desde el nodo menos endurecido hacia el más caro.
#
# UNA RUTA CANÓNICA POR HOST (~/.cache/spm/.token) y symlinks para las rutas que los
# consumidores ya esperaban. Enlaces y no copias: una copia puede quedar vieja sin que nadie
# lo note, y eso ya pasó — sync-sharepoint-token.sh copió durante 6 días un token que silver
# había dejado de renovar, reportando "OK" cada 3 minutos.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
HOST="${1:-$(hostname -s)}"
CFG="$REPO_DIR/$HOST/config.env"

[ -f "$CFG" ] || { echo "✗ No hay config para '$HOST'. Esperaba $CFG"; exit 1; }
mkdir -p "$UNIT_DIR" "$HOME/bin" "$HOME/.config/sharepoint-manager" "$HOME/.cache/spm"
chmod 700 "$HOME/.config/sharepoint-manager" "$HOME/.cache/spm"

echo "→ [$HOST] Retirando el stack de navegador obsoleto (cutover a OAuth)..."
# sharepoint-wayland.service estaba en esta lista desde el cutover, pero deploy.sh nunca se
# corrió en silver: siguió vivo hasta que un corte de luz lo dejó 'running' sin producir nada.
for old in sharepoint-xvfb.service sharepoint-chrome.service sharepoint-daemon.service \
           sharepoint-watchdog.service sharepoint-watchdog.timer \
           sharepoint-healthcheck.service sharepoint-healthcheck.timer \
           sharepoint-token.service sharepoint-wayland.service \
           sharepoint-token-sync.timer sharepoint-token-sync.service; do
  systemctl --user disable --now "$old" 2>/dev/null || true
done

echo "→ [$HOST] Instalando el jalador de token..."
install -m 755 "$REPO_DIR/bin/pull-token.sh" "$HOME/bin/pull-token.sh"
install -m 600 "$CFG" "$HOME/.config/sharepoint-manager/pull.env"
install -m 644 "$REPO_DIR/systemd/sharepoint-token-pull.service" \
               "$REPO_DIR/systemd/sharepoint-token-pull.timer" "$UNIT_DIR/"

# Rutas heredadas -> symlink al canónico, para no tocar la config de otros proyectos.
CANON="$(grep -E '^TOKEN_FILE=' "$CFG" | cut -d= -f2- | tr -d '"')"
enlazar() {
  [ -e "$1" ] && [ ! -L "$1" ] && cp -a "$1" "$1.bak-$(date +%Y%m%d)" 2>/dev/null || true
  mkdir -p "$(dirname "$1")"; rm -f "$1"; ln -sfn "$CANON" "$1"; echo "   enlace: $1 -> $CANON"
}
case "$HOST" in
  silver|gold) enlazar "$HOME/Desktop/Cargue a Onedrive/.token" ;;   # SHAREPOINT_TOKEN_FILE del CRM
  carbon)      enlazar "$HOME/.local/state/sharepoint/.token" ;;     # crm-preview
esac

systemctl --user daemon-reload
systemctl --user enable --now sharepoint-token-pull.timer

if [ "$HOST" = "gold" ]; then
  echo "→ [gold] Instalando el vigilante de salud OAuth..."
  chmod +x "$REPO_DIR"/bin/*.sh 2>/dev/null || true
  for unit in "$REPO_DIR"/systemd/sharepoint-oauth-health.service \
              "$REPO_DIR"/systemd/sharepoint-oauth-health.timer; do
    ln -sf "$unit" "$UNIT_DIR/$(basename "$unit")"
  done
  systemctl --user daemon-reload
  systemctl --user enable --now sharepoint-oauth-health.timer
fi

echo ""
echo "✅ [$HOST] listo. El refresco vive en la Pi; acá solo se jala y se afirma frescura."
echo "   Token:     systemctl --user list-timers sharepoint-token-pull.timer"
echo "   Estado:    cat ~/.local/state/sharepoint-manager/pull-state.json"
echo "   Productor: ssh ${PI_HOST:-raspberrypi3} 'python3 ~/sharepoint-token/token_refresher.py estado'"
echo "   Re-login:  ssh ${PI_HOST:-raspberrypi3} 'python3 ~/sharepoint-token/token_refresher.py login'"
