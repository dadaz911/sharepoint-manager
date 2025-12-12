#!/usr/bin/env python3
"""
Dashboard Web para Control de Subida a OneDrive
Flask + Flask-SocketIO para tiempo real
"""

import os
import sys
import json
import time
import base64
import signal
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

# Configuracion de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuracion hardcodeada
CONFIG = {
    "base_url": "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web",
    "dest_folder": "/personal/dzuniga_shd_gov_co1/Documents/Pruebas",
    "source_dir": "/home/daniel/Desktop/Cargue a Onedrive",
    "total_files": 92579,
    "cdp_port": 9333,
    "chrome_profile": "/home/daniel/.config/onedrive-uploader-chrome",
    "onedrive_url": "https://shdgov-my.sharepoint.com",
}

# Configuracion de sitios SharePoint para el explorador
SHAREPOINT_SITES = {
    "personal": {
        "name": "Mi OneDrive",
        "base_url": "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web",
        "root_folder": "/personal/dzuniga_shd_gov_co1/Documents",
        "cache_file": ".sharepoint_map.json"
    },
    "oficina": {
        "name": "Oficina Depuración Cartera",
        "base_url": "https://shdgov.sharepoint.com/sites/OficinadeDepuracindeCartera/_api/web",
        "root_folder": "/sites/OficinadeDepuracindeCartera/Documentos compartidos",
        "cache_file": ".oficina_map.json"
    }
}

# Rutas de archivos
BASE_DIR = Path(CONFIG["source_dir"])
TOKEN_FILE = BASE_DIR / ".token"
PROGRESS_FILE = BASE_DIR / ".upload_progress.json"
HISTORY_FILE = BASE_DIR / ".dashboard_history.json"

# Inicializar Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = 'onedrive-dashboard-secret-2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Estado global
class DashboardState:
    def __init__(self):
        self.upload_process: Optional[subprocess.Popen] = None
        self.is_running = False
        self.logs: deque = deque(maxlen=100)
        self.history: List[Dict] = []
        self.last_uploaded_count = -1  # -1 indica no inicializado
        self.last_check_time = time.time()
        self.speed_samples: deque = deque(maxlen=30)
        self.monitor_thread: Optional[threading.Thread] = None
        self.stop_monitor = False
        self.last_refresh_attempt = 0  # Timestamp del último intento de refresh
        self.known_uploaded: set = set()  # Archivos ya conocidos como subidos
        self.known_errors: set = set()  # Archivos ya conocidos con error
        self.last_log_pos: int = 0  # Posicion del ultimo byte leido del log de upload
        self.last_log_uploaded: int = 0  # Ultimo conteo de uploaded del log
        self.config = {
            "threads": 6,
            "auto_refresh_token": True,
        }
        self.load_history()

    def load_history(self):
        """Cargar historial desde archivo"""
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE) as f:
                    data = json.load(f)
                    self.history = data.get("history", [])[-180:]  # ultimos 30 min (cada 10s)
                    self.speed_samples = deque(data.get("speed_samples", []), maxlen=30)
            except Exception as e:
                logger.error(f"Error cargando historial: {e}")

    def save_history(self):
        """Guardar historial a archivo"""
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump({
                    "history": list(self.history)[-180:],
                    "speed_samples": list(self.speed_samples),
                    "updated_at": datetime.now().isoformat()
                }, f)
        except Exception as e:
            logger.error(f"Error guardando historial: {e}")

    def add_log(self, message: str, level: str = "info"):
        """Agregar entrada al log"""
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level
        }
        self.logs.append(entry)
        socketio.emit('log_event', entry)
        logger.log(getattr(logging, level.upper(), logging.INFO), message)

state = DashboardState()

# =============================================================================
# Funciones de utilidad
# =============================================================================

UPLOAD_LOG_FILE = Path("/tmp/upload_paralelo.log")

def get_upload_log_progress() -> Dict[str, Any]:
    """Lee el log del proceso de subida para obtener progreso en tiempo real"""
    result = {"uploaded": 0, "speed": 0, "percentage": 0, "new_count": 0}

    if not UPLOAD_LOG_FILE.exists():
        return result

    try:
        # Leer las ultimas lineas del log
        with open(UPLOAD_LOG_FILE, 'rb') as f:
            # Ir al final y leer las ultimas 5000 bytes
            f.seek(0, 2)  # Ir al final
            size = f.tell()
            f.seek(max(0, size - 5000))
            content = f.read().decode('utf-8', errors='ignore')

        # Buscar el ultimo patron [XXXXX/92579] en el log
        import re
        matches = re.findall(r'\[(\d+)/(\d+)\]\s+(\d+\.?\d*)%\s+\|\s+(\d+)/min', content)

        if matches:
            last_match = matches[-1]
            result["uploaded"] = int(last_match[0])
            result["total"] = int(last_match[1])
            result["percentage"] = float(last_match[2])
            result["speed"] = int(last_match[3])

            # Detectar cuantos archivos nuevos hay desde la ultima vez
            if state.last_log_uploaded > 0:
                result["new_count"] = result["uploaded"] - state.last_log_uploaded

        return result
    except Exception as e:
        logger.debug(f"Error leyendo log de upload: {e}")
        return result


