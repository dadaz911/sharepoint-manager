#!/usr/bin/env python3
"""
Script de subida PARALELA a OneDrive/SharePoint
Permite configurar el numero de hilos para acelerar la subida.
"""

import os
import sys
import json
import time
import urllib.parse
import requests
import base64
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

CONFIG = {
    "base_url": "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web",
    "dest_folder": "/personal/dzuniga_shd_gov_co1/Documents/Pruebas",
    "source_dir": "/home/daniel/Desktop/Cargue a Onedrive",
    "extensions": [".pdf", ".PDF"],
    "exclude_dirs": [".claude", "__pycache__", ".git"],
    "min_token_minutes": 5,
}

class ParallelUploader:
    def __init__(self, num_threads=4):
        self.num_threads = num_threads
        self.token = None
        self.source_dir = Path(CONFIG["source_dir"])
        self.progress_file = self.source_dir / ".upload_progress.json"
        self.token_file = self.source_dir / ".token"
        self.progress = self.load_progress()
        self.created_folders = set(self.progress.get("folders_created", []))
        self.lock = threading.Lock()
        self.uploaded_count = 0
        self.error_count = 0
        self.total_files = 0
        self.already_uploaded = 0
        self.stop_flag = False

    def load_progress(self):
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    return json.load(f)
            except:
                pass
        return {"uploaded": [], "errors": [], "folders_created": []}

    def save_progress(self):
        with self.lock:
            self.progress["folders_created"] = list(self.created_folders)
            with open(self.progress_file, "w") as f:
                json.dump(self.progress, f)

    def load_token(self):
        if self.token_file.exists():
            self.token = self.token_file.read_text().strip()
            return True
        return False

    def token_minutes_remaining(self):
        if not self.token:
            return 0
        try:
            parts = self.token.split('.')
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            exp = datetime.fromtimestamp(int(data['exp']))
            remaining = (exp - datetime.now()).total_seconds() / 60
            return max(0, remaining)
        except:
            return 0

    def is_token_valid(self):
        return self.token_minutes_remaining() > CONFIG["min_token_minutes"]

    def wait_for_new_token(self):
        print(f"\n[PAUSA] Token expirado - Esperando nuevo token...")
        old_token = self.token
        while not self.stop_flag:
            time.sleep(10)
            self.load_token()
            if self.token != old_token and self.is_token_valid():
                print(f"[OK] Nuevo token detectado! Continuando...")
                return True
        return False

    def create_folder(self, relative_path):
        folder_path = f"{CONFIG['dest_folder']}/{relative_path}"

        with self.lock:
            if folder_path in self.created_folders:
                return True

        parts = relative_path.split("/")
        current_path = ""

        for part in parts:
            if not part:
                continue
            current_path = f"{current_path}/{part}" if current_path else part
            full_path = f"{CONFIG['dest_folder']}/{current_path}"

            with self.lock:
                if full_path in self.created_folders:
                    continue

            encoded_path = urllib.parse.quote(full_path, safe='/')
            url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_path}')"
            headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    with self.lock:
                        self.created_folders.add(full_path)
                    continue

                parent_path = "/".join(full_path.rsplit("/", 1)[:-1])
                encoded_parent = urllib.parse.quote(parent_path, safe='/')
                url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_parent}')/folders/add(url='{part}')"

                r = requests.post(url, headers=headers, timeout=30)
                if r.status_code in [200, 201]:
                    with self.lock:
                        self.created_folders.add(full_path)
            except:
                pass

        return True

    def upload_single_file(self, file_path):
        if self.stop_flag:
            return {"status": "stopped", "file": str(file_path)}

        relative_path = str(file_path.relative_to(self.source_dir))

        with self.lock:
            if relative_path in self.progress["uploaded"]:
                return {"status": "skipped", "file": relative_path}

        # Verificar token
        if not self.is_token_valid():
            self.load_token()
            if not self.is_token_valid():
                return {"status": "token_expired", "file": relative_path}

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

            r = requests.post(url, headers=headers, data=data, timeout=120)

            if r.status_code in [200, 201]:
                with self.lock:
                    self.progress["uploaded"].append(relative_path)
                    self.uploaded_count += 1
                return {"status": "success", "file": relative_path}
            elif r.status_code == 401:
                return {"status": "token_expired", "file": relative_path}
            elif r.status_code == 429:  # Rate limited
                time.sleep(2)
                return {"status": "retry", "file": relative_path}
            else:
                with self.lock:
                    self.progress["errors"].append({"file": relative_path, "error": str(r.status_code)})
                    self.error_count += 1
                return {"status": "error", "file": relative_path, "code": r.status_code}

        except Exception as e:
            with self.lock:
                self.progress["errors"].append({"file": relative_path, "error": str(e)})
                self.error_count += 1
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
        print(f"  SUBIDA PARALELA A ONEDRIVE ({self.num_threads} hilos)")
        print("=" * 60)
        print()

        if not self.load_token():
            print("[ERROR] Token no encontrado")
            return

        if not self.is_token_valid():
            print("[AVISO] Token expirado, esperando nuevo...")
            if not self.wait_for_new_token():
                return

        print(f"[OK] Token valido ({self.token_minutes_remaining():.0f} min)")
        print()

        all_files = self.get_files_to_upload()
        self.total_files = len(all_files)

        with self.lock:
            uploaded_set = set(self.progress["uploaded"])

        files_to_upload = [f for f in all_files
                          if str(f.relative_to(self.source_dir)) not in uploaded_set]

        self.already_uploaded = len(self.progress["uploaded"])
        pending = len(files_to_upload)

        print(f"Total: {self.total_files} | Ya subidos: {self.already_uploaded} | Pendientes: {pending}")
        print()

        if not files_to_upload:
            print("[COMPLETADO] Todos los archivos ya fueron subidos!")
            return

        print(f"Iniciando con {self.num_threads} hilos... (Ctrl+C para pausar)")
        print()

        start_time = time.time()
        last_save = time.time()
        retry_queue = []

        try:
            with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                futures = {executor.submit(self.upload_single_file, f): f for f in files_to_upload}

                for future in as_completed(futures):
                    if self.stop_flag:
                        break

                    result = future.result()

                    if result["status"] == "success":
                        current = self.already_uploaded + self.uploaded_count
                        pct = (current / self.total_files) * 100
                        elapsed = time.time() - start_time
                        rate = self.uploaded_count / (elapsed / 60) if elapsed > 0 else 0
                        remaining_files = self.total_files - current
                        eta_min = remaining_files / rate if rate > 0 else 0

                        print(f"\r[{current}/{self.total_files}] {pct:.1f}% | {rate:.0f}/min | ETA: {eta_min:.0f}min   ", end="", flush=True)

                    elif result["status"] == "token_expired":
                        print(f"\n[TOKEN] Expirado, pausando hilos...")
                        self.save_progress()
                        if self.wait_for_new_token():
                            retry_queue.append(futures[future])

                    elif result["status"] == "retry":
                        retry_queue.append(futures[future])

                    # Guardar progreso cada 30 segundos
                    if time.time() - last_save > 30:
                        self.save_progress()
                        last_save = time.time()

                # Procesar reintentos
                if retry_queue and not self.stop_flag:
                    print(f"\n[RETRY] Reintentando {len(retry_queue)} archivos...")
                    for f in retry_queue:
                        self.upload_single_file(f)

        except KeyboardInterrupt:
            print("\n\n[PAUSA] Detenido por usuario")
            self.stop_flag = True

        finally:
            self.save_progress()
            elapsed = time.time() - start_time

            print()
            print("=" * 60)
            print(f"  RESUMEN")
            print(f"  Subidos esta sesion: {self.uploaded_count}")
            print(f"  Errores: {self.error_count}")
            print(f"  Total subidos: {len(self.progress['uploaded'])}")
            print(f"  Tiempo: {elapsed/60:.1f} min")
            print(f"  Velocidad: {self.uploaded_count/(elapsed/60):.0f} archivos/min")
            print(f"  Restantes: {self.total_files - len(self.progress['uploaded'])}")
            print("=" * 60)


def main():
    print()
    print("=" * 60)
    print("  CONFIGURACION DE SUBIDA PARALELA")
    print("=" * 60)
    print()
    print("  Recomendaciones:")
    print("    1-2 hilos: Conservador, sin riesgo de throttling")
    print("    3-4 hilos: Balanceado (recomendado)")
    print("    5-6 hilos: Agresivo, puede haber throttling")
    print()

    while True:
        try:
            threads = input("  Numero de hilos [4]: ").strip()
            if not threads:
                threads = 4
            else:
                threads = int(threads)

            if 1 <= threads <= 10:
                break
            else:
                print("  Por favor, ingresa un numero entre 1 y 10")
        except ValueError:
            print("  Por favor, ingresa un numero valido")

    print()
    uploader = ParallelUploader(num_threads=threads)
    uploader.run()


if __name__ == "__main__":
    main()
