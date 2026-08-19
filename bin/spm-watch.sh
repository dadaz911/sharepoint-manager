#!/bin/bash
# spm-watch.sh — vigilante de la infraestructura de token, pensado para correr en un host
# SIEMPRE ENCENDIDO y CONSUMIDOR real (carbon).
#
# Tres decisiones de diseño, cada una por un fallo que ya ocurrió:
#
# 1. ALERTA POR AUSENCIA DE ÉXITO, NO POR EVENTO DE ERROR. Un vigilante que reacciona a errores
#    no puede avisar de los fallos silenciosos: el 12-ago el productor de silver quedó
#    `active (running)` sin producir nada y no emitió un solo error en 6 días. La ausencia
#    cubre todo —proceso muerto, host apagado, partición de red— con una sola regla.
#
# 2. CHEQUEO FUNCIONAL, NO SINTÁCTICO. El vigilante anterior decodificaba el JWT y miraba `exp`.
#    Eso responde "¿el archivo parece vigente?", no "¿puedo escribir en SharePoint?". El 17-ago
#    todo devolvió HTTP 401 desde carbon con un token que `exp` daba por bueno hacía horas.
#
# 3. VIVE EN EL CONSUMIDOR. El vigilante estaba en gold, una laptop que se apaga: el 14-ago el
#    refresco se rompió a las 15:06 y no pudo avisar hasta el 16-ago 14:01. 46,5 h de latencia.
#    Su lógica era correcta; su disponibilidad no.
#
# El canal es ntfy autoalojado en carbon, publicado solo a la tailnet. No se usa el correo del
# tenant ni Teams: dependerían de la misma credencial que se vigila, así que el aviso se caería
# justo cuando hace falta.
#
# Salidas: 0 sano · 1 degradado (token no sirve) · 2 productor sin éxito reciente
set -uo pipefail

PI_HOST="${PI_HOST:-raspberrypi3}"
REFRESHER="${REFRESHER:-/home/daniel/sharepoint-token/token_refresher.py}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.cache/spm/.token}"
SITIO="${SPM_SITIO_PRUEBA:-https://shdgov.sharepoint.com/sites/OficinadeDepuracindeCartera}"
NTFY="${NTFY_URL:-http://127.0.0.1:8790}"
TEMA="${NTFY_TOPIC:-shd-infra}"
MAX_SIN_EXITO="${MAX_SIN_EXITO:-7200}"     # 2 h sin refresco exitoso en el productor = problema
RE_AVISO="${RE_AVISO:-21600}"              # 6 h entre avisos repetidos del mismo problema
LATIDO="${LATIDO:-604800}"                 # 7 d: aviso "todo bien" para probar que el canal vive

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sharepoint-manager"
mkdir -p "$STATE_DIR"
ULT_AVISO="$STATE_DIR/watch.last-alert"
ULT_LATIDO="$STATE_DIR/watch.last-heartbeat"

ahora=$(date +%s)
avisar() {  # $1=prioridad $2=título $3=cuerpo
  curl -s -m 10 -o /dev/null \
    -H "Title: $2" -H "Priority: $1" -H "Tags: warning" \
    -d "$3" "$NTFY/$TEMA" || echo "AVISO: no se pudo publicar en $NTFY/$TEMA" >&2
}
avisar_con_cooldown() {
  if [ -f "$ULT_AVISO" ]; then
    edad=$(( ahora - $(stat -c %Y "$ULT_AVISO" 2>/dev/null || echo 0) ))
    [ "$edad" -lt "$RE_AVISO" ] && { echo "  (en cooldown, faltan $(( (RE_AVISO-edad)/60 )) min)" >&2; return; }
  fi
  avisar "$1" "$2" "$3"; touch "$ULT_AVISO"
}

# ── 1. Dead-man's switch sobre el PRODUCTOR ──────────────────────────────────────────────
# No se pregunta "¿falló algo?" sino "¿cuándo fue el último éxito?". Si el productor está
# apagado o incomunicado no hay nadie que reporte un error: solo se nota por el silencio.
prod=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_HOST" \
        "cat ~/.local/state/sharepoint-manager/refresher-state.json" 2>/dev/null)