def get_token_info() -> Dict[str, Any]:
    """Obtener informacion del token"""
    if not TOKEN_FILE.exists():
        return {"valid": False, "minutes_remaining": 0, "expires_at": None}

    try:
        token = TOKEN_FILE.read_text().strip()
        if not token:
            return {"valid": False, "minutes_remaining": 0, "expires_at": None}

        parts = token.split('.')
        payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = datetime.fromtimestamp(int(data['exp']))
        remaining = (exp - datetime.now()).total_seconds() / 60

        return {
            "valid": remaining > 5,
            "minutes_remaining": max(0, remaining),
            "expires_at": exp.strftime("%H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Error leyendo token: {e}")
        return {"valid": False, "minutes_remaining": 0, "expires_at": None}


def get_progress() -> Dict[str, Any]:
    """Obtener progreso de subida"""
    if not PROGRESS_FILE.exists():
        return {
            "uploaded": 0,
            "errors": 0,
            "total": CONFIG["total_files"],
            "percentage": 0,
            "error_list": []
        }

    try:
        with open(PROGRESS_FILE) as f:
            data = json.load(f)

        uploaded_list = data.get("uploaded", [])
        uploaded = len(uploaded_list)
        errors = data.get("errors", [])
        error_count = len(errors)
        total = CONFIG["total_files"]

        return {
            "uploaded": uploaded,
            "errors": error_count,
            "total": total,
            "percentage": round((uploaded / total) * 100, 2) if total > 0 else 0,
            "error_list": errors[-50:],  # ultimos 50 errores
            "uploaded_set": set(uploaded_list),  # set para comparacion rapida
            "errors_set": set(e.get("file", "") for e in errors)  # set de archivos con error
        }
    except Exception as e:
        logger.error(f"Error leyendo progreso: {e}")
        return {
            "uploaded": 0,
            "errors": 0,
            "total": CONFIG["total_files"],
            "percentage": 0,
            "error_list": [],
            "uploaded_set": set(),
            "errors_set": set()
        }


def calculate_speed() -> float:
    """Calcular velocidad de subida (archivos/min)"""
    progress = get_progress()
    current_count = progress["uploaded"]
    current_time = time.time()

    # Primera ejecucion: inicializar sin calcular velocidad
    if state.last_uploaded_count < 0:
        state.last_uploaded_count = current_count
        state.last_check_time = current_time
        # Retornar promedio de muestras existentes o 0
        if state.speed_samples:
            return round(sum(state.speed_samples) / len(state.speed_samples), 1)
        return 0

    elapsed_seconds = current_time - state.last_check_time
    files_diff = current_count - state.last_uploaded_count

    # Solo calcular si paso suficiente tiempo (minimo 2 segundos) y menos de 2 minutos
    if elapsed_seconds >= 2 and elapsed_seconds < 120:
        elapsed_minutes = elapsed_seconds / 60.0
        speed = files_diff / elapsed_minutes  # archivos por minuto

        # Solo agregar muestras razonables (0 a 2000 archivos/min)
        if 0 <= speed <= 2000:
            state.speed_samples.append(speed)

        # Actualizar estado
        state.last_uploaded_count = current_count
        state.last_check_time = current_time

    # Retornar promedio de las ultimas muestras
    if state.speed_samples:
        return round(sum(state.speed_samples) / len(state.speed_samples), 1)
    return 0


def check_chrome_cdp() -> bool:
    """Verificar si Chrome CDP esta disponible"""
    try:
        import requests
        r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json/version", timeout=2)
        return r.status_code == 200
    except:
        return False


# =============================================================================
# Funciones del Explorador SharePoint
# =============================================================================

def explore_sharepoint_folder(site_key: str, folder_path: str) -> Dict[str, Any]:
    """Explorar una carpeta de SharePoint y obtener su contenido"""
    import requests
    import urllib.parse

    if site_key not in SHAREPOINT_SITES:
        return {"error": f"Sitio no encontrado: {site_key}"}

    site = SHAREPOINT_SITES[site_key]
    base_url = site["base_url"]

    # Obtener token
    if not TOKEN_FILE.exists():
        return {"error": "Token no disponible"}

    token = TOKEN_FILE.read_text().strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=verbose"
    }

    encoded_folder = urllib.parse.quote(folder_path, safe='/')
    resultado = {
        "path": folder_path,
        "site": site_key,
        "site_name": site["name"],
        "folders": [],
        "files": [],
        "file_count": 0,
        "total_size": 0
    }

    # Obtener subcarpetas
    url = f"{base_url}/GetFolderByServerRelativeUrl('{encoded_folder}')/Folders"
    url += "?$select=Name,ItemCount,ServerRelativeUrl"

    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            folders = data.get('d', {}).get('results', [])
            for f in folders:
                name = f.get('Name', '')
                if not name.startswith('_'):
                    resultado["folders"].append({
                        "name": name,
                        "path": f.get('ServerRelativeUrl'),
                        "item_count": f.get('ItemCount', 0)
                    })
        elif r.status_code == 401:
            return {"error": "Token expirado o inválido"}
    except Exception as e:
        logger.error(f"Error explorando carpetas: {e}")
        return {"error": f"Error de conexión: {str(e)}"}

    # Obtener archivos
    url = f"{base_url}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files"
    url += "?$select=Name,Length,TimeLastModified,ServerRelativeUrl&$top=200"

    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            files = data.get('d', {}).get('results', [])
            for f in files:
                size = int(f.get('Length', 0))
                resultado["files"].append({
                    "name": f.get('Name'),
                    "size": size,
                    "size_formatted": format_file_size(size),
                    "modified": f.get('TimeLastModified'),
                    "path": f.get('ServerRelativeUrl')
                })
                resultado["total_size"] += size
            resultado["file_count"] = len(files)
        elif r.status_code == 500:
            resultado["file_count"] = ">5000"
    except Exception as e:
        logger.error(f"Error explorando archivos: {e}")

    resultado["total_size_formatted"] = format_file_size(resultado["total_size"])
    return resultado


