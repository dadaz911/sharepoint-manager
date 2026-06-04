#!/bin/bash
# run-upload.sh — corre el cargue masivo EN carbon, detached y resumible.
# Mantiene el token fresco jalándolo del Pi cada 10 min y corre subir_masivo.py.
cd "$(dirname "$0")" || exit 1
set -a; . ./upload.env; set +a

# Loop que refresca el token (lo redime el Pi; carbon solo lo jala).
( while true; do
    rsync -q -e "ssh -o BatchMode=yes -o ConnectTimeout=10" \
      raspberrypi3:~/.cache/spm/.token "$SPM_TOKEN_FILE" 2>/dev/null
    sleep 600
  done ) &
PULL=$!
trap 'kill $PULL 2>/dev/null' EXIT

echo "=== run-upload START $(date '+%F %T') (token-pull pid $PULL) ==="
python3 subir_masivo.py
echo "=== run-upload END $(date '+%F %T') ==="
