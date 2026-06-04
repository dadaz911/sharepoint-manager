#!/usr/bin/env python3
"""
Script para subir archivos masivamente a OneDrive usando el token del navegador.
Uso: python3 subir_onedrive.py
"""

import json
import sys
import time
import urllib.parse
from pathlib import Path

import requests

# Configuración
CONFIG = {
    "base_url": "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web",
    "dest_folder": "/personal/dzuniga_shd_gov_co1/Documents/Pruebas",
    "source_dir": "/home/daniel/Desktop/Cargue a Onedrive",
    "max_workers": 3,  # Subidas en paralelo
    "extensions": [".pdf", ".PDF"],
    "exclude_dirs": [".claude", "__pycache__", ".git"],
}


class OneDriveUploader:
    def __init__(self):
        self.token = None
        self.source_dir = Path(CONFIG["source_dir"])
        self.progress_file = self.source_dir / ".upload_progress.json"
        self.progress = self.load_progress()
        self.session = requests.Session()
        self.uploaded_count = 0
        self.error_count = 0
        self.created_folders = set()

    def load_progress(self):
        """Cargar progreso anterior"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    return json.load(f)
            except:
                pass
        return {"uploaded": [], "errors": [], "folders_created": []}

    def save_progress(self):
        """Guardar progreso"""
        with open(self.progress_file, "w") as f:
            json.dump(self.progress, f, indent=2)

    def load_token(self):
        """Cargar token desde archivo"""
        token_file = self.source_dir / ".token"
        if token_file.exists():
            self.token = token_file.read_text().strip()
            return True
        return False

    def test_token(self):
        """Verificar si el token es válido"""
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
        """Crear una carpeta en OneDrive"""
        folder_path = f"{CONFIG['dest_folder']}/{relative_path}"

        if folder_path in self.created_folders:
            return True

        # Crear carpetas padre primero
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

            # Crear carpeta
            parent_path = "/".join(full_path.rsplit("/", 1)[:-1])
            encoded_parent = urllib.parse.quote(parent_path, safe='/')

            url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_parent}')/folders/add(url='{part}')"

            try:
                r = self.session.post(url, headers=headers, timeout=30)
                if r.status_code in [200, 201]:
                    self.created_folders.add(full_path)
                    print(f"  📁 Carpeta creada: {current_path}")
            except Exception:
                pass

        return True

    def upload_file(self, file_path):
        """Subir un archivo a OneDrive"""
        relative_path = str(file_path.relative_to(self.source_dir))

        # Saltar si ya fue subido
        if relative_path in self.progress["uploaded"]:
            return {"status": "skipped", "file": relative_path}

        # Crear carpeta si es necesario
        folder_relative = str(file_path.parent.relative_to(self.source_dir))
        if folder_relative != ".":
            self.create_folder(folder_relative)

        # Preparar URL
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
            "Content-Type": "application/octet-stream",
        }

        try:
            with open(file_path, "rb") as f:
                data = f.read()

            r = self.session.post(url, headers=headers, data=data, timeout=120)

            if r.status_code in [200, 201]:
                self.progress["uploaded"].append(relative_path)
                return {"status": "success", "file": relative_path}
            else:
                error_msg = r.text[:200] if r.text else str(r.status_code)
                self.progress["errors"].append({"file": relative_path, "error": error_msg})
                return {"status": "error", "file": relative_path, "error": error_msg}

        except Exception as e:
            self.progress["errors"].append({"file": relative_path, "error": str(e)})
            return {"status": "error", "file": relative_path, "error": str(e)}

    def get_files_to_upload(self):
        """Obtener lista de archivos a subir"""
        files = []
        for ext in CONFIG["extensions"]:
            for file_path in self.source_dir.rglob(f"*{ext}"):
                # Excluir directorios específicos
                skip = False
                for exclude in CONFIG["exclude_dirs"]:
                    if exclude in str(file_path):
                        skip = True
                        break
                if not skip:
                    files.append(file_path)
        return files

    def run(self):
        """Ejecutar la subida"""
        print("=" * 50)
        print("  Subida masiva a OneDrive")
        print("=" * 50)
        print()

        # Cargar token
        if not self.load_token():
            print("❌ Token no encontrado.")
            print("   Asegúrate de que el archivo .token existe")
            return

        # Verificar token
        print("🔑 Verificando token...")
        if not self.test_token():
            print("❌ Token inválido o expirado.")
            print("   Necesitas actualizar el token (ver instrucciones)")
            return

        print("✅ Token válido")
        print()

        # Obtener archivos
        all_files = self.get_files_to_upload()
        already_uploaded = len(self.progress["uploaded"])

        # Filtrar ya subidos
        files_to_upload = [f for f in all_files if str(f.relative_to(self.source_dir)) not in self.progress["uploaded"]]

        print(f"📊 Total de archivos: {len(all_files)}")
        print(f"✅ Ya subidos: {already_uploaded}")
        print(f"📤 Pendientes: {len(files_to_upload)}")
        print()

        if not files_to_upload:
            print("🎉 ¡Todos los archivos ya fueron subidos!")
            return

        print("Iniciando subida... (Ctrl+C para pausar)")
        print()

        start_time = time.time()

        try:
            for i, file_path in enumerate(files_to_upload, 1):
                result = self.upload_file(file_path)

                if result["status"] == "success":
                    self.uploaded_count += 1
                    print(f"✅ [{i}/{len(files_to_upload)}] {result['file']}")
                elif result["status"] == "error":
                    self.error_count += 1
                    print(f"❌ [{i}/{len(files_to_upload)}] {result['file']}")

                # Guardar progreso cada 10 archivos
                if i % 10 == 0:
                    self.save_progress()

                # Pequeña pausa
                time.sleep(0.05)

        except KeyboardInterrupt:
            print("\n\n⏸️  Pausado por el usuario")

        finally:
            self.save_progress()

            elapsed = time.time() - start_time
            print()
            print("=" * 50)
            print("📊 Resumen:")
            print(f"   ✅ Subidos: {self.uploaded_count}")
            print(f"   ❌ Errores: {self.error_count}")
            print(f"   ⏱️  Tiempo: {elapsed / 60:.1f} minutos")
            print(f"   📁 Total subidos: {len(self.progress['uploaded'])}")
            print("=" * 50)


def refresh_token_instructions():
    """Mostrar instrucciones para refrescar token"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║  CÓMO REFRESCAR EL TOKEN                                      ║
╠══════════════════════════════════════════════════════════════╣
║  1. Abre OneDrive en Chrome/Edge                              ║
║  2. Presiona F12 para abrir DevTools                          ║
║  3. Ve a la pestaña "Application" (o "Aplicación")            ║
║  4. En el panel izquierdo: Local Storage → tu sitio           ║
║  5. Busca la clave que contiene "OAuth" y "sharepoint"        ║
║  6. Copia el valor del campo "value" (es el token)            ║
║  7. Pégalo en el archivo .token                               ║
╚══════════════════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--help-token":
        refresh_token_instructions()
    else:
        uploader = OneDriveUploader()
        uploader.run()