def format_file_size(size_bytes: int) -> str:
    """Formatear tamaño de archivo"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_sharepoint_file_url(site_key: str, file_path: str) -> Optional[str]:
    """Obtener URL de descarga de un archivo"""
    import urllib.parse

    if site_key not in SHAREPOINT_SITES:
        return None

    site = SHAREPOINT_SITES[site_key]
    base_url = site["base_url"]
    encoded_path = urllib.parse.quote(file_path, safe='/')
    return f"{base_url}/GetFileByServerRelativeUrl('{encoded_path}')/$value"


def get_file_details(site_key: str, file_path: str) -> Dict[str, Any]:
    """Obtener detalles completos de un archivo"""
    import requests
    import urllib.parse

    if site_key not in SHAREPOINT_SITES:
        return {"error": f"Sitio no encontrado: {site_key}"}

    site = SHAREPOINT_SITES[site_key]
    base_url = site["base_url"]

    if not TOKEN_FILE.exists():
        return {"error": "Token no disponible"}

    token = TOKEN_FILE.read_text().strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=verbose"
    }

    encoded_path = urllib.parse.quote(file_path, safe='/')
    url = f"{base_url}/GetFileByServerRelativeUrl('{encoded_path}')"
    url += "?$select=Name,Length,TimeCreated,TimeLastModified,ServerRelativeUrl,CheckOutType,MajorVersion,MinorVersion,UIVersionLabel,Author/Title,ModifiedBy/Title"
    url += "&$expand=Author,ModifiedBy"

    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json().get('d', {})
            size = int(data.get('Length', 0))
            name = data.get('Name', '')
            ext = name.split('.')[-1].lower() if '.' in name else ''

            return {
                "type": "file",
                "name": name,
                "extension": ext,
                "path": data.get('ServerRelativeUrl'),
                "size": size,
                "size_formatted": format_file_size(size),
                "created": data.get('TimeCreated'),
                "modified": data.get('TimeLastModified'),
                "author": data.get('Author', {}).get('Title', 'Desconocido'),
                "modified_by": data.get('ModifiedBy', {}).get('Title', 'Desconocido'),
                "version": data.get('UIVersionLabel', '1.0'),
                "site": site_key,
                "site_name": site["name"]
            }
        elif r.status_code == 401:
            return {"error": "Token expirado"}
        else:
            return {"error": f"Error {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def get_folder_details(site_key: str, folder_path: str) -> Dict[str, Any]:
    """Obtener detalles completos de una carpeta"""
    import requests
    import urllib.parse

    if site_key not in SHAREPOINT_SITES:
        return {"error": f"Sitio no encontrado: {site_key}"}

    site = SHAREPOINT_SITES[site_key]
    base_url = site["base_url"]

    if not TOKEN_FILE.exists():
        return {"error": "Token no disponible"}

    token = TOKEN_FILE.read_text().strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=verbose"
    }

    encoded_path = urllib.parse.quote(folder_path, safe='/')
    url = f"{base_url}/GetFolderByServerRelativeUrl('{encoded_path}')"
    url += "?$select=Name,ItemCount,ServerRelativeUrl,TimeCreated,TimeLastModified"

    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json().get('d', {})

            # Contar subcarpetas y archivos
            folder_result = explore_sharepoint_folder(site_key, folder_path)
            subfolder_count = len(folder_result.get('folders', []))
            file_count = folder_result.get('file_count', 0)
            total_size = folder_result.get('total_size', 0)

            return {
                "type": "folder",
                "name": data.get('Name', folder_path.split('/')[-1]),
                "path": data.get('ServerRelativeUrl'),
                "item_count": data.get('ItemCount', 0),
                "subfolder_count": subfolder_count,
                "file_count": file_count,
                "total_size": total_size,
                "total_size_formatted": format_file_size(total_size),
                "created": data.get('TimeCreated'),
                "modified": data.get('TimeLastModified'),
                "site": site_key,
                "site_name": site["name"]
            }
        elif r.status_code == 401:
            return {"error": "Token expirado"}
        else:
            return {"error": f"Error {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def list_folder_files_recursive(site_key: str, folder_path: str, max_files: int = 500) -> List[Dict]:
    """Listar todos los archivos de una carpeta recursivamente"""
    import requests
    import urllib.parse

    if site_key not in SHAREPOINT_SITES:
        return []

    site = SHAREPOINT_SITES[site_key]
    base_url = site["base_url"]

    if not TOKEN_FILE.exists():
        return []

    token = TOKEN_FILE.read_text().strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=verbose"
    }

    all_files = []

    def explore_recursive(path: str, depth: int = 0):
        if len(all_files) >= max_files or depth > 5:
            return

        encoded_path = urllib.parse.quote(path, safe='/')

        # Obtener archivos
        url = f"{base_url}/GetFolderByServerRelativeUrl('{encoded_path}')/Files"
        url += "?$select=Name,Length,ServerRelativeUrl&$top=200"

        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                files = r.json().get('d', {}).get('results', [])
                for f in files:
                    if len(all_files) >= max_files:
                        break
                    all_files.append({
                        "name": f.get('Name'),
                        "size": int(f.get('Length', 0)),
                        "path": f.get('ServerRelativeUrl')
                    })
        except:
            pass

        # Obtener subcarpetas y explorar
        url = f"{base_url}/GetFolderByServerRelativeUrl('{encoded_path}')/Folders"
        url += "?$select=Name,ServerRelativeUrl"

        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                folders = r.json().get('d', {}).get('results', [])
                for folder in folders:
                    name = folder.get('Name', '')
                    if not name.startswith('_') and len(all_files) < max_files:
                        explore_recursive(folder.get('ServerRelativeUrl'), depth + 1)
        except:
            pass

    explore_recursive(folder_path)
    return all_files


def get_token_expiration(token: str) -> int:
    """Extraer timestamp de expiración de un JWT token"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return 0
        payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data.get('exp', 0))
    except:
        return 0