if [ -z "$prod" ]; then
  echo "PRODUCTOR: no se pudo leer el estado de $PI_HOST (¿apagado, sin red?)"
  avisar_con_cooldown urgent "SharePoint: productor incomunicado" \
    "No se puede leer el estado del refresco en $PI_HOST. Revisá energía y red ANTES de tocar credenciales."
  exit 2
fi
sin_exito=$(python3 - "$ahora" <<PY
import json, sys, datetime
try:
    s = json.loads('''$prod''')
    t = datetime.datetime.fromisoformat(s["ultimo_exito"])
    print(int(int(sys.argv[1]) - t.timestamp()), s.get("clase", "?"), s.get("motivo") or "-")
except Exception as e:
    print(-1, "ILEGIBLE", str(e)[:80])
PY
)
segs=$(echo "$sin_exito" | cut -d' ' -f1); clase=$(echo "$sin_exito" | cut -d' ' -f2)
motivo=$(echo "$sin_exito" | cut -d' ' -f3-)

if [ "$segs" -lt 0 ] 2>/dev/null || [ "$segs" -gt "$MAX_SIN_EXITO" ] 2>/dev/null; then
  echo "PRODUCTOR: sin refresco exitoso hace $(( segs / 60 )) min · clase=$clase · $motivo"
  case "$clase" in
    HUMANO) cuerpo="El refresh token dejó de servir ($motivo). Hace falta re-login con MFA:
ssh $PI_HOST 'python3 $REFRESHER login'" ;;
    TRANSITORIO) cuerpo="El refresco falla por red desde $PI_HOST hace $(( segs / 60 )) min ($motivo). Suele curarse solo; si sigue, revisá conectividad." ;;
    *) cuerpo="Sin refresco exitoso hace $(( segs / 60 )) min. Estado: $clase — $motivo" ;;
  esac
  avisar_con_cooldown urgent "SharePoint: el token dejó de refrescarse" "$cuerpo"
  exit 2
fi

# ── 2. Chequeo FUNCIONAL: ¿este host puede hablar con SharePoint? ────────────────────────
# Un GET de solo lectura, no una escritura: se ejecuta cada 15 min y no debe ensuciar la
# biblioteca de producción. La escritura real se prueba aparte, con `test-write`.
if [ ! -s "$TOKEN_FILE" ]; then
  echo "LOCAL: no hay token en $TOKEN_FILE"
  avisar_con_cooldown urgent "SharePoint: este host no tiene token" "No hay token en $(hostname -s):$TOKEN_FILE"
  exit 1
fi
http=$(curl -s -m 25 -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
  -H "Accept: application/json;odata=nometadata" \
  "$SITIO/_api/web?\$select=Title")

if [ "$http" != "200" ]; then
  echo "FUNCIONAL: SharePoint respondió HTTP $http desde $(hostname -s)"
  avisar_con_cooldown urgent "SharePoint: el token no sirve para escribir" \
    "$(hostname -s) recibió HTTP $http del sitio del cargue, aunque el productor dice estar sano.
Revisá: python3 $REFRESHER estado (en $PI_HOST) y ~/bin/pull-token.sh acá."
  exit 1
fi

# ── 3. Sano ──────────────────────────────────────────────────────────────────────────────
rm -f "$ULT_AVISO"
echo "OK: productor con éxito hace $(( segs / 60 )) min · SharePoint responde 200 desde $(hostname -s)"

# Latido semanal. El modo de falla número uno de un sistema de alertas es un canal que nunca
# se ejercita: si no llega este mensaje, el canal murió antes de que muriera el token.
if [ ! -f "$ULT_LATIDO" ] || [ $(( ahora - $(stat -c %Y "$ULT_LATIDO" 2>/dev/null || echo 0) )) -ge "$LATIDO" ]; then
  avisar low "SharePoint: todo bien" \
    "Latido semanal. Productor $PI_HOST refrescando, $(hostname -s) escribe contra SharePoint. Si dejás de ver esto, el canal de alertas se rompió."
  touch "$ULT_LATIDO"
fi
exit 0
