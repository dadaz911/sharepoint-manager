#!/bin/bash
# wait-cdp.sh — bloquea hasta que el CDP de Chrome responda en :CDP_PORT.
# Usado como ExecStartPre de sharepoint-daemon.service para que el daemon no
# arranque antes de que Chrome esté listo. Falla (exit≠0) si Chrome no sube en ~30s,
# lo que hace que systemd reintente el daemon (RestartSec).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=/dev/null
[ -f "$REPO_DIR/config.env" ] && source "$REPO_DIR/config.env"
CDP_PORT="${CDP_PORT:-9222}"

exec curl --retry 30 --retry-delay 1 --retry-connrefused -fsS -m 3 \
  -o /dev/null "http://localhost:${CDP_PORT}/json/version"
