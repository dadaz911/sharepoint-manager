#!/bin/bash
# run-consolidate.sh — corre la consolidación masiva EN carbon, detached y resumible.
# Mantiene el token fresco jalándolo del Pi y corre consolidar_masivo.py (MoveFile + delete).
cd "$(dirname "$0")"
set -a; . ./upload.env; set +a

( while true; do
    rsync -q -e "ssh -o BatchMode=yes -o ConnectTimeout=10" \
      raspberrypi3:~/.cache/spm/.token "$SPM_TOKEN_FILE" 2>/dev/null
    sleep 600
  done ) &
PULL=$!
trap 'kill $PULL 2>/dev/null' EXIT

echo "=== run-consolidate START $(date '+%F %T') (token-pull pid $PULL) ==="
python3 consolidar_masivo.py
echo "=== run-consolidate END $(date '+%F %T') ==="
