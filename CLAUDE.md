# CLAUDE.md — SharePoint Manager

## Descripción

Mantiene fresco un **token de SharePoint/M365 del tenant SHD** para subir soportes a SharePoint
sin intervención. El token se renueva por **OAuth2 (refresh token), sin navegador**.

## Arquitectura (OAuth, browser-less)

```
device-code login (1 vez, MFA)  ->  refresh token en disco (Pi)
                                         │
Pi (raspberrypi3, siempre ON)            ▼
  sharepoint-refresher.service: token_refresher.py refresca el access token cada ~50 min
  (grant_type=refresh_token, solo requests) -> ~/.cache/spm/.token
                                         │
            ┌────────────────────────────┼───────────────────────────┐
            ▼                             ▼                            ▼
  gold: oauth-health.timer        carbon / host de carga      (re-login ~90 días)
  avisa SOLO si el token          jala el token del Pi y      device-code de nuevo
  deja de refrescarse             corre los uploaders         (único toque interactivo)
```

**Por qué OAuth y no navegador:** el tenant SHD no re-autentica en silencio un navegador
reiniciado (se probó headless y real). El refresh-token sí se redime no-interactivo (verificado:
~14 h / 18 ciclos / 0 fallos, sosteniendo un cargue de 157k archivos). El stack de navegador
(cage/wayvnc/xvfb/chrome keep-alive) fue **retirado**; queda en el historial de git.

## Archivos

| Archivo | Rol |
|---------|-----|
| `token_refresher.py` | Motor OAuth (corre en la Pi). `login` (device-code) / `refresh` / `run` (daemon) / `test-write` / `status`. Escribe el token con `chmod 0600`. |
| `pi/config.env`, `pi/sharepoint-refresher.service` | Config + unit del refresher en la Pi. |
| `bin/oauth-health.sh` + `systemd/sharepoint-oauth-health.{service,timer}` | gold: notifica SOLO si el token deja de refrescarse (settling 45 min + cooldown 6 h). |
| `bin/pull-token.sh` | Jala el token del Pi por rsync (para hosts que corran cargas). |
| `config.env` | Config gold OAuth (PI_HOST, PI_TOKEN, TOKEN_FILE). |
| `deploy.sh` | Despliegue gold: instala el notificador + retira units de navegador. |
| `subir_masivo.py` + `run-upload.sh` | Uploader masivo robusto (Retry-After, resume, whitelist pdf/png). |
| `consolidar_masivo.py` + `run-consolidate.sh` | Consolida carpetas en SharePoint (MoveCopyUtil.MoveFile server-side). |
| `cleanup-redundant.py`, `consolidar-origen.py` | Limpieza de carpetas redundantes (SP) y consolidación del origen local. |
| `subir_*.py`, `explorador_*.py`, `dashboard.py`, … | Herramientas previas (subida/exploración/dashboard). |

Dependencias: `requirements.txt` (solo `requests`). En la Pi: Python 3 + requests (ya presentes).

## Operación

```bash
# Pi: refrescador (una vez)
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py login'   # device-code, MFA
# gold: notificador de salud
bash deploy.sh
# carbon: cargue (ver MANUAL.md)
```

## Notas clave

- **Token**: lo refresca la Pi a `~/.cache/spm/.token`; los consumidores lo jalan. La ruta
  canónica para los uploaders es `/home/daniel/Desktop/Cargue a Onedrive/.token`.
- **Re-login** (device-code, navegador/MFA) solo cuando el refresh token caduque (~90 días,
  cambio de contraseña o revocación). El `oauth-health` avisa cuándo.
- **Cliente OAuth**: público "Microsoft Office" `d3590ed6-...`; el token sirve tanto para el
  OneDrive personal (`-my`) como para sitios de equipo (`shdgov.sharepoint.com/sites/...`).

Runbook detallado y herramientas de cargue/consolidación: **`MANUAL.md`**.
