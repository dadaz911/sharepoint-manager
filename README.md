# SharePoint Manager

Dashboard web para gestionar archivos en SharePoint/OneDrive con soporte para subidas masivas, explorador de archivos y refresh automatico de token.

## Token automático (OAuth, sin navegador)

El token de SharePoint/M365 se renueva por **OAuth2 refresh-token, sin navegador**. El motor
(`token_refresher.py`) corre en la **Pi** (siempre encendida) y refresca el token cada ~50 min;
**gold** solo vigila y avisa si deja de refrescarse; los cargues corren donde estén los datos.

```bash
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py login'   # device-code (1 vez / ~90 días)
bash deploy.sh                                                           # gold: notificador de salud
```

Documentación: **`CLAUDE.md`** (arquitectura) y **`MANUAL.md`** (runbook + cargues/consolidación).

## Caracteristicas

- **Dashboard Web**: Interfaz moderna con graficos en tiempo real, monitoreo de progreso y logs
- **Subida Masiva**: Soporte para subir miles de archivos en paralelo con multiples hilos
- **Explorador SharePoint**: Navegar, buscar y descargar archivos de sitios SharePoint
- **Token Auto-Refresh**: Renovación automática por OAuth2 refresh-token (sin navegador), en la Pi
- **Descarga Paralela**: Descargas de carpetas usando ThreadPoolExecutor

## Requisitos

- Python 3.8+
- Dependencias Python:

```bash
pip install -r requirements.txt   # requests (refresh OAuth + uploaders)
# dashboard (opcional): pip install flask flask-socketio gevent gevent-websocket
```

## Estructura del Proyecto

```
sharepoint-manager/
├── token_refresher.py        # Motor OAuth (refresh token, sin navegador) — corre en la Pi
├── pi/                       # Config + unit del refresher en la Pi
├── bin/oauth-health.sh       # gold: avisa solo si el token deja de refrescarse
├── bin/pull-token.sh         # jala el token del Pi (hosts de carga)
├── subir_masivo.py           # Uploader masivo robusto (Retry-After, resume, whitelist)
├── consolidar_masivo.py      # Consolida carpetas en SharePoint (MoveCopyUtil server-side)
├── subir_*.py, explorador_*.py, dashboard.py   # herramientas previas
├── systemd/                  # units OAuth (sharepoint-oauth-health.*)
├── deploy.sh                 # despliegue gold (notificador + cutover)
└── requirements.txt
```

## Uso

### Token automático (servicio)

El token se refresca solo: el refresher OAuth corre en la **Pi** y renueva el token cada ~50 min
sin navegador. `gold` solo vigila (`bash deploy.sh` instala el notificador). Si el *refresh token*
caduca (~cada 90 días), reautentica una vez con device-code:

```bash
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py login'
```

Los hosts de carga jalan el token del Pi (`bin/pull-token.sh` / `rsync raspberrypi3:~/.cache/spm/.token`).
Ver `MANUAL.md`.

### Dashboard

```bash
python3 dashboard.py    # http://localhost:5000
```

### 4. Subida de Archivos

Desde el dashboard o usando el script:

```bash
echo "4" | python3 subir_paralelo.py  # 4 hilos
```

## Configuracion

Editar las URLs de SharePoint en los archivos:
- `dashboard.py` - Variable `SHAREPOINT_SITES`
- `explorador_sharepoint.py` - Variable `CONFIG`

## API Endpoints

| Endpoint | Metodo | Descripcion |
|----------|--------|-------------|
| `/api/status` | GET | Estado actual del proceso |
| `/api/start` | POST | Iniciar subida |
| `/api/stop` | POST | Detener subida |
| `/api/explorer/sites` | GET | Listar sitios SharePoint |
| `/api/explorer/browse` | GET | Explorar carpeta |
| `/api/explorer/download-folder` | GET | Descargar carpeta como ZIP |
| `/api/token/refresh` | POST | Forzar refresh de token |

## Notas de Seguridad

- **NO** subir `.token` ni el *refresh token* al repositorio (ya en `.gitignore`)
- El token de acceso expira cada ~60 min; el refresher OAuth en la Pi lo renueva automáticamente
- Sin navegador, sin CDP, sin VNC: el flujo es OAuth2 refresh-token puro (solo HTTP a `login.microsoftonline.com`)
- El *refresh token* se guarda con permisos `0600`; el único paso interactivo es el device-code (~90 días)
- Entre hosts, el token viaja por la malla Tailscale vía `rsync` (sin exponer puertos a la red)

## Licencia

MIT
