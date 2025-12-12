#!/usr/bin/env python3
"""
Explorador de SharePoint - Navega y descarga archivos bajo demanda.
Genera un mapa del sitio sin descargar los archivos.
"""

import os
import json
import urllib.parse
import requests
import base64
from pathlib import Path
from datetime import datetime

CONFIG = {
    "base_url": "https://shdgov-my.sharepoint.com/personal/dzuniga_shd_gov_co1/_api/web",
    "root_folder": "/personal/dzuniga_shd_gov_co1/Documents",
    "token_file": Path("/home/daniel/Desktop/Cargue a Onedrive/.token"),
    "cache_file": Path("/home/daniel/Desktop/Cargue a Onedrive/.sharepoint_map.json"),
    "download_dir": Path("/home/daniel/Desktop/Descargas_SharePoint"),
}


class ExploradorSharePoint:
    def __init__(self):
        self.token = None
        self.mapa = {"folders": {}, "last_update": None}
        self.load_token()
        self.load_cache()

    def load_token(self):
        if CONFIG["token_file"].exists():
            self.token = CONFIG["token_file"].read_text().strip()
            return True
        return False

    def token_valid(self):
        if not self.token:
            return False
        try:
            parts = self.token.split('.')
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            exp = datetime.fromtimestamp(int(data['exp']))
            return datetime.now() < exp
        except:
            return False

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json;odata=verbose"
        }

    def load_cache(self):
        """Cargar mapa cacheado"""
        if CONFIG["cache_file"].exists():
            try:
                self.mapa = json.loads(CONFIG["cache_file"].read_text())
            except:
                pass

    def save_cache(self):
        """Guardar mapa a cache"""
        self.mapa["last_update"] = datetime.now().isoformat()
        CONFIG["cache_file"].write_text(json.dumps(self.mapa, indent=2))

    def explorar_carpeta(self, folder_path, profundidad=1, max_prof=3):
        """
        Explorar una carpeta y obtener su contenido.
        No descarga archivos, solo obtiene la estructura.
        """
        if not self.token_valid():
            self.load_token()
            if not self.token_valid():
                print("[ERROR] Token expirado")
                return None

        encoded_folder = urllib.parse.quote(folder_path, safe='/')
        indent = "  " * profundidad

        resultado = {
            "path": folder_path,
            "folders": [],
            "files": [],
            "file_count": 0,
            "total_size": 0
        }

        # Obtener subcarpetas
        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Folders"
        url += "?$select=Name,ItemCount,ServerRelativeUrl"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code == 200:
                data = r.json()
                folders = data.get('d', {}).get('results', [])
                for f in folders:
                    name = f.get('Name', '')
                    if not name.startswith('_'):  # Ignorar carpetas de sistema
                        resultado["folders"].append({
                            "name": name,
                            "path": f.get('ServerRelativeUrl'),
                            "item_count": f.get('ItemCount', 0)
                        })
        except Exception as e:
            print(f"{indent}[ERROR carpetas] {e}")

        # Obtener archivos (solo metadata, no contenido)
        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files"
        url += "?$select=Name,Length,TimeLastModified,ServerRelativeUrl&$top=100"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code == 200:
                data = r.json()
                files = data.get('d', {}).get('results', [])
                for f in files:
                    size = int(f.get('Length', 0))
                    resultado["files"].append({
                        "name": f.get('Name'),
                        "size": size,
                        "modified": f.get('TimeLastModified'),
                        "path": f.get('ServerRelativeUrl')
                    })
                    resultado["total_size"] += size
                resultado["file_count"] = len(files)
            elif r.status_code == 500:
                # Carpeta con muchos archivos - marcar como "grande"
                resultado["file_count"] = ">5000"
        except Exception as e:
            print(f"{indent}[ERROR archivos] {e}")

        return resultado

    def generar_mapa(self, folder_path=None, max_profundidad=2):
        """Generar mapa completo del sitio"""
        folder = folder_path or CONFIG["root_folder"]

        print(f"\n{'='*60}")
        print(f"  GENERANDO MAPA DE: {folder}")
        print(f"  Profundidad maxima: {max_profundidad}")
        print(f"{'='*60}\n")

        def explorar_recursivo(path, nivel=0):
            if nivel > max_profundidad:
                return None

            indent = "  " * nivel
            nombre = path.split('/')[-1] or "Raiz"
            print(f"{indent}Explorando: {nombre}...")

            resultado = self.explorar_carpeta(path, nivel)
            if not resultado:
                return None

            # Explorar subcarpetas recursivamente
            for subfolder in resultado.get("folders", []):
                subfolder["contenido"] = explorar_recursivo(
                    subfolder["path"],
                    nivel + 1
                )

            return resultado

        self.mapa["folders"][folder] = explorar_recursivo(folder)
        self.save_cache()

        print(f"\n[OK] Mapa guardado en: {CONFIG['cache_file']}")
        return self.mapa

    def mostrar_arbol(self, folder_path=None, nivel=0):
        """Mostrar arbol de carpetas"""
        if not self.mapa.get("folders"):
            print("[INFO] No hay mapa cacheado. Usa 'generar_mapa()' primero.")
            return

        folder = folder_path or CONFIG["root_folder"]
        data = self.mapa["folders"].get(folder)

        if not data:
            print(f"[INFO] Carpeta no encontrada en cache: {folder}")
            return

        def mostrar_recursivo(data, nivel=0):
            indent = "  " * nivel
            nombre = data["path"].split('/')[-1] or "Raiz"

            # Mostrar info de carpeta
            file_info = data.get("file_count", 0)
            size_mb = data.get("total_size", 0) / (1024*1024)

            if file_info == ">5000":
                print(f"{indent}[DIR] {nombre}/ (>5000 archivos)")
            elif file_info > 0:
                print(f"{indent}[DIR] {nombre}/ ({file_info} archivos, {size_mb:.1f} MB)")
            else:
                print(f"{indent}[DIR] {nombre}/")

            # Mostrar subcarpetas
            for subfolder in data.get("folders", []):
                if subfolder.get("contenido"):
                    mostrar_recursivo(subfolder["contenido"], nivel + 1)
                else:
                    item_count = subfolder.get("item_count", "?")
                    print(f"{indent}  [DIR] {subfolder['name']}/ ({item_count} items)")

        print(f"\n{'='*60}")
        print(f"  ARBOL DE CARPETAS")
        print(f"  Ultima actualizacion: {self.mapa.get('last_update', 'N/A')}")
        print(f"{'='*60}\n")

        mostrar_recursivo(data)

    def buscar(self, termino):
        """Buscar en el mapa cacheado"""
        resultados = []
        termino = termino.lower()

        def buscar_recursivo(data):
            if not data:
                return

            # Buscar en archivos
            for f in data.get("files", []):
                if termino in f["name"].lower():
                    resultados.append({
                        "tipo": "archivo",
                        "nombre": f["name"],
                        "ruta": f["path"],
                        "tamano": f["size"]
                    })

            # Buscar en carpetas
            for folder in data.get("folders", []):
                if termino in folder["name"].lower():
                    resultados.append({
                        "tipo": "carpeta",
                        "nombre": folder["name"],
                        "ruta": folder["path"]
                    })
                if folder.get("contenido"):
                    buscar_recursivo(folder["contenido"])

        for folder_data in self.mapa.get("folders", {}).values():
            buscar_recursivo(folder_data)

        print(f"\n[BUSQUEDA] '{termino}' - {len(resultados)} resultados\n")
        for r in resultados[:20]:
            if r["tipo"] == "archivo":
                size_kb = r["tamano"] / 1024
                print(f"  [FILE] {r['nombre']} ({size_kb:.1f} KB)")
                print(f"         {r['ruta']}")
            else:
                print(f"  [DIR]  {r['nombre']}/")
                print(f"         {r['ruta']}")

        if len(resultados) > 20:
            print(f"\n  ... y {len(resultados) - 20} resultados mas")

        return resultados

    def descargar_archivo(self, file_path, destino=None):
        """Descargar un archivo especifico"""
        if not self.token_valid():
            self.load_token()
            if not self.token_valid():
                print("[ERROR] Token expirado")
                return False

        encoded_path = urllib.parse.quote(file_path, safe='/')
        url = f"{CONFIG['base_url']}/GetFileByServerRelativeUrl('{encoded_path}')/$value"

        try:
            print(f"[DESCARGANDO] {file_path.split('/')[-1]}...")
            r = requests.get(url, headers=self.get_headers(), timeout=300, stream=True)

            if r.status_code != 200:
                print(f"[ERROR] {r.status_code}")
                return False

            # Determinar destino
            file_name = file_path.split('/')[-1]
            if destino:
                dest_path = Path(destino)
            else:
                CONFIG["download_dir"].mkdir(parents=True, exist_ok=True)
                dest_path = CONFIG["download_dir"] / file_name

            # Descargar con progreso
            total = int(r.headers.get('content-length', 0))
            downloaded = 0

            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = (downloaded / total) * 100
                            print(f"\r  {pct:.1f}% ({downloaded/1024/1024:.1f} MB)", end="", flush=True)

            print(f"\n[OK] Guardado en: {dest_path}")
            return True

        except Exception as e:
            print(f"[ERROR] {e}")
            return False

    def descargar_carpeta(self, folder_path, destino=None):
        """Descargar todos los archivos de una carpeta"""
        if not self.token_valid():
            self.load_token()
            if not self.token_valid():
                print("[ERROR] Token expirado")
                return False

        folder_name = folder_path.split('/')[-1]
        if destino:
            dest_dir = Path(destino)
        else:
            dest_dir = CONFIG["download_dir"] / folder_name

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Obtener lista de archivos
        encoded_folder = urllib.parse.quote(folder_path, safe='/')
        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files"
        url += "?$select=Name,ServerRelativeUrl"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code != 200:
                print(f"[ERROR] {r.status_code}")
                return False

            data = r.json()
            files = data.get('d', {}).get('results', [])

            print(f"\n[DESCARGANDO CARPETA] {folder_name}")
            print(f"  {len(files)} archivos -> {dest_dir}\n")

            for i, f in enumerate(files):
                file_path = f.get('ServerRelativeUrl')
                file_name = f.get('Name')
                dest_file = dest_dir / file_name

                print(f"  [{i+1}/{len(files)}] {file_name}")
                self.descargar_archivo(file_path, str(dest_file))

            print(f"\n[OK] Carpeta descargada: {dest_dir}")
            return True

        except Exception as e:
            print(f"[ERROR] {e}")
            return False


