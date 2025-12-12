#!/usr/bin/env python3
"""
Auto Token Refresh - Sistema automático de refresh de token

CONFIGURACIÓN INICIAL (solo una vez):
1. Ejecuta: python3 auto_token_refresh.py --setup
2. Se abrirá Chrome - inicia sesión en OneDrive
3. Una vez logueado, presiona Enter en la terminal
4. Listo! El sistema funcionará automático

USO NORMAL:
   python3 auto_token_refresh.py    # Inicia el daemon

El daemon:
- Monitorea el token cada 2 minutos
- Refresca automáticamente cuando quedan < 10 minutos
- Envía notificaciones del sistema
- Funciona sin intervención
"""

import os
import sys
import json
import time
import base64
import signal
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# Configuración
CONFIG = {
    "token_file": "/home/daniel/Desktop/Cargue a Onedrive/.token",
    "progress_file": "/home/daniel/Desktop/Cargue a Onedrive/.upload_progress.json",
    "chrome_profile": "/home/daniel/.config/onedrive-uploader-chrome",
    "cdp_port": 9333,
    "onedrive_url": "https://shdgov-my.sharepoint.com",
    "refresh_threshold": 10,
    "check_interval": 120,
    "total_files": 92579,
}


class AutoTokenRefresh:
    def __init__(self):
        self.token_file = Path(CONFIG["token_file"])
        self.chrome_process = None
        self.running = True

    def get_token_info(self) -> Tuple[Optional[str], float]:
        """Obtener minutos restantes del token"""
        if not self.token_file.exists():
            return None, 0

        token = self.token_file.read_text().strip()
        if not token:
            return None, 0

        try:
            parts = token.split('.')
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            exp = datetime.fromtimestamp(int(data['exp']))
            remaining = (exp - datetime.now()).total_seconds() / 60
            return token, max(0, remaining)
        except:
            return token, 0

    def get_progress(self) -> Tuple[int, float]:
        """Obtener progreso de subida"""
        progress_file = Path(CONFIG["progress_file"])
        if not progress_file.exists():
            return 0, 0
        try:
            with open(progress_file) as f:
                data = json.load(f)
            uploaded = len(data.get("uploaded", []))
            pct = (uploaded / CONFIG["total_files"]) * 100
            return uploaded, pct
        except:
            return 0, 0

    def check_chrome_running(self) -> bool:
        """Verificar si Chrome dedicado está corriendo"""
        try:
            import requests
            r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json/version", timeout=2)
            return r.status_code == 200
        except:
            return False

    def start_chrome(self, headless: bool = True) -> bool:
        """Iniciar Chrome dedicado"""
        profile_dir = Path(CONFIG["chrome_profile"])
        profile_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "/usr/bin/google-chrome",
            f"--remote-debugging-port={CONFIG['cdp_port']}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-default-apps",
            "--disable-extensions",
        ]

        if headless:
            cmd.extend(["--headless=new", "--disable-gpu"])

        cmd.append(CONFIG["onedrive_url"])

        self.chrome_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Esperar a que inicie
        for _ in range(15):
            time.sleep(1)
            if self.check_chrome_running():
                return True

        return False

    def extract_token(self) -> Optional[str]:
        """Extraer token del Chrome dedicado"""
        if not self.check_chrome_running():
            return None

        try:
            import requests
            import websocket

            # Obtener lista de tabs
            r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json", timeout=5)
            tabs = r.json()

            # Buscar tab de OneDrive
            tab = None
            for t in tabs:
                if "sharepoint" in t.get("url", "").lower():
                    tab = t
                    break

            if not tab:
                # Si no hay tab de OneDrive, navegamos
                if tabs:
                    tab = tabs[0]
                    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
                    ws.send(json.dumps({
                        "id": 1,
                        "method": "Page.navigate",
                        "params": {"url": CONFIG["onedrive_url"]}
                    }))
                    ws.recv()
                    ws.close()
                    time.sleep(5)

                    # Obtener tabs de nuevo
                    r = requests.get(f"http://localhost:{CONFIG['cdp_port']}/json", timeout=5)
                    tabs = r.json()
                    tab = tabs[0] if tabs else None

            if not tab:
                return None

            # Conectar via WebSocket
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)

            # Recargar para obtener token fresco
            ws.send(json.dumps({"id": 1, "method": "Page.reload"}))
            ws.recv()
            time.sleep(5)

            # Extraer token
            js_code = """
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
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {"expression": js_code, "returnByValue": True}
            }))

            response = json.loads(ws.recv())
            ws.close()

            if "result" in response and "result" in response["result"]:
                return response["result"]["result"].get("value")

            return None

        except Exception as e:
            print(f"Error extrayendo token: {e}")
            return None

    def refresh_token(self) -> bool:
        """Refrescar el token"""
        # Asegurar que Chrome está corriendo
        if not self.check_chrome_running():
            print("Iniciando Chrome...")
            if not self.start_chrome(headless=True):
                print("ERROR: No se pudo iniciar Chrome")
                return False

        token = self.extract_token()

        if token and len(token) > 100:
            self.token_file.write_text(token + "\n")
            _, remaining = self.get_token_info()
            print(f"Token refrescado. Validez: {remaining:.0f} minutos")
            self.notify("Token refrescado", f"Validez: {remaining:.0f} minutos")
            return True

        print("No se pudo extraer el token")
        return False

    def notify(self, title: str, message: str):
        """Enviar notificación del sistema"""
        try:
            subprocess.run(
                ["notify-send", title, message, "-u", "normal"],
                capture_output=True,
                timeout=5
            )
        except:
            pass

    def setup(self):
        """Configuración inicial - requiere login manual"""
        print()
        print("=" * 60)
        print("  CONFIGURACIÓN INICIAL")
        print("=" * 60)
        print()
        print("Se abrirá Chrome para que inicies sesión en OneDrive.")
        print("Este login se guardará y se usará automáticamente.")
        print()

        # Iniciar Chrome visible
        print("Iniciando Chrome...")
        if not self.start_chrome(headless=False):
            print("ERROR: No se pudo iniciar Chrome")
            return False

        print()
        print("Chrome abierto. Por favor:")
        print("1. Inicia sesión en OneDrive")
        print("2. Espera a que cargue completamente")
        print("3. Presiona ENTER aquí cuando estés listo")
        print()

        input("Presiona ENTER cuando hayas iniciado sesión... ")

        # Intentar extraer token
        token = self.extract_token()

        if token and len(token) > 100:
            self.token_file.write_text(token + "\n")
            _, remaining = self.get_token_info()
            print()
            print(f"¡Configuración exitosa!")
            print(f"Token guardado. Validez: {remaining:.0f} minutos")
            print()
            print("Ahora puedes cerrar Chrome y ejecutar:")
            print("  python3 auto_token_refresh.py")
            print()
            return True
        else:
            print()
            print("ERROR: No se pudo extraer el token.")
            print("Asegúrate de haber iniciado sesión correctamente.")
            return False

    def run(self):
        """Ejecutar daemon de refresh automático"""
        print()
        print("=" * 60)
        print("  AUTO TOKEN REFRESH - DAEMON")
        print("=" * 60)
        print()

        # Verificar si hay token
        _, remaining = self.get_token_info()
        if remaining <= 0:
            print("Token expirado o no existe.")
            print("Intentando refrescar...")
            if not self.refresh_token():
                print()
                print("ERROR: No hay token válido.")
                print("Ejecuta primero: python3 auto_token_refresh.py --setup")
                return

        print(f"Token actual: {remaining:.0f} minutos restantes")
        print(f"Verificando cada {CONFIG['check_interval']} segundos")
        print(f"Refrescando cuando queden < {CONFIG['refresh_threshold']} minutos")
        print()
        print("Daemon activo. Ctrl+C para detener.")
        print()

        def signal_handler(sig, frame):
            self.running = False
            print("\nDeteniendo daemon...")

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        while self.running:
            try:
                _, remaining = self.get_token_info()
                uploaded, pct = self.get_progress()
                now = datetime.now().strftime("%H:%M:%S")

                status = f"[{now}] Token: {remaining:.0f}min | Subidos: {uploaded:,} ({pct:.1f}%)"

                if remaining <= 0:
                    print(f"\n{status} - TOKEN EXPIRADO")
                    print("Refrescando...")
                    self.refresh_token()
                elif remaining < CONFIG["refresh_threshold"]:
                    print(f"\n{status} - Refrescando preventivamente...")
                    self.refresh_token()
                else:
                    print(f"\r{status}                    ", end="", flush=True)

                time.sleep(CONFIG["check_interval"])

            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(60)

        # Cleanup
        if self.chrome_process:
            self.chrome_process.terminate()

        print("Daemon detenido.")


def main():
    daemon = AutoTokenRefresh()

    if len(sys.argv) > 1:
        if sys.argv[1] == "--setup":
            daemon.setup()
        elif sys.argv[1] == "--refresh":
            if daemon.refresh_token():
                print("Token refrescado exitosamente")
            else:
                print("No se pudo refrescar el token")
                sys.exit(1)
        elif sys.argv[1] == "--status":
            _, remaining = daemon.get_token_info()
            uploaded, pct = daemon.get_progress()
            print(f"Token: {remaining:.0f} minutos restantes")
            print(f"Subidos: {uploaded:,} / {CONFIG['total_files']:,} ({pct:.1f}%)")
        else:
            print(__doc__)
    else:
        daemon.run()


if __name__ == "__main__":
    main()
