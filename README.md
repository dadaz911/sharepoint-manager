# SharePoint Manager

Dashboard web para gestionar archivos en SharePoint/OneDrive con soporte para subidas masivas, explorador de archivos y refresh automatico de token.

## Servicio estable (systemd)

El refresh de token corre como **servicio de usuario en gold**, autogestionado con watchdog y
health-check (mismo patrón operativo que el repo `vpn`). Modelo **keep-alive**: un Chrome real
(bajo Xvfb, invisible) se mantiene vivo y se loguea vía VNC, porque M365 no re-autentica en
silencio un Chrome reiniciado.

```bash
bash deploy.sh            # instala/actualiza units + arranca
bin/spm.sh status         # estado de units, Chrome y token
bin/spm.sh login          # login M365 vía VNC (tras reboot o ~90 días)
```

Documentación: **`CLAUDE.md`** (arquitectura) y **`MANUAL.md`** (runbook operativo).

## Caracteristicas

- **Dashboard Web**: Interfaz moderna con graficos en tiempo real, monitoreo de progreso y logs
- **Subida Masiva**: Soporte para subir miles de archivos en paralelo con multiples hilos
- **Explorador SharePoint**: Navegar, buscar y descargar archivos de sitios SharePoint
- **Token Auto-Refresh**: Renovacion automatica de tokens usando Chrome DevTools Protocol
- **Descarga Paralela**: Descargas de carpetas usando ThreadPoolExecutor

## Requisitos

- Python 3.8+
- Google Chrome (para refresh de token)
- Dependencias Python:

```bash
pip install flask flask-socketio gevent gevent-websocket requests websocket-client
```

## Estructura del Proyecto

```
sharepoint-manager/
├── dashboard.py              # Servidor Flask principal
├── templates/dashboard.html  # Frontend del dashboard
├── token_daemon.py           # Daemon de refresh de token (CDP)
├── subir_paralelo.py         # Subida con hilos
├── subir_onedrive*.py        # Subidas
├── explorador_sharepoint.py  # Explorador CLI
├── config.env                # Configuración central (paths, puerto, URL)
├── bin/                      # Control: spm.sh, chrome-headless, watchdog, health-check
├── systemd/                  # Units: chrome, daemon, watchdog, healthcheck
├── deploy.sh                 # Despliegue local (symlink units + enable)
└── requirements.txt
```

## Uso

### Token automático (servicio)

El token se refresca solo vía el servicio (`bash deploy.sh`). Si la sesión M365 expiró
(~cada 90 días), reautentica con:

```bash
bin/spm.sh login
```

El token queda en `/home/daniel/Desktop/Cargue a Onedrive/.token`. Ver `MANUAL.md`.

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

- **NO** subir el archivo `.token` al repositorio (ya en `.gitignore`)
- Ruta canónica del token: `/home/daniel/Desktop/Cargue a Onedrive/.token`
- El token expira cada ~60 min (el daemon lo refresca); la sesión M365 se renueva con login vía VNC
- Los puertos CDP (Chrome) y VNC (login) escuchan **solo en 127.0.0.1**; el token nunca sale a la red

## Licencia

MIT
