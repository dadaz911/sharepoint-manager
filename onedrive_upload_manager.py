#!/usr/bin/env python3
"""
OneDrive Upload Manager - Script unificado para subida masiva

Este script maneja todo el proceso:
1. Verifica/inicia Chrome con remote debugging
2. Monitorea y refresca el token automáticamente
3. Ejecuta la subida de archivos

USO:
   python3 onedrive_upload_manager.py        # Ejecutar todo
   python3 onedrive_upload_manager.py status # Ver estado
"""

import base64
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import requests

# Configuración
CONFIG = {
    "base_dir": "/home/daniel/Desktop/Cargue a Onedrive",
    "token_file": "/home/daniel/Desktop/Cargue a Onedrive/.token",
    "progress_file": "/home/daniel/Desktop/Cargue a Onedrive/.upload_progress.json",
    "cdp_port": 9222,
    "onedrive_url": "https://shdgov-my.sharepoint.com",
    "refresh_threshold": 10,  # Refrescar cuando queden menos de 10 minutos
    "check_interval": 120,  # Verificar cada 2 minutos
    "total_files": 92579,
}


class OneDriveManager:
    def __init__(self):
        self.token_file = Path(CONFIG["token_file"])
        self.progress_file = Path(CONFIG["progress_file"])
        self.running = True
        self.upload_process = None

    def get_token_info(self) -> Tuple[Optional[str], Optional[datetime], float]:
        """Obtener información del token actual"""
        if not self.token_file.exists():
            return None, None, 0

        token = self.token_file.read_text().strip()
        if not token:
            return None, None, 0

        try:
            parts = token.split('.')
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            exp = datetime.fromtimestamp(int(data['exp']))
            remaining = (exp - datetime.now()).total_seconds() / 60
            return token, exp, max(0, remaining)
        except:
            return token, None, 0

    def get_progress(self) -> Tuple[int, int, float]:
        """Obtener progreso de subida"""
        if not self.progress_file.exists():
            return 0, 0, 0

        try:
            with open(self.progress_file) as f:
                data = json.load(f)
            uploaded = len(data.get("uploaded", []))
            errors = len(data.get("errors", []))
            pct = (uploaded / CONFIG["total_files"]) * 100
            return uploaded, errors, pct
        except:
            return 0, 0, 0

    def check_chrome_debugging(self) -> bool:
        """Verificar si Chrome está corriendo con remote debugging"""
        try:
            r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json/version", timeout=2)
            return r.status_code == 200
        except:
            return False

    def start_chrome_with_debugging(self) -> bool:
        """Iniciar Chrome con remote debugging"""
        if self.check_chrome_debugging():
            print("Chrome ya está en modo debugging")
            return True

        print("Iniciando Chrome con remote debugging...")

        # Cerrar Chrome existente
        subprocess.run(["pkill", "-x", "chrome"], capture_output=True)
        time.sleep(2)

        # Iniciar Chrome
        cmd = [
            "/usr/bin/google-chrome",
            f"--remote-debugging-port={CONFIG['cdp_port']}",
            f"--user-data-dir={Path.home()}/.config/google-chrome",
            "--profile-directory=Default",
            CONFIG["onedrive_url"],
        ]

        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Esperar a que inicie
        for _ in range(10):
            time.sleep(1)
            if self.check_chrome_debugging():
                print("Chrome iniciado correctamente")
                return True

        print("ERROR: No se pudo iniciar Chrome")
        return False

    def get_onedrive_tab(self) -> Optional[dict]:
        """Encontrar la pestaña de OneDrive"""
        try:
            r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json", timeout=5)
            tabs = r.json()
            for tab in tabs:
                if CONFIG["onedrive_url"].split("//")[1].split("/")[0] in tab.get("url", ""):
                    return tab
            return None
        except:
            return None

    def refresh_token(self) -> bool:
        """Refrescar el token desde Chrome"""
        if not self.check_chrome_debugging():
            print("Chrome no disponible para refresh")
            return False

        tab = self.get_onedrive_tab()
        if not tab:
            print("No se encontró pestaña de OneDrive")
            return False

        try:
            import websocket

            ws_url = tab["webSocketDebuggerUrl"]
            ws = websocket.create_connection(ws_url, timeout=15)

            # Recargar página
            ws.send(json.dumps({"id": 1, "method": "Page.reload"}))
            ws.recv()
            time.sleep(5)

            # Extraer token
            js_code = """
            (function() {
                const keys = Object.keys(localStorage);
                const spKey = keys.find(k => k.includes('sharepoint_selfissued') && k.includes('shdgov-my.sharepoint.com'));
                if (spKey) {
                    const data = JSON.parse(localStorage.getItem(spKey));
                    return data.value;
                }
                return null;
            })()
            """

            ws.send(
                json.dumps(
                    {"id": 2, "method": "Runtime.evaluate", "params": {"expression": js_code, "returnByValue": True}}
                )
            )

            response = json.loads(ws.recv())
            ws.close()

            if "result" in response and "result" in response["result"]:
                token = response["result"]["result"].get("value")
                if token:
                    self.token_file.write_text(token + "\n")
                    _, _, remaining = self.get_token_info()
                    print(f"Token refrescado. Validez: {remaining:.0f} minutos")
                    return True

            return False

        except ImportError:
            print("Instalando websocket-client...")
            subprocess.run([sys.executable, "-m", "pip", "install", "websocket-client", "-q"])
            return self.refresh_token()
        except Exception as e:
            print(f"Error refrescando token: {e}")
            return False

    def token_monitor_thread(self):
        """Thread que monitorea y refresca el token"""
        while self.running:
            try:
                _, _, remaining = self.get_token_info()

                if remaining <= 0:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Token EXPIRADO - Refrescando...")
                    self.refresh_token()
                elif remaining < CONFIG["refresh_threshold"]:
                    print(
                        f"\n[{datetime.now().strftime('%H:%M:%S')}] Token por expirar ({remaining:.0f} min) - Refrescando..."
                    )
                    self.refresh_token()

                time.sleep(CONFIG["check_interval"])

            except Exception as e:
                print(f"Error en monitor: {e}")
                time.sleep(60)

    def start_upload(self):
        """Iniciar proceso de subida"""
        upload_script = Path(CONFIG["base_dir"]) / "subir_onedrive_auto.py"

        if not upload_script.exists():
            print(f"ERROR: No se encontró {upload_script}")
            return None

        print("Iniciando subida de archivos...")
        self.upload_process = subprocess.Popen([sys.executable, str(upload_script)], cwd=CONFIG["base_dir"])
        return self.upload_process

    def status(self):
        """Mostrar estado completo"""
        token, exp, remaining = self.get_token_info()
        uploaded, errors, pct = self.get_progress()

        print()
        print("=" * 60)
        print("  ESTADO DE SUBIDA A ONEDRIVE")
        print("=" * 60)
        print()

        # Progreso
        print(f"  Archivos subidos: {uploaded:,} / {CONFIG['total_files']:,} ({pct:.1f}%)")
        print(f"  Restantes: {CONFIG['total_files'] - uploaded:,}")
        print(f"  Errores: {errors}")
        print()

        # Token
        if remaining > 0:
            print(f"  Token: VALIDO ({remaining:.0f} minutos restantes)")
            if exp:
                print(f"  Expira: {exp.strftime('%H:%M:%S')}")
        else:
            print("  Token: EXPIRADO")
        print()

        # Chrome
        chrome_status = "Activo" if self.check_chrome_debugging() else "Inactivo"
        print(f"  Chrome debugging: {chrome_status}")

        if self.check_chrome_debugging():
            tab = self.get_onedrive_tab()
            print(f"  Pestaña OneDrive: {'Encontrada' if tab else 'No encontrada'}")

        print()
        print("=" * 60)

    def run(self):
        """Ejecutar manager completo"""
        print()
        print("=" * 60)
        print("  ONEDRIVE UPLOAD MANAGER")
        print("  Subida automática con refresh de token")
        print("=" * 60)
        print()

        # Verificar/iniciar Chrome
        if not self.start_chrome_with_debugging():
            print("No se pudo iniciar Chrome. Continuando sin auto-refresh...")

        # Verificar token inicial
        _, _, remaining = self.get_token_info()
        if remaining <= 0:
            print("Token expirado. Intentando refrescar...")
            if not self.refresh_token():
                print("ERROR: No se pudo obtener token válido")
                print("Por favor, abre OneDrive en Chrome y vuelve a ejecutar")
                return

        # Iniciar thread de monitoreo de token
        monitor_thread = threading.Thread(target=self.token_monitor_thread, daemon=True)
        monitor_thread.start()
        print("Monitor de token iniciado")

        # Iniciar subida
        self.start_upload()

        # Esperar
        try:
            while self.running:
                if self.upload_process and self.upload_process.poll() is not None:
                    print("\nProceso de subida terminado")
                    break

                uploaded, errors, pct = self.get_progress()
                _, _, remaining = self.get_token_info()

                status = f"Subidos: {uploaded:,} ({pct:.1f}%) | Token: {remaining:.0f}min"
                print(f"\r{status}          ", end="", flush=True)

                time.sleep(30)

        except KeyboardInterrupt:
            print("\n\nDeteniendo...")
            self.running = False
            if self.upload_process:
                self.upload_process.terminate()

        self.status()


def main():
    manager = OneDriveManager()

    if len(sys.argv) > 1:
        if sys.argv[1] == "status":
            manager.status()
        elif sys.argv[1] == "refresh":
            if manager.refresh_token():
                print("Token refrescado exitosamente")
            else:
                print("No se pudo refrescar el token")
        elif sys.argv[1] == "help":
            print(__doc__)
        else:
            print(f"Opción desconocida: {sys.argv[1]}")
            print("Uso: python3 onedrive_upload_manager.py [status|refresh|help]")
    else:
        manager.run()


if __name__ == "__main__":
    main()