def refresh_token_cdp() -> bool:
    """Refrescar token via Chrome CDP - versión mejorada"""
    if not check_chrome_cdp():
        state.add_log("Chrome CDP no disponible", "error")
        return False

    try:
        import requests
        import websocket

        # Obtener expiración del token actual para comparar después
        current_token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""
        current_exp = get_token_expiration(current_token)

        # Obtener tabs
        r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json", timeout=5)
        tabs = r.json()

        # Buscar tab de OneDrive
        tab = None
        for t in tabs:
            if "sharepoint" in t.get("url", "").lower():
                tab = t
                break

        if not tab and tabs:
            tab = tabs[0]
            # Navegar a OneDrive
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
            ws.send(json.dumps({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": CONFIG["onedrive_url"]}
            }))
            ws.recv()
            ws.close()
            time.sleep(8)

            r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json", timeout=5)
            tabs = r.json()
            tab = tabs[0] if tabs else None

        if not tab:
            state.add_log("No se encontro tab de OneDrive", "error")
            return False

        # Conectar al tab
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)

        # PASO 1: Borrar el token de localStorage para forzar refresh
        js_delete = """
        (function() {
            const keys = Object.keys(localStorage);
            let deleted = 0;
            keys.forEach(k => {
                if (k.includes('sharepoint_selfissued') && k.includes('shdgov-my.sharepoint.com')) {
                    localStorage.removeItem(k);
                    deleted++;
                }
            });
            return deleted;
        })()
        """
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": js_delete, "returnByValue": True}
        }))
        ws.recv()

        # PASO 2: Recargar página (SharePoint generará nuevo token)
        ws.send(json.dumps({"id": 2, "method": "Page.reload"}))
        ws.recv()

        # Esperar más tiempo para que SharePoint autentique
        time.sleep(10)

        # PASO 3: Extraer nuevo token
        js_extract = """
        (function() {
            const keys = Object.keys(localStorage);
            const spKey = keys.find(k => k.includes('sharepoint_selfissued') && k.includes('shdgov-my.sharepoint.com'));
            if (spKey) {
                try {
                    const data = JSON.parse(localStorage.getItem(spKey));
                    return data.value;
                } catch(e) {
                    return localStorage.getItem(spKey);
                }
            }
            return null;
        })()
        """

        ws.send(json.dumps({
            "id": 3,
            "method": "Runtime.evaluate",
            "params": {"expression": js_extract, "returnByValue": True}
        }))

        response = json.loads(ws.recv())
        ws.close()

        if "result" in response and "result" in response["result"]:
            new_token = response["result"]["result"].get("value")
            if new_token and len(new_token) > 100:
                new_exp = get_token_expiration(new_token)

                # Solo guardar si el nuevo token tiene expiración más tardía
                if new_exp > current_exp:
                    TOKEN_FILE.write_text(new_token + "\n")
                    token_info = get_token_info()
                    state.add_log(f"Token RENOVADO: {token_info['minutes_remaining']:.0f} min restantes", "success")
                    return True
                else:
                    state.add_log(f"Token extraído pero no es más nuevo (exp: {new_exp} vs {current_exp})", "warning")
                    return False

        state.add_log("No se pudo extraer token de localStorage", "error")
        return False

    except Exception as e:
        state.add_log(f"Error refrescando token: {e}", "error")
        return False


# =============================================================================
# Thread de monitoreo
# =============================================================================

def monitor_loop():
    """Loop de monitoreo que emite actualizaciones via WebSocket"""
    state.add_log("Monitor iniciado", "info")

    while not state.stop_monitor:
        try:
            progress = get_progress()
            token_info = get_token_info()
            speed = calculate_speed()

            # Leer progreso del log del proceso de subida (mas confiable y en tiempo real)
            log_progress = get_upload_log_progress()

            # Si hay un proceso corriendo, usar el progreso del log que es mas actualizado
            if log_progress["uploaded"] > 0:
                # Detectar archivos nuevos basandose en el conteo del log
                if state.last_log_uploaded == 0:
                    # Primera vez que detectamos progreso - registrar inicio
                    state.add_log(f"Detectado proceso de subida: {log_progress['uploaded']:,}/{log_progress.get('total', 92579):,} archivos", "info")
                    state.last_log_uploaded = log_progress["uploaded"]
                elif log_progress["uploaded"] < state.last_log_uploaded:
                    # El proceso se reinicio - el log empezo de nuevo
                    state.add_log(f"Proceso reiniciado - ahora en: {log_progress['uploaded']:,}/{log_progress.get('total', 92579):,}", "info")
                    state.last_log_uploaded = log_progress["uploaded"]
                elif log_progress["uploaded"] > state.last_log_uploaded:
                    new_count = log_progress["uploaded"] - state.last_log_uploaded
                    # Generar logs agrupados para no floodear
                    if new_count <= 5:
                        for i in range(new_count):
                            state.add_log(f"Archivo #{log_progress['uploaded'] - new_count + i + 1} subido", "upload")
                    else:
                        state.add_log(f"+{new_count} archivos subidos ({log_progress['uploaded']:,}/{log_progress.get('total', 92579):,})", "upload")
                    # Actualizar conteo para el proximo ciclo
                    state.last_log_uploaded = log_progress["uploaded"]

                # Usar la velocidad del log si esta disponible
                if log_progress["speed"] > 0:
                    speed = log_progress["speed"]

            # Detectar nuevos errores del JSON (esto aun funciona del JSON)
            current_errors = progress.get("errors_set", set())
            new_errors = current_errors - state.known_errors
            if new_errors and state.known_errors:  # Solo si ya teniamos datos previos
                for f in list(new_errors):  # Mostrar todos los nuevos errores
                    filename = f.split("/")[-1] if "/" in f else f
                    state.add_log(f"Error: {filename}", "file_error")
            state.known_errors = current_errors.copy()

            # Usar el mayor entre progreso JSON y log
            actual_uploaded = max(progress["uploaded"], log_progress.get("uploaded", 0))
            if log_progress["uploaded"] > progress["uploaded"]:
                progress["uploaded"] = log_progress["uploaded"]
                progress["percentage"] = log_progress.get("percentage", progress["percentage"])

            # Calcular ETA
            remaining_files = progress["total"] - actual_uploaded
            eta_minutes = remaining_files / speed if speed > 0 else 0

            # Guardar en historial
            history_entry = {
                "timestamp": datetime.now().isoformat(),
                "uploaded": progress["uploaded"],
                "speed": speed,
                "errors": progress["errors"]
            }
            state.history.append(history_entry)
            state.history = state.history[-180:]  # mantener ultimos 30 min

            # Detectar si hay proceso externo corriendo
            external_process = False
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'subir_paralelo.py'],
                    capture_output=True, text=True
                )
                external_process = result.returncode == 0 and bool(result.stdout.strip())
            except:
                pass

            # Construir status (excluir sets que no son JSON serializable)
            progress_for_client = {k: v for k, v in progress.items() if not k.endswith('_set')}
            status = {
                "progress": progress_for_client,
                "token": token_info,
                "speed": speed,
                "eta_minutes": round(eta_minutes, 0),
                "is_running": state.is_running or external_process,
                "external_process": external_process,
                "threads": state.config["threads"],
                "chrome_cdp": check_chrome_cdp(),
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }

            # Emitir actualizacion
            socketio.emit('status_update', status)

            # Auto-refresh token si esta por expirar (con cooldown de 2 minutos)
            REFRESH_COOLDOWN = 120  # 2 minutos entre intentos
            if state.config["auto_refresh_token"] and token_info["minutes_remaining"] < 10:
                time_since_last = time.time() - state.last_refresh_attempt
                if time_since_last >= REFRESH_COOLDOWN and token_info["minutes_remaining"] > 0:
                    state.add_log(f"Token por expirar ({token_info['minutes_remaining']:.0f} min), refrescando...", "warning")
                    state.last_refresh_attempt = time.time()
                    refresh_token_cdp()

            # Guardar historial cada minuto
            if len(state.history) % 6 == 0:
                state.save_history()

            # Verificar si el proceso sigue corriendo
            if state.upload_process and state.is_running:
                poll = state.upload_process.poll()
                if poll is not None:
                    state.is_running = False
                    if progress["uploaded"] >= progress["total"]:
                        state.add_log("Subida completada!", "success")
                        socketio.emit('upload_complete', {"uploaded": progress["uploaded"]})
                    else:
                        state.add_log(f"Proceso terminado (codigo: {poll})", "warning")

            time.sleep(1)  # Actualizar cada segundo para logs mas rapidos

        except Exception as e:
            logger.error(f"Error en monitor: {e}")
            time.sleep(2)

    state.add_log("Monitor detenido", "info")