def menu_interactivo():
    """Menu interactivo del explorador"""
    exp = ExploradorSharePoint()

    if not exp.token_valid():
        print("[ERROR] Token no valido")
        return

    while True:
        print(f"\n{'='*60}")
        print("  EXPLORADOR DE SHAREPOINT")
        print(f"{'='*60}")
        print()
        print("  1. Generar/Actualizar mapa del sitio")
        print("  2. Mostrar arbol de carpetas")
        print("  3. Buscar en el mapa")
        print("  4. Explorar carpeta especifica")
        print("  5. Descargar archivo")
        print("  6. Descargar carpeta completa")
        print("  7. Ver mapa cacheado (JSON)")
        print("  0. Salir")
        print()

        opcion = input("  Opcion: ").strip()

        if opcion == '1':
            folder = input("  Carpeta raiz (Enter para Documents): ").strip()
            folder = folder or CONFIG["root_folder"]
            prof = input("  Profundidad [2]: ").strip()
            prof = int(prof) if prof else 2
            exp.generar_mapa(folder, max_profundidad=prof)

        elif opcion == '2':
            exp.mostrar_arbol()

        elif opcion == '3':
            termino = input("  Buscar: ").strip()
            if termino:
                exp.buscar(termino)

        elif opcion == '4':
            folder = input("  Ruta de la carpeta: ").strip()
            if folder:
                resultado = exp.explorar_carpeta(folder)
                if resultado:
                    print(f"\n  Subcarpetas: {len(resultado.get('folders', []))}")
                    for f in resultado.get('folders', [])[:10]:
                        print(f"    [DIR] {f['name']}/")
                    print(f"\n  Archivos: {resultado.get('file_count', 0)}")
                    for f in resultado.get('files', [])[:10]:
                        size_kb = f['size'] / 1024
                        print(f"    [FILE] {f['name']} ({size_kb:.1f} KB)")

        elif opcion == '5':
            ruta = input("  Ruta del archivo: ").strip()
            if ruta:
                exp.descargar_archivo(ruta)

        elif opcion == '6':
            ruta = input("  Ruta de la carpeta: ").strip()
            if ruta:
                exp.descargar_carpeta(ruta)

        elif opcion == '7':
            if exp.mapa.get("folders"):
                print(json.dumps(exp.mapa, indent=2)[:2000])
                print("\n... (truncado)")
            else:
                print("  No hay mapa cacheado")

        elif opcion == '0':
            print("  Hasta luego!")
            break


if __name__ == "__main__":
    menu_interactivo()
