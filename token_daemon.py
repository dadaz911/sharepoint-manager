#!/usr/bin/env python3
"""
Daemon de Token para OneDrive - Refresco Automático

Este daemon monitorea el token y lo refresca automáticamente antes de que expire.
Usa Chrome DevTools Protocol para conectarse al navegador existente.

REQUISITOS:
1. Chrome debe estar corriendo con remote debugging habilitado:
   google-chrome --remote-debugging-port=9222

2. Debe haber una pestaña de OneDrive abierta en el navegador

USO:
   python3 token_daemon.py          # Ejecutar daemon
   python3 token_daemon.py --once   # Refrescar una vez y salir
   python3 token_daemon.py --status # Ver estado del token
"""

import os
import sys
import json
import time
import base64
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# Configuración. Los valores se leen del entorno (config.env, inyectado por la unit
# systemd vía EnvironmentFile) con defaults idénticos al comportamiento histórico,
# de modo que correrlo a mano sigue funcionando sin variables seteadas.
CONFIG = {
    "token_file": os.environ.get("TOKEN_FILE", "/home/daniel/Desktop/Cargue a Onedrive/.token"),
    "cdp_port": int(os.environ.get("CDP_PORT", "9222")),
    "check_interval": int(os.environ.get("CHECK_INTERVAL", "300")),  # Verificar cada 5 min
    "refresh_threshold": int(os.environ.get("REFRESH_THRESHOLD", "15")),  # Refrescar si <15 min
    "onedrive_url": os.environ.get("ONEDRIVE_URL", "https://shdgov-my.sharepoint.com"),
}

