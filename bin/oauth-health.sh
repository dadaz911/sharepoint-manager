#!/bin/bash
# oauth-health.sh — Vigila la salud del refresco OAuth (que ocurre en la Pi) y avisa solo si
# falla de forma SOSTENIDA. Pensado como timer de usuario (cada 15 min).
#
# CAMBIOS 2026-08-17, los tres por incidentes reales:
#
# 1. SALE 1 ANTE FALLO SOSTENIDO. Antes salía 0 en TODOS sus caminos, incluido el de alerta.
#    Su propio comentario documentaba que "110 fallos consecutivos se veían en journalctl como
#    110 ejecuciones SUCCESS": se corrigió el texto del log y NO el código de salida, así que
#    systemd seguía viendo éxito, `systemctl --user --failed` seguía vacío y `OnFailure=` era
#    inutilizable. La lección de junio se había aplicado a medias.
#
# 2. DISTINGUE RED DE CREDENCIAL. Antes, si la Pi no respondía, mins=-99999 y el mensaje decía
#    "token vencido hace 99999 min — re-login". Diagnosticaba un problema de credencial ante un
#    corte de red. Con cortes de luz documentados en el sitio (12-ago: silver y carbon
#    reiniciaron con 7 min de diferencia), eso manda a rehacer una autenticación con MFA por
#    algo que se cura solo cuando vuelve el internet.
#
# 3. ESTADO PERSISTENTE. El contador vivía en $XDG_RUNTIME_DIR, que se borra en cada arranque:
#    tras un reboot volvía a cero y había que esperar otros 45 min para que alertara.
#
# Delega el veredicto en `token_refresher.py estado`, que corre DONDE está el dato y ya
# distingue TRANSITORIO de HUMANO. Este script ya no decodifica el JWT por su cuenta: dos
# implementaciones del mismo juicio terminan contradiciéndose.
#
# LIMITACIÓN CONOCIDA: corre en gold, una laptop que se apaga. El 14-ago el refresco se rompió
# a las 15:06 y este vigilante no pudo avisar hasta el 16-ago 14:01, cuando encendieron gold:
# 46,5 h de latencia. Su lógica es correcta; su disponibilidad no. El vigilante definitivo debe
# vivir en un host siempre encendido y consumidor real (carbon).
#
# Salidas: 0 = sano · 1 = fallo sostenido (requiere acción) · 2 = productor inalcanzable
set -uo pipefail

PI_HOST="${PI_HOST:-raspberrypi3}"
REFRESHER="${REFRESHER:-/home/daniel/sharepoint-token/token_refresher.py}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/sharepoint-manager"
FAILC="$STATE_DIR/oauth-health.fail-count"
ALERTED="$STATE_DIR/oauth-health.alerted"
SETTLE="${SETTLE:-3}"          # ~45 min de fallo sostenido antes de avisar (evita transitorios)
COOLDOWN="${COOLDOWN:-21600}"  # 6 h entre avisos

mkdir -p "$STATE_DIR"
notif() { notify-send --urgency=critical "SharePoint OAuth" "$1" 2>/dev/null || true; }

salida="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_HOST" "python3 '$REFRESHER' estado" 2>&1)"
rc=$?
if [ "$rc" -eq 255 ] || [ "$rc" -eq 127 ]; then
  clase="RED"; detalle="no se pudo consultar a ${PI_HOST} (ssh rc=${rc})"
elif [ "$rc" -eq 0 ]; then
  clase="OK"; detalle="$(echo "$salida" | tail -1)"
else
  clase="$(echo "$salida" | grep -oE 'clase=[A-Z]+' | head -1 | cut -d= -f2)"
  clase="${clase:-DESCONOCIDO}"
  detalle="$(echo "$salida" | grep -E 'motivo:|INCONSISTENTE|SIN ESTADO' | head -1)"
fi

if [ "$clase" = "OK" ]; then
  echo "OK: ${detalle}"
  rm -f "$FAILC" "$ALERTED"
  exit 0
fi

c=$(( $(cat "$FAILC" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$FAILC"
# El journal es el registro PERSISTENTE; la notificación de escritorio es efímera y se pierde.
echo "FALLO ${c} [${clase}]: ${detalle}" >&2
echo "$salida" | sed 's/^/  | /' >&2

if [ "$c" -lt "$SETTLE" ]; then
  echo "Fallo aún no sostenido (${c}/${SETTLE}); sin avisar todavía." >&2
  exit 0
fi

# El remedio depende de la CLASE: prescribir re-login ante un corte de red es mandar a rehacer
# una autenticación con MFA por algo que no tiene que ver con la credencial.
case "$clase" in
  RED)         msg="No se puede consultar a ${PI_HOST}. Revisá red/Tailscale/energía ANTES de tocar credenciales."; code=2 ;;
  TRANSITORIO) msg="El refresco falla por red desde la Pi (${c} chequeos). Suele curarse solo; si persiste, revisá conectividad."; code=1 ;;
  HUMANO)      msg="El refresh token dejó de servir. Re-login: ssh ${PI_HOST} 'python3 ${REFRESHER} login'"; code=1 ;;
  *)           msg="Estado del refresco desconocido. Mirá: ssh ${PI_HOST} 'python3 ${REFRESHER} estado'"; code=1 ;;
esac

if [ -f "$ALERTED" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$ALERTED" 2>/dev/null || echo 0) ))
  if [ "$age" -lt "$COOLDOWN" ]; then
    echo "Ya se avisó hace $(( age / 60 )) min; en cooldown de $(( COOLDOWN / 60 )) min." >&2
    exit "$code"   # sigue siendo un fallo aunque no se repita el aviso
  fi
fi
echo "ALERTA [${clase}]: ${msg}" >&2
notif "$msg"
touch "$ALERTED"
exit "$code"
