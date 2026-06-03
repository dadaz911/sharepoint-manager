# Manual operativo — SharePoint Manager

Runbook del servicio que mantiene fresco el token de SharePoint/OneDrive (tenant SHD) en **gold**.
Arquitectura y modelo en `CLAUDE.md`. **Modelo keep-alive**: un Chrome real (bajo Xvfb) se
mantiene vivo y se loguea vía VNC; M365 no re-autentica en silencio un Chrome reiniciado.

## 1. Instalación / actualización

```bash
# Dependencias de sistema (una vez):
sudo apt install xvfb x11vnc                 # display virtual + servidor VNC
sudo apt install remmina                     # (o tigervnc-viewer) — visor VNC para el login

cd ~/claudecode/sharepoint-manager
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt   # 1ª vez
bash deploy.sh
```

`deploy.sh` symlinkea las units a `~/.config/systemd/user`, hace `daemon-reload` y `enable --now`
de Xvfb + Chrome + daemon + timers. Idempotente.

## 2. Operación diaria (sin intervención)

```bash
bin/spm.sh status      # units + Chrome CDP + URL de la pestaña + token
bin/spm.sh refresh     # forzar un refresco ahora
bin/spm.sh health      # ok/warn/crit
journalctl --user -u sharepoint-daemon -f
cat "$XDG_RUNTIME_DIR/sharepoint-watchdog.log"
```

`spm.sh status` muestra la URL de la pestaña: si está en `login.microsoftonline.com`, la sesión
expiró → haz login (sección 3).

## 3. Login de Microsoft (tras reboot, o ~cada 90 días)

La sesión M365 vive mientras el Chrome de larga vida siga corriendo. Si gold reinicia, Chrome
crashea, o pasan ~90 días, hay que reautenticar. El **watchdog** lo detecta y notifica
*"Sesión SharePoint expirada — corre: spm.sh login"*.

```bash
cd ~/claudecode/sharepoint-manager
bin/spm.sh login
```

Esto, **sin reiniciar el Chrome** (preserva la sesión):
1. Asegura que Xvfb + Chrome están arriba.
2. Abre `x11vnc` en `localhost:5900` contra el display `:77` del Chrome.
3. Abre automáticamente un visor (Remmina) apuntando a `localhost:5900` — ves el Chrome.
4. **Inicias sesión** (usuario SHD + MFA) hasta ver tus archivos.
5. Pulsas **ENTER** en la terminal → captura el token y cierra el VNC. El Chrome sigue vivo.

**Login remoto** (sin estar en gold): túnel SSH + visor en tu equipo:
```bash
ssh -L 5900:localhost:5900 gold     # en tu equipo
bin/spm.sh login                     # en gold (por SSH); conecta tu visor VNC a localhost:5900
```

> El login requiere tus credenciales gubernamentales y MFA: es **manual**, no se automatiza.

## 4. Watchdog (`sharepoint-watchdog.timer`, cada 5 min)

| Estado | Condición | Acción |
|--------|-----------|--------|
| LOGIN-EN-CURSO | flag de login | no toca nada |
| CHROME-DOWN | CDP :9222 no responde | reinicia `sharepoint-chrome` (perderá sesión → login) |
| DAEMON-DOWN | daemon inactivo | reinicia `sharepoint-daemon` |
| SETTLING | token vencido, 1ª vez, todo sano | espera |
| SESION-EXPIRADA | token vencido ≥2 ticks, todo sano | notifica "login requerido" + cooldown 6h |
| TOKEN-OK | token con margen | limpia contadores/flags |

> El Chrome usa `Restart=on-failure` (NO `always`): reiniciarlo pierde la sesión, así que no se
> respawnea en bucle; tras una caída, el watchdog avisa que hace falta `spm.sh login`.

## 5. Diagnóstico

```bash
curl -s http://localhost:9222/json/version           # ¿Chrome/CDP arriba?
curl -s http://localhost:9222/json | grep -o '"url":[^,]*' | head   # ¿en login o en SharePoint?
ls /tmp/.X11-unix/X77                                 # ¿Xvfb :77 arriba?
bin/spm.sh status
```

| Síntoma | Causa | Solución |
|---------|-------|----------|
| Pestaña en `login.microsoftonline.com`, token vencido | Sesión M365 caída (reboot/crash/90d) | `bin/spm.sh login` |
| `x11vnc ... Wayland ... Exiting` | x11vnc detecta Wayland | ya resuelto: `spm.sh` desetea `WAYLAND_DISPLAY` |
| Chrome con UA `HeadlessChrome` | regresión | debe ser Chrome real bajo Xvfb (`chrome-xvfb.sh`) |
| daemon `activating` sin fin | CDP no sube | `journalctl --user -u sharepoint-chrome` / `-u sharepoint-xvfb` |

## 6. Detener / desinstalar

```bash
bin/spm.sh stop
systemctl --user disable --now sharepoint-xvfb sharepoint-chrome sharepoint-daemon \
  sharepoint-watchdog.timer sharepoint-healthcheck.timer
```
