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

Config por entorno (defaults para el tenant SHD):
  TENANT, CLIENT_ID, SCOPE, TOKEN_FILE, RT_FILE, PERSONAL_SITE, DEST_FOLDER, REFRESH_EVERY,
  SPM_DELIVER_HOST (host destino, ej. "gold"; vacío = no entregar) y SPM_DELIVER_PATH
  (ruta remota, default = misma ruta canónica) — entrega el token vía rsync.
"""
import os, sys, time, json, base64, subprocess, datetime, urllib.parse
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
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().split(" #", 1)[0].strip()  # quita comentario en línea
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if key and key not in os.environ:      # el entorno real siempre gana
                os.environ[key] = val
        break  # primer config.env encontrado gana


_load_config_env()

TENANT   = os.environ.get("TENANT", "cd422ec3-3717-412e-ba56-3bdab9a2f7ef")
CLIENT   = os.environ.get("CLIENT_ID", "d3590ed6-52b3-4102-aeff-aad2292ab01c")  # MS Office (público)
SCOPE    = os.environ.get("SCOPE", "https://shdgov-my.sharepoint.com/.default offline_access")
AUTH     = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", "/home/daniel/Desktop/Cargue a Onedrive/.token"))
RT_FILE  = Path(os.environ.get("RT_FILE", str(Path.home() / ".config/sharepoint-manager/refresh_token")))
PERSONAL = os.environ.get("PERSONAL_SITE", "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1")
DEST     = os.environ.get("DEST_FOLDER", "/personal/dzuniga_shd_gov_co1/Documents/Pruebas")
REFRESH_EVERY = int(os.environ.get("REFRESH_EVERY", "3000"))  # 50 min (token dura ~65)
DELIVER_HOST = os.environ.get("SPM_DELIVER_HOST", "")           # ej. "gold"; vacío = no entregar
DELIVER_PATH = os.environ.get("SPM_DELIVER_PATH", str(TOKEN_FILE))  # ruta remota (default canónica)


def _log(m): print(m, flush=True)


def _write_secure(path: Path, data: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data.encode())
    finally:
        os.close(fd)


def _exp_minutes(tok: str):
    try:
        p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p))["exp"]
        return int((datetime.datetime.fromtimestamp(int(exp)) - datetime.datetime.now()).total_seconds() / 60)
    except Exception:
        return None


def _deliver():
    if not DELIVER_HOST:
        return
    # rsync -s (--protect-args): la ruta remota con espacios ("Cargue a Onedrive") NO la parte
    # el shell remoto. Args como lista (sin shell=True) => imposible inyectar comandos.
    rc = subprocess.run(
        ["rsync", "-s", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10",
         str(TOKEN_FILE), f"{DELIVER_HOST}:{DELIVER_PATH}"],
        capture_output=True, text=True,
    ).returncode
    _log(f"  entrega -> {DELIVER_HOST}: {'ok' if rc == 0 else 'rc=' + str(rc)}")


def _save_access(tok: str):
    _write_secure(TOKEN_FILE, tok + "\n")
    _log(f"  access token -> {TOKEN_FILE} (validez ~{_exp_minutes(tok)} min)")
    _deliver()


def login():
    r = requests.post(f"{AUTH}/devicecode", data={"client_id": CLIENT, "scope": SCOPE}, timeout=20)
    if r.status_code != 200:
        _log("ERROR devicecode: " + r.text[:300]); return 1
    dc = r.json()
    _log("\n  === AUTENTICA ===")
    _log("  URL:    " + dc["verification_uri"])
    _log("  CÓDIGO: " + dc["user_code"])
    _log("  (esperando login + MFA...)\n")
    iv = dc.get("interval", 5); waited = 0
    while waited < dc.get("expires_in", 900):
        time.sleep(iv); waited += iv
        pr = requests.post(f"{AUTH}/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT, "device_code": dc["device_code"]}, timeout=20)
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
            iv += 5; continue
        _log("  ❌ " + str(e) + ": " + (pr.json().get("error_description", "") or "")[:200]); return 2
    _log("  ❌ timeout"); return 3


def refresh():
    if not RT_FILE.exists():
        _log("  ❌ no hay refresh token; corre: token_refresher.py login"); return 1
    rt = RT_FILE.read_text().strip()
    r = requests.post(f"{AUTH}/token", data={
        "grant_type": "refresh_token", "client_id": CLIENT,
        "refresh_token": rt, "scope": SCOPE}, timeout=20)
    if r.status_code != 200:
        j = r.json()
        _log("  ❌ refresh falló: " + str(r.status_code) + " " + j.get("error", "") + " " + (j.get("error_description", "") or "")[:200])
        return 1
    j = r.json()
    if j.get("refresh_token"):        # rotación: persistir el refresh token nuevo
        _write_secure(RT_FILE, j["refresh_token"])
    _save_access(j["access_token"])
    return 0


def run():
    _log(f"=== token_refresher daemon (refresh cada {REFRESH_EVERY}s) ===")
    while True:
        try:
            refresh()
        except Exception as e:
            _log("  refresh exc: " + str(e))
        time.sleep(REFRESH_EVERY)


def _api_headers():
    tok = TOKEN_FILE.read_text().strip()
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json;odata=verbose"}


def test_write():
    """Sube y borra un archivo de prueba — mismo camino que subir_onedrive.py."""
    name = "spm_oauth_writetest.txt"
    enc_folder = urllib.parse.quote(DEST, safe="/")
    enc_name = urllib.parse.quote(name)
    add_url = f"{PERSONAL}/_api/web/GetFolderByServerRelativeUrl('{enc_folder}')/Files/add(url='{enc_name}',overwrite=true)"
    h = _api_headers(); h["Content-Type"] = "application/octet-stream"
    body = b"oauth write test " + str(datetime.datetime.now()).encode()
    r = requests.post(add_url, headers=h, data=body, timeout=60)
    _log(f"  ADD: HTTP {r.status_code} {'OK' if r.status_code in (200, 201) else r.text[:200]}")
    if r.status_code not in (200, 201):
        return 1
    # borrar el archivo de prueba
    server_rel = f"{DEST}/{name}"
    del_url = f"{PERSONAL}/_api/web/GetFileByServerRelativeUrl('{urllib.parse.quote(server_rel, safe='/')}')"
    hd = _api_headers(); hd["X-HTTP-Method"] = "DELETE"; hd["IF-MATCH"] = "*"
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


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"login": login, "refresh": refresh, "run": run, "test-write": test_write, "status": status}.get(cmd)
    if not fn:
        _log(__doc__); sys.exit(1)
    sys.exit(fn() or 0)


if __name__ == "__main__":
    main()
