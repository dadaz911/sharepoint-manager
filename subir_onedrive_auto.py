#!/usr/bin/env python3
"""
Script de subida automática a OneDrive con manejo de token.
Se pausa cuando el token expira y espera uno nuevo.
"""

import os
import sys
import json
import time
import urllib.parse
import requests
import base64
from pathlib import Path
from datetime import datetime

CONFIG = {
    "base_url": "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web",
    "dest_folder": "/personal/dzuniga_shd_gov_co1/Documents/Pruebas",
    "source_dir": "/home/daniel/Desktop/Cargue a Onedrive",
    "extensions": [".pdf", ".PDF"],
    "exclude_dirs": [".claude", "__pycache__", ".git"],
    "token_check_interval": 100,  # Verificar token cada N archivos
    "min_token_minutes": 5,  # Minutos mínimos antes de necesitar refresh
}

class AutoUploader:
    def __init__(self):
        self.token = None
        self.source_dir = Path(CONFIG["source_dir"])
        self.progress_file = self.source_dir / ".upload_progress.json"
        self.token_file = self.source_dir / ".token"
        self.progress = self.load_progress()
        self.session = requests.Session()
        self.created_folders = set()
        self.uploaded_count = 0
        self.error_count = 0
        self.waiting_for_token = False

    def load_progress(self):
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    return json.load(f)
            except:
                pass
        return {"uploaded": [], "errors": [], "folders_created": []}

    def save_progress(self):
        with open(self.progress_file, "w") as f:
            json.dump(self.progress, f)

    def load_token(self):
        if self.token_file.exists():
            self.token = self.token_file.read_text().strip()
            return True
        return False

    def get_token_expiry(self):
        """Obtener tiempo de expiración del token"""
        if not self.token:
            return None
        try:
            parts = self.token.split('.')
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            return datetime.fromtimestamp(int(data['exp']))
        except:
            return None

    def token_minutes_remaining(self):
        """Minutos restantes del token"""
        exp = self.get_token_expiry()
        if not exp:
            return 0
        remaining = (exp - datetime.now()).total_seconds() / 60
        return max(0, remaining)

    def is_token_valid(self):
        """Verificar si el token tiene suficiente tiempo"""
        return self.token_minutes_remaining() > CONFIG["min_token_minutes"]

    def wait_for_new_token(self):
        """Esperar a que se actualice el token"""
        self.waiting_for_token = True
        print(f"\n⏸️  TOKEN EXPIRADO - Esperando nuevo token...")
        print(f"   Claude Code debe refrescar el token desde el navegador")
        print(f"   Verificando cada 30 segundos...\n")

        old_token = self.token
        while True:
            time.sleep(30)
            self.load_token()

            if self.token != old_token and self.is_token_valid():
                print(f"✅ Nuevo token detectado! Continuando...")
                self.waiting_for_token = False
                return True

            print(f"   [{datetime.now().strftime('%H:%M:%S')}] Esperando token...")

    def test_token(self):
        """Verificar si el token funciona"""
        if not self.token:
            return False
        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{CONFIG['dest_folder']}')"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        try:
            r = self.session.get(url, headers=headers, timeout=10)
            return r.status_code == 200
        except:
            return False

    def create_folder(self, relative_path):
        folder_path = f"{CONFIG['dest_folder']}/{relative_path}"
        if folder_path in self.created_folders:
            return True

        parts = relative_path.split("/")
        current_path = ""

        for part in parts:
            if not part:
                continue
            current_path = f"{current_path}/{part}" if current_path else part
            full_path = f"{CONFIG['dest_folder']}/{current_path}"

            if full_path in self.created_folders:
                continue

            encoded_path = urllib.parse.quote(full_path, safe='/')
            url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_path}')"
            headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

            r = self.session.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                self.created_folders.add(full_path)
                continue

            parent_path = "/".join(full_path.rsplit("/", 1)[:-1])
            encoded_parent = urllib.parse.quote(parent_path, safe='/')
            url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_parent}')/folders/add(url='{part}')"

            try:
                r = self.session.post(url, headers=headers, timeout=30)
                if r.status_code in [200, 201]:
                    self.created_folders.add(full_path)
                    print(f"  📁 Carpeta: {current_path}")
            except:
                pass

        return True

    def upload_file(self, file_path):
        relative_path = str(file_path.relative_to(self.source_dir))

        if relative_path in self.progress["uploaded"]:
            return {"status": "skipped", "file": relative_path}

        folder_relative = str(file_path.parent.relative_to(self.source_dir))
        if folder_relative != ".":
            self.create_folder(folder_relative)

        dest_folder = CONFIG["dest_folder"]
        if folder_relative != ".":
            dest_folder = f"{dest_folder}/{folder_relative}"

        encoded_folder = urllib.parse.quote(dest_folder, safe='/')
        file_name = file_path.name
        encoded_name = urllib.parse.quote(file_name)

        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files/add(url='{encoded_name}',overwrite=true)"

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/octet-stream"
        }

        try:
            with open(file_path, "rb") as f:
                data = f.read()

            r = self.session.post(url, headers=headers, data=data, timeout=120)

            if r.status_code in [200, 201]:
                self.progress["uploaded"].append(relative_path)
                return {"status": "success", "file": relative_path}
            elif r.status_code == 401:
                return {"status": "token_expired", "file": relative_path}
            else:
                self.progress["errors"].append({"file": relative_path, "error": str(r.status_code)})
                return {"status": "error", "file": relative_path, "code": r.status_code}

        except Exception as e:
            self.progress["errors"].append({"file": relative_path, "error": str(e)})
            return {"status": "error", "file": relative_path, "error": str(e)}

    def get_files_to_upload(self):
        files = []
        for ext in CONFIG["extensions"]:
            for file_path in self.source_dir.rglob(f"*{ext}"):
                skip = False
                for exclude in CONFIG["exclude_dirs"]:
                    if exclude in str(file_path):
                        skip = True
                        break
                if not skip:
                    files.append(file_path)
        return files

    def run(self):
        print("=" * 60)
        print("  SUBIDA AUTOMÁTICA A ONEDRIVE")
        print("  (Se pausa automáticamente si el token expira)")
        print("=" * 60)
        print()

        if not self.load_token():
            print("❌ Token no encontrado. Ejecuta primero la extracción del token.")
            return

        if not self.is_token_valid():
            print("⚠️  Token expirado o por expirar. Esperando nuevo token...")
            self.wait_for_new_token()

        print(f"✅ Token válido ({self.token_minutes_remaining():.0f} min restantes)")
        print()

        all_files = self.get_files_to_upload()
        files_to_upload = [f for f in all_files
                          if str(f.relative_to(self.source_dir)) not in self.progress["uploaded"]]

        total = len(all_files)
        already = len(self.progress["uploaded"])
        pending = len(files_to_upload)

        print(f"📊 Total: {total} | Ya subidos: {already} | Pendientes: {pending}")
        print()

        if not files_to_upload:
            print("🎉 ¡Todos los archivos ya fueron subidos!")
            return

        print("Iniciando subida... (Ctrl+C para pausar)")
        print()

        start_time = time.time()
        check_counter = 0

        try:
            for i, file_path in enumerate(files_to_upload, 1):
                check_counter += 1

                # Verificar token periódicamente
                if check_counter >= CONFIG["token_check_interval"]:
                    check_counter = 0
                    if not self.is_token_valid():
                        self.save_progress()
                        self.wait_for_new_token()

                result = self.upload_file(file_path)

                if result["status"] == "success":
                    self.uploaded_count += 1
                    pct = ((already + self.uploaded_count) / total) * 100
                    print(f"✅ [{already + self.uploaded_count}/{total}] ({pct:.1f}%) {result['file']}")
                elif result["status"] == "token_expired":
                    print(f"⚠️  Token expirado durante subida")
                    self.save_progress()
                    self.wait_for_new_token()
                    # Reintentar este archivo
                    result = self.upload_file(file_path)
                    if result["status"] == "success":
                        self.uploaded_count += 1
                        print(f"✅ (reintento) {result['file']}")
                elif result["status"] == "error":
                    self.error_count += 1
                    print(f"❌ [{i}/{pending}] {result['file']}")

                # Guardar progreso cada 50 archivos
                if self.uploaded_count % 50 == 0:
                    self.save_progress()

                time.sleep(0.05)

        except KeyboardInterrupt:
            print("\n\n⏸️  Pausado por el usuario")

        finally:
            self.save_progress()
            elapsed = time.time() - start_time

            print()
            print("=" * 60)
            print(f"📊 RESUMEN")
            print(f"   ✅ Subidos esta sesión: {self.uploaded_count}")
            print(f"   ❌ Errores: {self.error_count}")
            print(f"   📁 Total subidos: {len(self.progress['uploaded'])}")
            print(f"   ⏱️  Tiempo: {elapsed/60:.1f} min")
            print(f"   📈 Restantes: {total - len(self.progress['uploaded'])}")
            print("=" * 60)


if __name__ == "__main__":
    uploader = AutoUploader()
    uploader.run()