class TokenDaemon:
    def __init__(self):
        self.token_file = Path(CONFIG["token_file"])
        self.cdp_port = CONFIG["cdp_port"]

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

    def check_chrome_debugging(self) -> bool:
        """Verificar si Chrome está corriendo con remote debugging"""
        try:
            r = requests.get(f"http://localhost:{self.cdp_port}/json/version", timeout=2)
            return r.status_code == 200
        except:
            return False

    def get_onedrive_tab(self) -> Optional[dict]:
        """Encontrar la pestaña de OneDrive"""
        try:
            r = requests.get(f"http://localhost:{self.cdp_port}/json", timeout=5)
            tabs = r.json()
            for tab in tabs:
                if CONFIG["onedrive_url"] in tab.get("url", ""):
                    return tab
            return None
        except:
            return None

    def extract_token_from_tab(self, ws_url: str) -> Optional[str]:
        """Extraer token usando Chrome DevTools Protocol via websocket"""
        try:
            import websocket

            ws = websocket.create_connection(ws_url, timeout=10)

            # Ejecutar JavaScript para obtener el token
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

            msg = json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js_code,
                    "returnByValue": True
                }
            })

            ws.send(msg)
            response = json.loads(ws.recv())
            ws.close()

            if "result" in response and "result" in response["result"]:
                return response["result"]["result"].get("value")
            return None
        except Exception as e:
            print(f"Error extrayendo token: {e}")
            return None

    def refresh_token_cdp(self) -> bool:
        """Refrescar token usando CDP"""
        if not self.check_chrome_debugging():
            print("Chrome no está en modo debugging")
            return False

        tab = self.get_onedrive_tab()
        if not tab:
            print("No se encontró pestaña de OneDrive")
            return False

        # Primero recargar la página para obtener token fresco
        try:
            import websocket
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)

            # Recargar página
            ws.send(json.dumps({"id": 1, "method": "Page.reload"}))
            ws.recv()
            ws.close()

            print("Página recargada, esperando 5 segundos...")
            time.sleep(5)

        except Exception as e:
            print(f"Error recargando página: {e}")

        # Obtener nuevo websocket y extraer token
        tab = self.get_onedrive_tab()
        if not tab:
            return False

        token = self.extract_token_from_tab(tab["webSocketDebuggerUrl"])

        if token:
            self.token_file.write_text(token + "\n")
            os.chmod(self.token_file, 0o600)  # bearer token: no world-readable
            print(f"Token refrescado exitosamente")
            return True

        return False

    def refresh_token_selenium(self) -> bool:
        """Refrescar token usando Selenium (alternativa)"""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager

            print("Iniciando Selenium...")

            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument(f"--user-data-dir={Path.home()}/.config/google-chrome")
            options.add_argument("--profile-directory=Default")

            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)

            driver.get(CONFIG["onedrive_url"])
            time.sleep(5)

            # Extraer token
            token = driver.execute_script("""
                const keys = Object.keys(localStorage);
                const spKey = keys.find(k => k.includes('sharepoint_selfissued') && k.includes('shdgov-my.sharepoint.com'));
                if (spKey) {
                    const data = JSON.parse(localStorage.getItem(spKey));
                    return data.value;
                }
                return null;
            """)

            driver.quit()

            if token:
                self.token_file.write_text(token + "\n")
                os.chmod(self.token_file, 0o600)  # bearer token: no world-readable
                print("Token refrescado via Selenium")
                return True

            return False

        except Exception as e:
            print(f"Error con Selenium: {e}")
            return False

    def send_notification(self, message: str):
        """Enviar notificación al usuario"""
        try:
            subprocess.run([
                "notify-send",
                "OneDrive Token",
                message,
                "-u", "critical"
            ], check=False)
        except:
            pass

    def status(self):
        """Mostrar estado actual"""
        token, exp, remaining = self.get_token_info()

        print("=" * 50)
        print("  Estado del Token de OneDrive")
        print("=" * 50)

        if token:
            if exp:
                print(f"Token: Presente")
                print(f"Expira: {exp.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"Restante: {remaining:.0f} minutos")

                if remaining < CONFIG["refresh_threshold"]:
                    print(f"Estado: NECESITA REFRESH")
                else:
                    print(f"Estado: OK")
            else:
                print("Token: Presente pero no se puede decodificar")
        else:
            print("Token: NO ENCONTRADO")

        print()
        print(f"Chrome debugging: {'Activo' if self.check_chrome_debugging() else 'Inactivo'}")

        if self.check_chrome_debugging():
            tab = self.get_onedrive_tab()
            print(f"Pestaña OneDrive: {'Encontrada' if tab else 'No encontrada'}")

        print("=" * 50)

    def refresh_once(self) -> bool:
        """Intentar refrescar el token una vez"""
        print("Intentando refrescar token...")

        # Primero intentar con CDP
        if self.check_chrome_debugging():
            if self.refresh_token_cdp():
                return True

        # Si falla, intentar con Selenium
        print("CDP no disponible, intentando con Selenium...")
        return self.refresh_token_selenium()

    def run(self):
        """Ejecutar daemon"""
        print("=" * 50)
        print("  Token Daemon para OneDrive")
        print(f"  Verificando cada {CONFIG['check_interval']} segundos")
        print(f"  Refrescando cuando queden < {CONFIG['refresh_threshold']} min")
        print("=" * 50)
        print()

        while True:
            try:
                token, exp, remaining = self.get_token_info()
                now = datetime.now().strftime("%H:%M:%S")

                if remaining <= 0:
                    print(f"[{now}] Token EXPIRADO - Refrescando...")
                    if self.refresh_once():
                        self.send_notification("Token refrescado exitosamente")
                    else:
                        self.send_notification("ERROR: No se pudo refrescar el token")
                        print("ERROR: No se pudo refrescar el token")

                elif remaining < CONFIG["refresh_threshold"]:
                    print(f"[{now}] Token por expirar ({remaining:.0f} min) - Refrescando...")
                    if self.refresh_once():
                        self.send_notification("Token refrescado exitosamente")
                    else:
                        print("ERROR: No se pudo refrescar el token")

                else:
                    print(f"[{now}] Token OK - {remaining:.0f} min restantes")

                time.sleep(CONFIG["check_interval"])

            except KeyboardInterrupt:
                print("\nDaemon detenido")
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(60)


def main():
    daemon = TokenDaemon()

    if len(sys.argv) > 1:
        if sys.argv[1] == "--status":
            daemon.status()
        elif sys.argv[1] == "--once":
            if daemon.refresh_once():
                _, _, remaining = daemon.get_token_info()
                print(f"Token refrescado. Validez: {remaining:.0f} minutos")
            else:
                print("No se pudo refrescar el token")
                sys.exit(1)
        elif sys.argv[1] == "--help":
            print(__doc__)
        else:
            print(f"Opción desconocida: {sys.argv[1]}")
            print("Uso: python3 token_daemon.py [--status|--once|--help]")
    else:
        daemon.run()


if __name__ == "__main__":
    main()
