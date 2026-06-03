# CLAUDE.md — SharePoint Manager

## Descripción

Mantiene fresco un **token de SharePoint/OneDrive del tenant SHD** (`shdgov-my.sharepoint.com`)
para que los scripts de subida masiva (`subir_*.py`) y el dashboard operen sin intervención.
El token de M365 expira cada ~60 min; un daemon lo refresca leyéndolo de un Chrome vía Chrome
DevTools Protocol (CDP). Corre como servicio de usuario en **gold**.

## Modelo: keep-alive (importante)

Se verificó empíricamente que **el Conditional Access del tenant SHD NO re-autentica en
silencio un Chrome reiniciado** — ni `--headless` ni Chrome real bajo Xvfb: tras reiniciar,
la cookie persistente (`ESTSAUTHPERSISTENT`) no basta y M365 vuelve a pedir contraseña.
Por eso el único modelo que funciona es **mantener vivo el mismo Chrome que hizo login**:

- El Chrome corre **real** (UA normal, no "HeadlessChrome") sobre un **display X virtual (Xvfb)**,
  invisible, y **no se reinicia** durante la operación.
- El **login** (~tras reboot o cada ~90 días) se hace **vía VNC contra ESE mismo Chrome**
  (`spm.sh login`), sin relanzarlo → la sesión se preserva.
- Mientras Chrome siga vivo, el daemon refresca el token recargando la página. Si Chrome cae
  (reboot/crash), la sesión se pierde y el watchdog avisa "login requerido".

(Esto es la idea que perseguía el `cage`+`wayvnc` original; `cage` fallaba al *arrancar* Chrome,
pero la intención —Chrome real, no headless, accesible por VNC— era la correcta.)

## Arquitectura

```
sharepoint-xvfb.service     Xvfb :77 (display X virtual, -ac -nolisten tcp)
   ▲ BindsTo
sharepoint-chrome.service   google-chrome REAL en :77, Restart=ON-FAILURE (NUNCA always:
   (CDP solo 127.0.0.1:9222, --remote-allow-origins acotado)   reiniciar pierde la sesión)
   ▲ Requires + After
sharepoint-daemon.service   token_daemon.py: cada 5 min lee el token vía CDP-WebSocket y, si
   ExecStartPre=wait-cdp.sh  quedan <15 min, recarga la pestaña y reextrae a TOKEN_FILE.

sharepoint-watchdog.timer   (5 min) → bin/sharepoint-watchdog.sh
   revive chrome/daemon si caen; si la sesión M365 murió → notifica "login requerido"
   (cooldown anti-thrashing); no interviene durante un login (flag).
sharepoint-healthcheck.timer (lunes 9am) → bin/health-check.sh (ok/warn/crit)

LOGIN:  spm.sh login → x11vnc -display :77 -localhost:5900 → visor (remmina) → autenticas
        EN el Chrome vivo → ENTER captura el token. Chrome NO se reinicia.
```

## Archivos

| Archivo | Rol |
|---------|-----|
| `config.env` | Config única (TOKEN_FILE, CDP_PORT, ONEDRIVE_URL, CHROME_PROFILE, SPM_DISPLAY=:77, VNC_PORT). bash `source` + systemd `EnvironmentFile=`. |
| `bin/chrome-xvfb.sh` | Lanza Chrome **real** en `$SPM_DISPLAY`. `--remote-allow-origins=http://localhost:PORT` (Chrome 148 rechaza el CDP-WS sin él). |
| `bin/wait-cdp.sh` | ExecStartPre del daemon: espera al CDP. |
| `bin/spm.sh` | Control: `start/stop/restart/status/refresh/health/vnc/login`. `login` = VNC sin reiniciar Chrome. |
| `bin/sharepoint-watchdog.sh` | Watchdog (estilo `vpn/vpn-watchdog.sh`). |
| `bin/health-check.sh` | Reporte de salud (estilo `rpi3/maintenance/health-check.sh`). |
| `systemd/*.{service,timer}` | Units (symlinkeadas a `~/.config/systemd/user` por `deploy.sh`). |
| `deploy.sh` | Despliegue LOCAL: symlink units + daemon-reload + enable --now. Idempotente. |
| `token_daemon.py` | Daemon de refresco (config por entorno; escribe el token con `chmod 0600`). |

Dependencias de sistema: `xvfb`, `x11vnc`, un visor VNC (`remmina` o `tigervnc-viewer`).
Python: `requirements.txt` (requests, websocket-client) en `.venv`.

## Operación

```bash
bash deploy.sh                 # instalar/actualizar el servicio
bin/spm.sh status              # units + Chrome + URL de pestaña + token
bin/spm.sh login               # LOGIN M365 vía VNC (tras reboot o ~90 días) — ver MANUAL.md
journalctl --user -u sharepoint-daemon -f
```

## Notas clave

- **Token**: `/home/daniel/Desktop/Cargue a Onedrive/.token` (canónico, compartido por
  `token_daemon.py`/`refresh_token.py`/`explorador_sharepoint.py`; escrito `0600`). Los
  `subir_*.py` usan además `<dir_datos>/.token` (copia junto a los datos; patrón intencional).
- **Seguridad**: CDP y VNC escuchan **solo en 127.0.0.1**; el token nunca sale a la red.
- **Reboot/crash de Chrome ⇒ re-login** (la sesión M365 no sobrevive). gold reinicia poco.
- **Linger** (`enable-linger`) ya activo: las units arrancan tras reboot (logueadas requieren `spm.sh login`).

Manual operativo detallado: **`MANUAL.md`**.
