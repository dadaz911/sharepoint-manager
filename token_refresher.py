#!/usr/bin/env python3
"""
token_refresher.py — Mantiene fresco el access token de SharePoint/M365 vía OAuth2
refresh-token, SIN navegador. Reemplaza el stack Chrome/Xvfb/VNC.

Pensado para correr en la Raspberry Pi (siempre encendida): liviano (solo `requests`),
sobrevive reboots (el refresh token vive en disco), y entrega el token a donde corran
las subidas (gold) vía un comando configurable.

Comandos:
  login        device-code flow (interactivo, 1 vez) -> guarda el refresh token en disco
  refresh      lee el refresh token de disco, obtiene un access token nuevo, lo escribe
               (+ entrega vía SPM_DELIVER si está definido). Este ES el camino post-reboot.
  run          daemon: refresh cada REFRESH_EVERY segundos
  test-write   sube y borra un archivo de prueba (valida que el token sirve para ESCRIBIR)
  status       muestra la validez del access token actual
  estado       imprime el estado persistido y SALE 1 si el refresco no está sano.
               Es el comando que deben consumir un vigilante y el motor de cargue: responde
               "¿desde cuándo está roto y por qué?" sin depender del journal, que en la Pi
               retiene ~3 días. Distingue TRANSITORIO (red) de HUMANO (hace falta re-login).

Config por entorno (defaults para el tenant SHD):
  TENANT, CLIENT_ID, SCOPE, TOKEN_FILE, RT_FILE, PERSONAL_SITE, DEST_FOLDER, REFRESH_EVERY,
  SPM_DELIVER_HOST (host destino, ej. "gold"; vacío = no entregar) y SPM_DELIVER_PATH
  (ruta remota, default = misma ruta canónica) — entrega el token vía rsync.
"""

import base64
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests


def _load_config_env():
    """Carga un config.env adyacente al script si existe, SIN pisar el entorno real.

    Así el CLI manual (p. ej. `status`) resuelve las MISMAS rutas que usa el
    servicio systemd (que inyecta la config vía EnvironmentFile= antes de
    arrancar Python). Bajo systemd las claves ya están en os.environ -> no-op.
    Orden: $SPM_CONFIG, luego ./config.env, luego ./pi/config.env (junto al script).
    """
    here = Path(__file__).resolve().parent
    candidates = []
    if os.environ.get("SPM_CONFIG"):
        candidates.append(Path(os.environ["SPM_CONFIG"]))
    candidates += [here / "config.env", here / "pi" / "config.env"]
    for cfg in candidates:
        if not cfg.is_file():
            continue
        for raw in cfg.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().split(" #", 1)[0].strip()  # quita comentario en línea
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:  # el entorno real siempre gana
                os.environ[key] = val
        break  # primer config.env encontrado gana


_load_config_env()

TENANT = os.environ.get("TENANT", "cd422ec3-3717-412e-ba56-3bdab9a2f7ef")
CLIENT = os.environ.get("CLIENT_ID", "d3590ed6-52b3-4102-aeff-aad2292ab01c")  # MS Office (público)
SCOPE = os.environ.get("SCOPE", "https://shdgov-my.sharepoint.com/.default offline_access")
AUTH = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", "/home/daniel/Desktop/Cargue a Onedrive/.token"))
RT_FILE = Path(os.environ.get("RT_FILE", str(Path.home() / ".config/sharepoint-manager/refresh_token")))
PERSONAL = os.environ.get("PERSONAL_SITE", "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1")
DEST = os.environ.get("DEST_FOLDER", "/personal/dzuniga_shd_gov_co1/Documents/Pruebas")
REFRESH_EVERY = int(os.environ.get("REFRESH_EVERY", "3000"))  # 50 min (token dura ~65)
DELIVER_HOST = os.environ.get("SPM_DELIVER_HOST", "")  # ej. "gold"; vacío = no entregar
DELIVER_PATH = os.environ.get("SPM_DELIVER_PATH", str(TOKEN_FILE))  # ruta remota (default canónica)
# Estado en DISCO (no en tmpfs): responde "¿desde cuándo está roto y por qué?" sin depender del
# journal. En junio un fallo duró 40 días inadvertido y su rastro vivía en $XDG_RUNTIME_DIR,
# que se borra al reiniciar; hoy el journal de esta unidad retiene ~3 días.
STATE_FILE = Path(
    os.environ.get("STATE_FILE", str(Path.home() / ".local/state/sharepoint-manager/refresher-state.json"))
)
# Reintento tras un fallo transitorio. El ritmo normal (~50 min) con tokens de ~65 deja 15 min
# de holgura: UN solo ciclo perdido garantiza un hueco sin token. Ante un transitorio hay que
# volver ANTES, no después — el backoff solo aplica a 429 y a los fallos que piden un humano.
REINTENTO_MIN = int(os.environ.get("REINTENTO_MIN", "60"))  # segundos
REINTENTO_MAX = int(os.environ.get("REINTENTO_MAX", "600"))


