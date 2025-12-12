# SharePoint Manager

Dashboard web para gestionar archivos en SharePoint/OneDrive con soporte para subidas masivas, explorador de archivos y refresh automatico de token.

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
├── templates/
│   └── dashboard.html        # Frontend del dashboard
├── auto_token_refresh.py     # Daemon de refresh de token
├── subir_paralelo.py         # Script de subida con hilos
├── subir_onedrive.py         # Script de subida simple
├── subir_onedrive_auto.py    # Subida con auto-refresh
├── explorador_sharepoint.py  # Explorador CLI
├── explorador_oficina.py     # Explorador para sitio especifico
├── start_chrome_debug.sh     # Iniciar Chrome con CDP
├── token_daemon.py           # Servicio de token
└── .gitignore
```

## Uso

### 1. Iniciar Chrome con Debug

```bash
./start_chrome_debug.sh
```

### 2. Obtener Token

Navegar a SharePoint en Chrome y copiar el token de las DevTools (Network > Headers > Authorization)

Guardar en archivo `.token`:
```bash
echo "eyJhbGc..." > .token
```

### 3. Iniciar Dashboard

```bash
python3 dashboard.py
```

Acceder a: http://localhost:5000

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

- **NO** subir el archivo `.token` al repositorio
- El token expira cada ~60 minutos
- El refresh automatico requiere Chrome con sesion activa

## Licencia

MIT