def start_monitor():
    """Iniciar thread de monitoreo"""
    if state.monitor_thread and state.monitor_thread.is_alive():
        return

    state.stop_monitor = False
    state.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    state.monitor_thread.start()


def stop_monitor():
    """Detener thread de monitoreo"""
    state.stop_monitor = True
    if state.monitor_thread:
        state.monitor_thread.join(timeout=5)


# =============================================================================
# API REST
# =============================================================================

@app.route('/')
def index():
    """Pagina principal"""
    return render_template('dashboard.html')


@app.route('/api/status')
def api_status():
    """Obtener estado actual"""
    progress = get_progress()
    progress_for_client = {k: v for k, v in progress.items() if not k.endswith('_set')}
    token_info = get_token_info()
    speed = calculate_speed()

    # Obtener progreso del log de upload (mas actualizado que el JSON)
    log_progress = get_upload_log_progress()

    # Si hay datos en el log, usarlos (son mas recientes)
    if log_progress["uploaded"] > 0:
        if log_progress["uploaded"] > progress_for_client["uploaded"]:
            progress_for_client["uploaded"] = log_progress["uploaded"]
            progress_for_client["percentage"] = log_progress.get("percentage", progress_for_client["percentage"])
        if log_progress["speed"] > 0:
            speed = log_progress["speed"]

    # Detectar si hay proceso externo corriendo
    external_running = False
    try:
        result = subprocess.run(['pgrep', '-f', 'subir_paralelo.py'], capture_output=True, text=True)
        external_running = result.returncode == 0 and bool(result.stdout.strip())
    except:
        pass

    is_running = state.is_running or external_running

    remaining_files = progress_for_client["total"] - progress_for_client["uploaded"]
    eta_minutes = remaining_files / speed if speed > 0 else 0

    return jsonify({
        "progress": progress_for_client,
        "token": token_info,
        "speed": speed,
        "eta_minutes": round(eta_minutes, 0),
        "is_running": is_running,
        "threads": state.config["threads"],
        "chrome_cdp": check_chrome_cdp(),
        "config": CONFIG
    })


@app.route('/api/start', methods=['POST'])
def api_start():
    """Iniciar subida"""
    if state.is_running:
        return jsonify({"success": False, "error": "Ya hay una subida en progreso"})

    data = request.get_json() or {}
    threads = data.get('threads', state.config["threads"])
    threads = max(1, min(10, int(threads)))
    state.config["threads"] = threads

    token_info = get_token_info()
    if not token_info["valid"]:
        return jsonify({"success": False, "error": "Token invalido o expirado"})

    try:
        # Iniciar proceso de subida
        script_path = BASE_DIR / "subir_paralelo.py"

        state.upload_process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(BASE_DIR),
            text=True,
            bufsize=1
        )

        # Enviar numero de hilos
        state.upload_process.stdin.write(f"{threads}\n")
        state.upload_process.stdin.flush()

        state.is_running = True
        state.add_log(f"Subida iniciada con {threads} hilos", "success")

        return jsonify({"success": True, "threads": threads})

    except Exception as e:
        state.add_log(f"Error iniciando subida: {e}", "error")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/stop', methods=['POST'])
def api_stop():
    """Detener subida"""
    if not state.is_running or not state.upload_process:
        return jsonify({"success": False, "error": "No hay subida en progreso"})

    try:
        state.upload_process.terminate()
        state.upload_process.wait(timeout=10)
        state.is_running = False
        state.upload_process = None
        state.add_log("Subida detenida por el usuario", "warning")
        return jsonify({"success": True})
    except Exception as e:
        state.add_log(f"Error deteniendo subida: {e}", "error")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/logs')
def api_logs():
    """Obtener logs"""
    return jsonify({"logs": list(state.logs)})


@app.route('/api/errors')
def api_errors():
    """Obtener lista de errores"""
    progress = get_progress()
    return jsonify({"errors": progress["error_list"]})


@app.route('/api/history')
def api_history():
    """Obtener datos historicos para graficos"""
    # Agrupar por hora para grafico de barras
    hourly = {}
    for entry in state.history:
        try:
            dt = datetime.fromisoformat(entry["timestamp"])
            hour_key = dt.strftime("%H:00")
            if hour_key not in hourly:
                hourly[hour_key] = {"start": entry["uploaded"], "end": entry["uploaded"]}
            hourly[hour_key]["end"] = entry["uploaded"]
        except:
            pass

    hourly_data = []
    for hour, data in sorted(hourly.items()):
        hourly_data.append({
            "hour": hour,
            "count": max(0, data["end"] - data["start"])
        })

    return jsonify({
        "timeline": state.history[-90:],  # ultimos 15 min
        "hourly": hourly_data[-12:]  # ultimas 12 horas
    })