def _log(m):
    # Sello de tiempo propio: el journal de la Pi solo retiene ~3 días (comparte 100 MB con
    # vecinos ruidosos), así que estas líneas terminan copiadas o redirigidas a archivo, donde
    # la marca de systemd ya no está. Un log sin hora no sirve para reconstruir un incidente.
    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {m}", flush=True)


def _write_secure(path: Path, data: str, mode=0o600):
    """Escritura ATÓMICA: temporal + fsync + os.replace, con respaldo del anterior.

    Antes era os.open(O_TRUNC) + os.write directo. Un corte de energía dentro de esa ventana
    deja el archivo truncado; si el archivo es el refresh token —la única credencial de larga
    vida— eso exige re-login interactivo. La Pi corre sobre una SD, en un sitio con cortes
    documentados (12-ago: silver y carbon reiniciaron con 7 min de diferencia). El riesgo era
    pequeño en probabilidad y máximo en consecuencia.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            bak = path.with_suffix(path.suffix + ".bak")
            os.replace(str(path), str(bak))
            os.chmod(str(bak), mode)
        except OSError:
            pass  # el respaldo es un extra; nunca debe impedir escribir el token nuevo
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))


def _exp_minutes(tok: str):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p))["exp"]
        return int((datetime.datetime.fromtimestamp(int(exp)) - datetime.datetime.now()).total_seconds() / 60)
    except Exception:
        return None


def _estado(**campos):
    """Vuelca el estado a disco, fusionando con lo que ya había.

    Es la fuente de la que debe leer cualquier vigilante, y la precondición que un cargue
    debería exigir antes de arrancar. Nunca puede tumbar el refresco: si falla, se registra
    y se sigue — el token importa más que su bitácora.
    """
    try:
        prev = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        prev = {}
    prev.update(campos)
    prev["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(prev, indent=2, ensure_ascii=False))
        os.replace(str(tmp), str(STATE_FILE))
    except Exception as e:
        _log(f"  (aviso) no pude escribir el estado en {STATE_FILE}: {e}")


def _deliver():
    if not DELIVER_HOST:
        return
    # rsync -s (--protect-args): la ruta remota con espacios ("Cargue a Onedrive") NO la parte
    # el shell remoto. Args como lista (sin shell=True) => imposible inyectar comandos.
    p = subprocess.run(
        [
            "rsync",
            "-s",
            "-e",
            "ssh -o BatchMode=yes -o ConnectTimeout=10",
            str(TOKEN_FILE),
            f"{DELIVER_HOST}:{DELIVER_PATH}",
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        _log(f"  entrega -> {DELIVER_HOST}: ok")
    else:
        # Antes solo se registraba "rc=23", que no dice si fue DNS, clave, permisos o disco lleno.
        motivo = (p.stderr or "").strip().splitlines()
        _log(f"  entrega -> {DELIVER_HOST}: FALLÓ rc={p.returncode} — {motivo[-1] if motivo else 'sin stderr'}")


def _save_access(tok: str):
    _write_secure(TOKEN_FILE, tok + "\n")
    _log(f"  access token -> {TOKEN_FILE} (validez ~{_exp_minutes(tok)} min)")
    _deliver()


def login():
    r = requests.post(f"{AUTH}/devicecode", data={"client_id": CLIENT, "scope": SCOPE}, timeout=20)
    if r.status_code != 200:
        _log("ERROR devicecode: " + r.text[:300])
        return 1
    dc = r.json()
    _log("\n  === AUTENTICA ===")
    _log("  URL:    " + dc["verification_uri"])
    _log("  CÓDIGO: " + dc["user_code"])
    _log("  (esperando login + MFA...)\n")
    iv = dc.get("interval", 5)
    waited = 0
    while waited < dc.get("expires_in", 900):
        time.sleep(iv)
        waited += iv
        pr = requests.post(
            f"{AUTH}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT,
                "device_code": dc["device_code"],
            },
            timeout=20,
        )
        if pr.status_code == 200:
            j = pr.json()
            _write_secure(RT_FILE, j["refresh_token"])
            _log(f"  ✅ refresh token guardado en {RT_FILE} (0600)")
            _save_access(j["access_token"])
            return 0
        e = pr.json().get("error")
        if e == "authorization_pending":
            continue
        if e == "slow_down":
            iv += 5
            continue
        _log("  ❌ " + str(e) + ": " + (pr.json().get("error_description", "") or "")[:200])
        return 2
    _log("  ❌ timeout")
    return 3


# Códigos de retorno de refresh(). Son también el exit code del CLI (`refresh`), así que
# systemd puede distinguir "hace falta un humano" (1) de "se cayó la red" (2).
OK, HUMANO, TRANSITORIO = 0, 1, 2

_AADSTS = re.compile(r"AADSTS\d+")


def clase_nombre(c):
    return {OK: "OK", HUMANO: "HUMANO", TRANSITORIO: "TRANSITORIO"}.get(c, "DESCONOCIDO")


def _clasificar(status, cuerpo):
    """Tres clases porque solo hay tres acciones posibles: seguir, reintentar pronto, o
    llamar a un humano. El código AADSTS exacto NO ramifica el control de flujo: se guarda
    en el estado y se imprime. Ramificar por cada código sería taxonomía sin consecuencia.
    """
    err = (cuerpo.get("error") or "").strip()
    if status == 429 or status >= 500:
        return TRANSITORIO, f"HTTP {status} del servidor de autenticación — se reintenta pronto"
    if err in ("invalid_grant", "invalid_client", "unauthorized_client", "interaction_required"):
        return HUMANO, f"{err} — la credencial dejó de servir; hace falta re-login con MFA"
    if status >= 400:
        return HUMANO, f"HTTP {status} {err or 'sin código de error'}"
    return TRANSITORIO, f"HTTP {status} inesperado"


def _consecutivos():
    try:
        return int(json.loads(STATE_FILE.read_text()).get("consecutivos", 0))
    except Exception:
        return 0


def refresh():
    if not RT_FILE.exists():
        _log("❌ no hay refresh token en disco; corre: token_refresher.py login")
        _estado(veredicto="humano", clase="HUMANO", motivo="no hay refresh token en disco",
                consecutivos=_consecutivos() + 1)
        return HUMANO

    rt = RT_FILE.read_text().strip()
    try:
        r = requests.post(
            f"{AUTH}/token",
            data={"grant_type": "refresh_token", "client_id": CLIENT, "refresh_token": rt, "scope": SCOPE},
            timeout=20,
        )
    except requests.RequestException as e:
        # ESTA es la clase que antes se perdía por completo. La excepción subía hasta run(),
        # que la registraba como "refresh exc: <str>" sin decir si fue DNS, TLS o timeout —
        # es decir, sin poder distinguir "se cayó el internet" de un problema real. Ahora
        # queda nombrada y clasificada aquí, que es donde se sabe qué se estaba intentando.
        n = _consecutivos() + 1
        _log(f"❌ TRANSITORIO (red): sin respuesta del servidor — {type(e).__name__}: {str(e)[:160]}")
        _log(f"   fallos consecutivos: {n}. Se reintenta en {REINTENTO_MIN}s, no en {REFRESH_EVERY}s.")
        _estado(veredicto="fallo", clase="TRANSITORIO", motivo=f"red: {type(e).__name__}",
                http=None, consecutivos=n)
        return TRANSITORIO

    if r.status_code != 200:
        # r.json() sin guarda era un bug silencioso: un 502/503 devuelto por un proxy o una
        # ventana de mantenimiento viene en HTML, r.json() lanzaba ValueError, y el fallo
        # terminaba registrado como "Expecting value: line 1 column 1" SIN el código HTTP.
        # O sea: la clase de fallo más recuperable era, por construcción, la más ilegible.
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = {}
        clase, motivo = _clasificar(r.status_code, cuerpo)
        desc = (cuerpo.get("error_description") or r.text or "")[:400]
        aad = _AADSTS.search(desc)
        # correlation_id es lo único que permite abrir un caso con Microsoft o cruzar con los
        # sign-in logs del tenant. Se descartaba.
        corr = cuerpo.get("correlation_id")
        n = _consecutivos() + 1

        _log(f"❌ {clase_nombre(clase)}: {motivo}")
        _log(f"   HTTP {r.status_code} · {cuerpo.get('error', 's/error')}"
             f"{' · ' + aad.group(0) if aad else ''}"
             f"{' · correlation_id=' + str(corr) if corr else ''}")
        _log(f"   fallos consecutivos: {n}")
        if clase == HUMANO:
            _log("   REMEDIO: ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py login'")

        _estado(
            veredicto="fallo", clase=clase_nombre(clase), motivo=motivo,
            http=r.status_code, error=cuerpo.get("error"), suberror=cuerpo.get("suberror"),
            error_codes=cuerpo.get("error_codes"), aadsts=aad.group(0) if aad else None,
            correlation_id=corr, descripcion=desc[:300], consecutivos=n,
            retry_after=r.headers.get("Retry-After"),
        )
        return clase

    cuerpo = r.json()
    if cuerpo.get("refresh_token"):  # rotación: persistir el refresh token nuevo
        _write_secure(RT_FILE, cuerpo["refresh_token"])
    _save_access(cuerpo["access_token"])
    _estado(
        veredicto="ok", clase="OK", motivo=None, http=200, error=None, aadsts=None,
        consecutivos=0, ultimo_exito=datetime.datetime.now().isoformat(timespec="seconds"),
        exp_min=_exp_minutes(cuerpo["access_token"]),
    )
    return OK


def _proxima_espera():
    """Ritmo derivado de la vida real del token, no de una constante.

    Con 50 min fijos y tokens de ~65-80, la holgura es de 15-30 min: perder UN ciclo ya
    garantiza un hueco sin token válido — el presupuesto de error del diseño era cero.
    Refrescando al ~55% de la vida restante quedan varios fallos de margen antes de que
    algún consumidor se quede sin credencial.
    """
    try:
        m = _exp_minutes(TOKEN_FILE.read_text().strip())
    except Exception:
        m = None
    if not m or m <= 0:
        return REINTENTO_MIN
    return max(300, min(int(m * 60 * 0.55), REFRESH_EVERY))


def _espera_humano(n):
    """Sondeo cuando hace falta un humano.

    Se sondea porque algunos fallos de política se curan solos, pero se sondea POCO y a
    ritmo decreciente: cada intento es un sign-in fallido en el tenant, y este daemon usa
    el client id público de Microsoft Office. Miles de sign-ins no interactivos fallidos con
    ese appid desde una IP residencial son la firma de un robo de token; el remedio no puede
    ser lo que dispare una revocación administrativa. La alerta ya salió al primer fallo.
    """
    return 1800 if n <= 4 else 3600


def run():
    _log(f"=== token_refresher daemon — ritmo base {REFRESH_EVERY}s · estado en {STATE_FILE} ===")
    espera_transitorio = REINTENTO_MIN
    while True:
        try:
            clase = refresh()
        except Exception as e:
            # Red de seguridad. Antes ESTE era el único manejo de error del bucle y se comía
            # todo —incluidos los fallos de red— en una sola línea sin clasificar.
            n = _consecutivos() + 1
            _log(f"❌ DESCONOCIDO: excepción no prevista — {type(e).__name__}: {str(e)[:200]}")
            _estado(veredicto="fallo", clase="DESCONOCIDO", motivo=type(e).__name__, consecutivos=n)
            clase = TRANSITORIO

        if clase == OK:
            espera_transitorio = REINTENTO_MIN
            dormir = _proxima_espera()
        elif clase == TRANSITORIO:
            ra = None
            try:  # si el servidor pidió Retry-After, mandá el servidor
                ra = json.loads(STATE_FILE.read_text()).get("retry_after")
            except Exception:
                pass
            dormir = int(ra) if (ra and str(ra).isdigit()) else espera_transitorio
            espera_transitorio = min(espera_transitorio * 2, REINTENTO_MAX)
        else:
            dormir = _espera_humano(_consecutivos())

        _log(f"   próximo intento en {dormir}s")
        time.sleep(dormir)


def _api_headers():
    tok = TOKEN_FILE.read_text().strip()
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json;odata=verbose"}


def test_write():
    """Sube y borra un archivo de prueba — mismo camino que subir_onedrive.py."""
    name = "spm_oauth_writetest.txt"
    enc_folder = urllib.parse.quote(DEST, safe="/")
    enc_name = urllib.parse.quote(name)
    add_url = (
        f"{PERSONAL}/_api/web/GetFolderByServerRelativeUrl('{enc_folder}')/Files/add(url='{enc_name}',overwrite=true)"
    )
    h = _api_headers()
    h["Content-Type"] = "application/octet-stream"
    body = b"oauth write test " + str(datetime.datetime.now()).encode()
    r = requests.post(add_url, headers=h, data=body, timeout=60)
    _log(f"  ADD: HTTP {r.status_code} {'OK' if r.status_code in (200, 201) else r.text[:200]}")
    if r.status_code not in (200, 201):
        return 1
    # borrar el archivo de prueba
    server_rel = f"{DEST}/{name}"
    del_url = f"{PERSONAL}/_api/web/GetFileByServerRelativeUrl('{urllib.parse.quote(server_rel, safe='/')}')"
    hd = _api_headers()
    hd["X-HTTP-Method"] = "DELETE"
    hd["IF-MATCH"] = "*"
    rd = requests.post(del_url, headers=hd, timeout=60)
    _log(f"  DELETE prueba: HTTP {rd.status_code} {'OK' if rd.status_code in (200, 204) else rd.text[:120]}")
    return 0


def status():
    if TOKEN_FILE.exists():
        m = _exp_minutes(TOKEN_FILE.read_text().strip())
        _log(f"  access token: {'válido ~' + str(m) + ' min' if m and m > 0 else 'VENCIDO/ilegible'}  ({TOKEN_FILE})")
    else:
        _log(f"  sin access token en {TOKEN_FILE}")
    _log(f"  refresh token: {'presente' if RT_FILE.exists() else 'AUSENTE'} ({RT_FILE})")


def estado():
    """Imprime el estado y sale 0 solo si el refresco está SANO.

    Pensado para que lo consuman un vigilante (`OnFailure=`) y el motor de cargue como
    precondición. La regla: el exit code refleja la validez del RESULTADO, no el éxito de
    haber leído el archivo. Un `estado` que siempre saliera 0 sería el mismo error que
    cometía oauth-health.sh, que salía 0 incluso ante fallo sostenido y por eso systemd
    nunca vio nada.
    """
    if not STATE_FILE.exists():
        _log(f"SIN ESTADO: {STATE_FILE} no existe (¿el daemon nunca corrió?)")
        return 1
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception as e:
        _log(f"ESTADO ILEGIBLE: {e}")
        return 1

    clase = s.get("clase", "DESCONOCIDO")
    _log(f"clase={clase} veredicto={s.get('veredicto')} consecutivos={s.get('consecutivos', 0)}")
    _log(f"último éxito: {s.get('ultimo_exito', 'nunca registrado')} · última evaluación: {s.get('ts')}")
    if clase != "OK":
        _log(f"motivo: {s.get('motivo')}")
        for k in ("http", "error", "aadsts", "correlation_id"):
            if s.get(k):
                _log(f"  {k}: {s[k]}")
        if clase == "HUMANO":
            _log("REMEDIO: token_refresher.py login  (device-code, exige navegador y MFA)")
        return 1

    # Sano en la última evaluación no basta: hay que comprobar que el token de disco VIVE.
    m = _exp_minutes(TOKEN_FILE.read_text().strip()) if TOKEN_FILE.exists() else None
    if m is None or m <= 0:
        _log(f"INCONSISTENTE: el estado dice OK pero el token de disco no sirve ({m} min).")
        return 1
    _log(f"access token vigente ~{m} min ({TOKEN_FILE})")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {
        "login": login,
        "refresh": refresh,
        "run": run,
        "test-write": test_write,
        "status": status,
        "estado": estado,
    }.get(cmd)
    if not fn:
        _log(__doc__)
        sys.exit(1)
    sys.exit(fn() or 0)


if __name__ == "__main__":
    main()
