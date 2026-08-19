#!/bin/bash
# pull-token.sh — un consumidor JALA el access token de SharePoint desde el productor.
#
# El refresher OAuth vive en la Pi (siempre encendida); los consumidores (gold, carbon)
# corren esto como timer. Se eligió pull (no push) porque gold es una laptop intermitente:
# al despertar jala el token más fresco, y no hay pushes fallidos contra una laptop apagada.
# Además evita darle a la Pi claves SSH de escritura hacia el almacén de datos.
#
# REGLA CENTRAL (2026-08-17): se afirma la FRESCURA DEL RESULTADO, no el éxito del intento.
# La versión anterior hacía rsync PRIMERO y validaba DESPUÉS, así que podía pisar un token
# local bueno con uno remoto vencido y devolver 0. Peor: `sync-sharepoint-token.sh` en carbon
# reportó "OK: token sincronizado" ~2.100 veces seguidas copiando un token muerto desde silver.
# Un consumidor NUNCA debe reportar éxito por haber copiado bien un archivo inservible.
#
# Códigos de salida (los lee systemd; `OnFailure=` depende de esto):
#   0  el token local quedó VIGENTE
#   1  el token local NO sirve (ausente, vencido o ilegible) -> requiere acción
#   2  no se pudo contactar al productor y el token local ya no sirve
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# Config: el entorno real (p. ej. EnvironmentFile= de systemd) SIEMPRE gana sobre el archivo.
for cfg in "${SPM_CONFIG:-}" "$REPO_DIR/config.env"; do
  [ -n "$cfg" ] && [ -f "$cfg" ] && { set -a; # shellcheck source=/dev/null
    source "$cfg"; set +a; break; }
done

PI_HOST="${PI_HOST:-raspberrypi3}"
PI_TOKEN="${PI_TOKEN:-/home/daniel/.cache/spm/.token}"
TOKEN_FILE="${TOKEN_FILE:-/home/daniel/.cache/spm/.token}"
MIN_VIDA="${MIN_VIDA:-5}"          # minutos de vida bajo los cuales un token se considera inútil
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sharepoint-manager"
STATE="$STATE_DIR/pull-state.json"

ts() { date -Iseconds; }
log() { echo "[$(ts)] $*"; }

# Minutos de vida restante de un JWT. Tolera `exp` como int o como string (el productor
# de silver lo emite entrecomillado). Devuelve -99999 si no es legible.
vida_min() {
  [ -f "$1" ] || { echo -99999; return; }
  python3 - "$1" <<'PY' 2>/dev/null || echo -99999
import sys, json, base64, datetime
try:
    t = open(sys.argv[1]).read().strip()
    p = t.split(".")[1]; p += "=" * (-len(p) % 4)
    exp = int(json.loads(base64.urlsafe_b64decode(p))["exp"])
    print(int((datetime.datetime.fromtimestamp(exp) - datetime.datetime.now()).total_seconds() / 60))
except Exception:
    print(-99999)
PY
}

escribir_estado() {   # $1=veredicto $2=min_local $3=min_remoto $4=detalle
  mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" 2>/dev/null || true
  cat > "$STATE.tmp" <<EOF
{
  "host": "$(hostname -s)",
  "ts": "$(ts)",
  "veredicto": "$1",
  "min_vida_local": $2,
  "min_vida_remoto": $3,
  "productor": "${PI_HOST}",
  "token_file": "${TOKEN_FILE}",
  "detalle": "$4"
}
EOF
  mv -f "$STATE.tmp" "$STATE"
}

mkdir -p "$(dirname "$TOKEN_FILE")"
local_min="$(vida_min "$TOKEN_FILE")"

# 1. Traer el token remoto a un temporal EN EL MISMO SISTEMA DE ARCHIVOS (mv atómico después).
TMP="$(mktemp "$(dirname "$TOKEN_FILE")/.token.pull.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
if ! rsync -e "ssh -o BatchMode=yes -o ConnectTimeout=10" "$PI_HOST:$PI_TOKEN" "$TMP" 2>/dev/null; then
  if [ "$local_min" -gt "$MIN_VIDA" ] 2>/dev/null; then
    log "productor ${PI_HOST} inalcanzable; el token local aún sirve (${local_min} min)."
    escribir_estado "local_vigente_sin_productor" "$local_min" -99999 "rsync falló contra ${PI_HOST}"
    exit 0
  fi
  log "ERROR: productor ${PI_HOST} inalcanzable Y el token local no sirve (${local_min} min)."
  escribir_estado "sin_token" "$local_min" -99999 "rsync falló contra ${PI_HOST}"
  exit 2
fi

# 2. Validar ANTES de instalar. Este es el orden que la versión anterior tenía invertido.
remoto_min="$(vida_min "$TMP")"

if [ "$remoto_min" -le "$MIN_VIDA" ] 2>/dev/null; then
  # El remoto no sirve: NO pisar el local. Mejor viejo-pero-válido que recién-copiado-y-muerto.
  if [ "$local_min" -gt "$MIN_VIDA" ] 2>/dev/null; then
    log "token remoto inservible (${remoto_min} min); se CONSERVA el local (${local_min} min)."
    escribir_estado "local_vigente_remoto_muerto" "$local_min" "$remoto_min" "remoto no instalado"
    exit 0
  fi
  log "ERROR: token remoto inservible (${remoto_min} min) y local tampoco (${local_min} min)."
  log "       El productor dejó de refrescar. Re-login: ssh ${PI_HOST} 'python3 ~/sharepoint-token/token_refresher.py login'"
  escribir_estado "sin_token" "$local_min" "$remoto_min" "productor no refresca"
  exit 1
fi

# 3. Instalar solo si aporta: el remoto vive más que el local.
if [ "$remoto_min" -le "$local_min" ] 2>/dev/null; then
  log "token local ya es igual o más fresco (${local_min} vs ${remoto_min} min); sin cambios."
  escribir_estado "local_vigente" "$local_min" "$remoto_min" "sin instalar, local más fresco"
  exit 0
fi

chmod 600 "$TMP"
mv -f "$TMP" "$TOKEN_FILE"     # atómico dentro del mismo filesystem
trap - EXIT
log "token instalado desde ${PI_HOST}: ${local_min} -> ${remoto_min} min de vida."
escribir_estado "instalado" "$remoto_min" "$remoto_min" "instalado desde ${PI_HOST}"
exit 0