@app.route('/api/token/refresh', methods=['POST'])
def api_token_refresh():
    """Forzar refresh de token"""
    state.add_log("Refresh de token solicitado", "info")
    success = refresh_token_cdp()
    token_info = get_token_info()
    return jsonify({
        "success": success,
        "token": token_info
    })


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """Obtener/actualizar configuracion"""
    if request.method == 'POST':
        data = request.get_json() or {}
        if 'threads' in data:
            state.config["threads"] = max(1, min(10, int(data["threads"])))
        if 'auto_refresh_token' in data:
            state.config["auto_refresh_token"] = bool(data["auto_refresh_token"])
        state.add_log(f"Configuracion actualizada: {state.config}", "info")

    return jsonify({"config": state.config})


def is_upload_process_running() -> bool:
    """Verificar si hay un proceso de subida corriendo (interno o externo)"""
    # Verificar proceso interno del dashboard
    if state.is_running and state.upload_process:
        if state.upload_process.poll() is None:
            return True

    # Verificar procesos externos (subir_paralelo.py lanzado desde terminal)
    try:
        import subprocess
        result = subprocess.run(
            ['pgrep', '-f', 'subir_paralelo.py'],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    except:
        pass

    return False


@app.route('/api/retry-errors', methods=['POST'])
def api_retry_errors():
    """Limpiar errores para reintentar"""
    # IMPORTANTE: No modificar JSON si hay un proceso de subida activo
    if is_upload_process_running():
        return jsonify({
            "success": False,
            "error": "No se puede reintentar mientras hay una subida activa. Detén el proceso primero."
        })

    if not PROGRESS_FILE.exists():
        return jsonify({"success": False, "error": "No hay archivo de progreso"})

    try:
        with open(PROGRESS_FILE) as f:
            data = json.load(f)

        error_files = [e["file"] for e in data.get("errors", []) if "file" in e]
        data["errors"] = []

        with open(PROGRESS_FILE, 'w') as f:
            json.dump(data, f)

        state.add_log(f"Errores limpiados: {len(error_files)} archivos para reintentar", "info")
        return jsonify({"success": True, "cleared": len(error_files)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# =============================================================================
# API REST - Explorador SharePoint
# =============================================================================

@app.route('/api/explorer/sites')
def api_explorer_sites():
    """Obtener lista de sitios disponibles"""
    sites = []
    for key, site in SHAREPOINT_SITES.items():
        sites.append({
            "key": key,
            "name": site["name"],
            "root_folder": site["root_folder"]
        })
    return jsonify({"sites": sites})


@app.route('/api/explorer/browse')
def api_explorer_browse():
    """Explorar una carpeta de SharePoint"""
    site_key = request.args.get('site', 'personal')
    folder_path = request.args.get('path', '')

    # Si no se especifica path, usar la carpeta raíz del sitio
    if not folder_path and site_key in SHAREPOINT_SITES:
        folder_path = SHAREPOINT_SITES[site_key]["root_folder"]

    if not folder_path:
        return jsonify({"error": "Carpeta no especificada"})

    result = explore_sharepoint_folder(site_key, folder_path)
    return jsonify(result)


@app.route('/api/explorer/download')
def api_explorer_download():
    """Obtener URL de descarga de un archivo"""
    import requests as req

    site_key = request.args.get('site', 'personal')
    file_path = request.args.get('path', '')

    if not file_path:
        return jsonify({"error": "Ruta del archivo no especificada"})

    if site_key not in SHAREPOINT_SITES:
        return jsonify({"error": f"Sitio no encontrado: {site_key}"})

    # Obtener token
    if not TOKEN_FILE.exists():
        return jsonify({"error": "Token no disponible"})

    token = TOKEN_FILE.read_text().strip()

    # Construir URL de descarga
    download_url = get_sharepoint_file_url(site_key, file_path)
    if not download_url:
        return jsonify({"error": "No se pudo generar URL de descarga"})

    # Hacer la descarga y pasarla al cliente
    headers = {
        "Authorization": f"Bearer {token}"
    }

    try:
        r = req.get(download_url, headers=headers, stream=True, timeout=60)
        if r.status_code == 200:
            from flask import Response
            filename = file_path.split('/')[-1]

            def generate():
                for chunk in r.iter_content(chunk_size=8192):
                    yield chunk

            return Response(
                generate(),
                mimetype='application/octet-stream',
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Content-Length': r.headers.get('content-length', '')
                }
            )
        elif r.status_code == 401:
            return jsonify({"error": "Token expirado"})
        else:
            return jsonify({"error": f"Error {r.status_code}"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/explorer/search')
def api_explorer_search():
    """Buscar en el mapa cacheado de un sitio"""
    site_key = request.args.get('site', 'personal')
    term = request.args.get('q', '').lower()

    if not term:
        return jsonify({"error": "Término de búsqueda no especificado"})

    if site_key not in SHAREPOINT_SITES:
        return jsonify({"error": f"Sitio no encontrado: {site_key}"})

    cache_file = BASE_DIR / SHAREPOINT_SITES[site_key]["cache_file"]

    if not cache_file.exists():
        return jsonify({"error": "No hay mapa cacheado. Usa el explorador primero."})

    try:
        with open(cache_file) as f:
            mapa = json.load(f)

        resultados = []

        def buscar_recursivo(data):
            if not data:
                return
            # Buscar en archivos
            for f in data.get("files", []):
                if term in f["name"].lower():
                    resultados.append({
                        "tipo": "archivo",
                        "nombre": f["name"],
                        "ruta": f["path"],
                        "tamano": f.get("size", 0)
                    })
            # Buscar en carpetas
            for folder in data.get("folders", []):
                if term in folder["name"].lower():
                    resultados.append({
                        "tipo": "carpeta",
                        "nombre": folder["name"],
                        "ruta": folder["path"]
                    })
                if folder.get("contenido"):
                    buscar_recursivo(folder["contenido"])

        for folder_data in mapa.get("folders", {}).values():
            buscar_recursivo(folder_data)

        return jsonify({
            "term": term,
            "site": site_key,
            "count": len(resultados),
            "results": resultados[:50]  # Limitar a 50 resultados
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/explorer/details')
def api_explorer_details():
    """Obtener detalles de un archivo o carpeta"""
    site_key = request.args.get('site', 'personal')
    path = request.args.get('path', '')
    item_type = request.args.get('type', 'file')  # 'file' o 'folder'

    if not path:
        return jsonify({"error": "Ruta no especificada"})

    if item_type == 'folder':
        result = get_folder_details(site_key, path)
    else:
        result = get_file_details(site_key, path)

    return jsonify(result)


# =============================================================================
# FUNCIONES DE DESCARGA PARALELA
# =============================================================================

def download_single_file(file_info: dict, folder_path: str, headers: dict, site_key: str) -> tuple:
    """Descargar un archivo individual (usado por ThreadPoolExecutor)"""
    import requests as req

    file_path = file_info['path']
    relative_path = file_path.replace(folder_path, '').lstrip('/')

    download_url = get_sharepoint_file_url(site_key, file_path)
    if not download_url:
        return (relative_path, None, "URL no disponible")

    try:
        r = req.get(download_url, headers=headers, timeout=120)
        if r.status_code == 200:
            return (relative_path, r.content, None)
        else:
            return (relative_path, None, f"HTTP {r.status_code}")
    except Exception as e:
        return (relative_path, None, str(e))


def download_file_chunked(download_url: str, headers: dict, file_size: int,
                          chunk_size: int = 10 * 1024 * 1024, max_workers: int = 4) -> bytes:
    """Descargar un archivo grande en chunks paralelos usando Range headers"""
    import requests as req

    # Calcular los rangos de bytes para cada chunk
    ranges = []
    for start in range(0, file_size, chunk_size):
        end = min(start + chunk_size - 1, file_size - 1)
        ranges.append((start, end))

    # Almacenar los chunks descargados
    chunks_data = {}

    def download_chunk(range_tuple):
        start, end = range_tuple
        chunk_headers = headers.copy()
        chunk_headers['Range'] = f'bytes={start}-{end}'

        try:
            r = req.get(download_url, headers=chunk_headers, timeout=300)
            if r.status_code in (200, 206):  # 206 = Partial Content
                return (start, r.content, None)
            else:
                return (start, None, f"HTTP {r.status_code}")
        except Exception as e:
            return (start, None, str(e))

    # Descargar chunks en paralelo
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(download_chunk, r): r for r in ranges}

        for future in as_completed(futures):
            start, content, error = future.result()
            if content:
                chunks_data[start] = content

    # Ensamblar el archivo en orden
    if len(chunks_data) != len(ranges):
        raise Exception(f"Solo se descargaron {len(chunks_data)}/{len(ranges)} chunks")

    result = bytearray()
    for start, _ in sorted(ranges):
        if start in chunks_data:
            result.extend(chunks_data[start])

    return bytes(result)


def download_files_parallel(files: list, folder_path: str, headers: dict,
                           site_key: str, max_workers: int = 4) -> dict:
    """Descargar multiples archivos en paralelo"""
    results = {
        "downloaded": [],
        "failed": [],
        "data": {}  # relative_path -> bytes
    }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(download_single_file, f, folder_path, headers, site_key): f
            for f in files
        }

        for future in as_completed(futures):
            file_info = futures[future]
            relative_path, content, error = future.result()

            if content:
                results["downloaded"].append(relative_path)
                results["data"][relative_path] = content
            else:
                results["failed"].append({
                    "path": relative_path,
                    "error": error
                })

    return results


@app.route('/api/explorer/download-folder')
def api_explorer_download_folder():
    """Descargar una carpeta completa como ZIP (con descarga paralela)"""
    import zipfile
    import io

    site_key = request.args.get('site', 'personal')
    folder_path = request.args.get('path', '')
    threads = int(request.args.get('threads', 4))  # Hilos para descarga paralela (1-8)
    threads = max(1, min(8, threads))  # Limitar entre 1 y 8

    if not folder_path:
        return jsonify({"error": "Ruta de carpeta no especificada"})

    if site_key not in SHAREPOINT_SITES:
        return jsonify({"error": f"Sitio no encontrado: {site_key}"})

    if not TOKEN_FILE.exists():
        return jsonify({"error": "Token no disponible"})

    token = TOKEN_FILE.read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}

    # Listar todos los archivos recursivamente
    files = list_folder_files_recursive(site_key, folder_path, max_files=200)

    if not files:
        return jsonify({"error": "No se encontraron archivos o la carpeta no existe"})

    folder_name = folder_path.split('/')[-1] or "download"

    try:
        # Descargar archivos en paralelo
        logger.info(f"Descargando {len(files)} archivos con {threads} hilos...")
        download_results = download_files_parallel(
            files, folder_path, headers, site_key, max_workers=threads
        )

        # Crear ZIP en memoria con los archivos descargados
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for relative_path, content in download_results["data"].items():
                zf.writestr(relative_path, content)

        zip_buffer.seek(0)

        # Log del resultado
        success_count = len(download_results["downloaded"])
        fail_count = len(download_results["failed"])
        logger.info(f"ZIP creado: {success_count} archivos OK, {fail_count} fallidos")

        from flask import Response
        return Response(
            zip_buffer.getvalue(),
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="{folder_name}.zip"',
                'Content-Length': len(zip_buffer.getvalue())
            }
        )

    except Exception as e:
        logger.error(f"Error en descarga de carpeta: {e}")
        return jsonify({"error": str(e)})


@app.route('/api/explorer/folder-contents')
def api_explorer_folder_contents():
    """Obtener lista de archivos de una carpeta para descarga"""
    site_key = request.args.get('site', 'personal')
    folder_path = request.args.get('path', '')
    max_files = int(request.args.get('max', 200))

    if not folder_path:
        return jsonify({"error": "Ruta de carpeta no especificada"})

    files = list_folder_files_recursive(site_key, folder_path, max_files=max_files)
    total_size = sum(f.get('size', 0) for f in files)

    return jsonify({
        "path": folder_path,
        "site": site_key,
        "file_count": len(files),
        "total_size": total_size,
        "total_size_formatted": format_file_size(total_size),
        "files": files
    })


@app.route('/api/explorer/download-file-chunked')
def api_explorer_download_file_chunked():
    """Descargar un archivo grande usando chunks paralelos"""
    import requests as req

    site_key = request.args.get('site', 'personal')
    file_path = request.args.get('path', '')
    threads = int(request.args.get('threads', 4))  # Hilos para chunks (1-8)
    threads = max(1, min(8, threads))
    chunk_size = int(request.args.get('chunk_size', 10 * 1024 * 1024))  # 10MB default

    if not file_path:
        return jsonify({"error": "Ruta de archivo no especificada"})

    if site_key not in SHAREPOINT_SITES:
        return jsonify({"error": f"Sitio no encontrado: {site_key}"})

    if not TOKEN_FILE.exists():
        return jsonify({"error": "Token no disponible"})

    token = TOKEN_FILE.read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}

    # Obtener URL de descarga
    download_url = get_sharepoint_file_url(site_key, file_path)
    if not download_url:
        return jsonify({"error": "No se pudo obtener URL de descarga"})

    try:
        # Primero obtener el tamano del archivo
        head_response = req.head(download_url, headers=headers, timeout=30)
        if head_response.status_code != 200:
            # Fallback: descargar completo si HEAD no funciona
            logger.info(f"HEAD no disponible, descargando archivo completo...")
            r = req.get(download_url, headers=headers, timeout=300)
            if r.status_code == 200:
                file_name = file_path.split('/')[-1]
                from flask import Response
                return Response(
                    r.content,
                    mimetype='application/octet-stream',
                    headers={
                        'Content-Disposition': f'attachment; filename="{file_name}"',
                        'Content-Length': len(r.content)
                    }
                )
            else:
                return jsonify({"error": f"Error descargando archivo: HTTP {r.status_code}"})

        file_size = int(head_response.headers.get('Content-Length', 0))
        file_name = file_path.split('/')[-1]

        # Si el archivo es pequeno (<50MB), descargar completo
        if file_size < 50 * 1024 * 1024:
            logger.info(f"Archivo pequeno ({format_file_size(file_size)}), descarga directa...")
            r = req.get(download_url, headers=headers, timeout=300)
            if r.status_code == 200:
                from flask import Response
                return Response(
                    r.content,
                    mimetype='application/octet-stream',
                    headers={
                        'Content-Disposition': f'attachment; filename="{file_name}"',
                        'Content-Length': len(r.content)
                    }
                )
            else:
                return jsonify({"error": f"Error descargando archivo: HTTP {r.status_code}"})

        # Para archivos grandes, usar chunks paralelos
        logger.info(f"Descargando archivo grande ({format_file_size(file_size)}) con {threads} hilos...")
        content = download_file_chunked(download_url, headers, file_size,
                                        chunk_size=chunk_size, max_workers=threads)

        from flask import Response
        return Response(
            content,
            mimetype='application/octet-stream',
            headers={
                'Content-Disposition': f'attachment; filename="{file_name}"',
                'Content-Length': len(content)
            }
        )

    except Exception as e:
        logger.error(f"Error en descarga chunked: {e}")
        return jsonify({"error": str(e)})


# =============================================================================
# WebSocket events
# =============================================================================

@socketio.on('connect')
def handle_connect():
    """Cliente conectado"""
    state.add_log("Cliente conectado al dashboard", "info")
    # Enviar estado inicial con logs existentes
    progress = get_progress()
    progress_for_client = {k: v for k, v in progress.items() if not k.endswith('_set')}
    token_info = get_token_info()
    emit('status_update', {
        "progress": progress_for_client,
        "token": token_info,
        "speed": calculate_speed(),
        "is_running": state.is_running,
        "threads": state.config["threads"],
        "chrome_cdp": check_chrome_cdp(),
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "logs": list(state.logs)  # Enviar logs existentes al conectar
    })


@socketio.on('disconnect')
def handle_disconnect():
    """Cliente desconectado"""
    logger.info("Cliente desconectado")


@socketio.on('request_status')
def handle_request_status():
    """Cliente solicita estado"""
    progress = get_progress()
    progress_for_client = {k: v for k, v in progress.items() if not k.endswith('_set')}
    token_info = get_token_info()
    speed = calculate_speed()
    remaining_files = progress["total"] - progress["uploaded"]
    eta_minutes = remaining_files / speed if speed > 0 else 0

    emit('status_update', {
        "progress": progress_for_client,
        "token": token_info,
        "speed": speed,
        "eta_minutes": round(eta_minutes, 0),
        "is_running": state.is_running,
        "threads": state.config["threads"],
        "chrome_cdp": check_chrome_cdp(),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    })


# =============================================================================
# Cleanup y signal handlers
# =============================================================================

def cleanup():
    """Limpieza al salir"""
    logger.info("Limpiando...")
    stop_monitor()
    state.save_history()

    if state.upload_process:
        state.upload_process.terminate()
        try:
            state.upload_process.wait(timeout=5)
        except:
            state.upload_process.kill()


def signal_handler(sig, frame):
    """Manejador de senales"""
    logger.info(f"Senal {sig} recibida, saliendo...")
    cleanup()
    sys.exit(0)


# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    # Registrar signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Iniciar monitor
    start_monitor()

    print()
    print("=" * 60)
    print("  ONEDRIVE UPLOAD DASHBOARD")
    print("=" * 60)
    print()
    print(f"  URL: http://localhost:5000")
    print(f"  Total archivos: {CONFIG['total_files']:,}")
    print(f"  Chrome CDP: puerto {CONFIG['cdp_port']}")
    print()
    print("  Presiona Ctrl+C para detener")
    print("=" * 60)
    print()

    try:
        logger.info("Iniciando servidor Flask-SocketIO en puerto 5000...")
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
        logger.info("Servidor detenido")
    except Exception as e:
        logger.error(f"Error iniciando servidor: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()
