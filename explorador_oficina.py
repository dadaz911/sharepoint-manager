#!/usr/bin/env python3
"""
Explorador del sitio SharePoint de la Oficina de Depuración de Cartera.
Genera un mapa del sitio sin descargar archivos.
"""

import os
import json
import urllib.parse
import requests
import base64
from pathlib import Path
from datetime import datetime

CONFIG = {
    "site_url": "https://shdgov.sharepoint.com/sites/OficinadeDepuracindeCartera",
    "base_url": "https://shdgov.sharepoint.com/sites/OficinadeDepuracindeCartera/_api/web",
    "root_folder": "/sites/OficinadeDepuracindeCartera/Documentos compartidos",
    "token_file": Path("/home/daniel/Desktop/Cargue a Onedrive/.token"),
    "cache_file": Path("/home/daniel/Desktop/Cargue a Onedrive/.oficina_map.json"),
    "download_dir": Path("/home/daniel/Desktop/Descargas_Oficina"),
}


class ExploradorOficina:
    def __init__(self):
        self.token = None
        self.mapa = {"folders": {}, "last_update": None, "site": CONFIG["site_url"]}
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

    def token_minutes(self):
        if not self.token:
            return 0
        try:
            parts = self.token.split('.')
            payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            exp = datetime.fromtimestamp(int(data['exp']))
            return max(0, (exp - datetime.now()).total_seconds() / 60)
        except:
            return 0

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json;odata=verbose"
        }

    def load_cache(self):
        if CONFIG["cache_file"].exists():
            try:
                self.mapa = json.loads(CONFIG["cache_file"].read_text())
            except:
                pass

    def save_cache(self):
        self.mapa["last_update"] = datetime.now().isoformat()
        CONFIG["cache_file"].write_text(json.dumps(self.mapa, indent=2, ensure_ascii=False))

    def explorar_carpeta(self, folder_path, profundidad=1, max_prof=3):
        """Explorar una carpeta y obtener su contenido."""
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
                    if not name.startswith('_'):
                        resultado["folders"].append({
                            "name": name,
                            "path": f.get('ServerRelativeUrl'),
                            "item_count": f.get('ItemCount', 0)
                        })
        except Exception as e:
            print(f"{indent}[ERROR carpetas] {e}")

        # Obtener archivos (solo metadata)
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
                resultado["file_count"] = ">5000"
        except Exception as e:
            print(f"{indent}[ERROR archivos] {e}")

        return resultado

    def generar_mapa(self, folder_path=None, max_profundidad=2):
        """Generar mapa del sitio"""
        folder = folder_path or CONFIG["root_folder"]

        print(f"\n{'='*60}")
        print(f"  MAPA DEL SITIO: Oficina de Depuración de Cartera")
        print(f"  Carpeta: {folder}")
        print(f"  Profundidad: {max_profundidad}")
        print(f"  Token: {self.token_minutes():.0f} min restantes")
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
            nombre = data["path"].split('/')[-1] or "Documentos compartidos"

            file_info = data.get("file_count", 0)
            size_mb = data.get("total_size", 0) / (1024*1024)

            if file_info == ">5000":
                print(f"{indent}[DIR] {nombre}/ (>5000 archivos)")
            elif file_info > 0:
                print(f"{indent}[DIR] {nombre}/ ({file_info} archivos, {size_mb:.1f} MB)")
            else:
                print(f"{indent}[DIR] {nombre}/")

            for subfolder in data.get("folders", []):
                if subfolder.get("contenido"):
                    mostrar_recursivo(subfolder["contenido"], nivel + 1)
                else:
                    item_count = subfolder.get("item_count", "?")
                    print(f"{indent}  [DIR] {subfolder['name']}/ ({item_count} items)")

        print(f"\n{'='*60}")
        print(f"  ARBOL - Oficina de Depuración de Cartera")
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

            for f in data.get("files", []):
                if termino in f["name"].lower():
                    resultados.append({
                        "tipo": "archivo",
                        "nombre": f["name"],
                        "ruta": f["path"],
                        "tamano": f["size"]
                    })

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

            file_name = file_path.split('/')[-1]
            if destino:
                dest_path = Path(destino)
            else:
                CONFIG["download_dir"].mkdir(parents=True, exist_ok=True)
                dest_path = CONFIG["download_dir"] / file_name

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

    def listar_bibliotecas(self):
        """Listar todas las bibliotecas de documentos del sitio"""
        if not self.token_valid():
            self.load_token()
            if not self.token_valid():
                print("[ERROR] Token expirado")
                return []

        url = f"{CONFIG['base_url']}/lists"
        url += "?$filter=BaseTemplate eq 101"
        url += "&$select=Title,ItemCount,RootFolder/ServerRelativeUrl"
        url += "&$expand=RootFolder"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code == 200:
                libs = r.json().get('d', {}).get('results', [])
                print(f"\n{'='*60}")
                print(f"  BIBLIOTECAS DE DOCUMENTOS")
                print(f"{'='*60}\n")
                for lib in libs:
                    title = lib.get('Title', 'N/A')
                    count = lib.get('ItemCount', 0)
                    folder = lib.get('RootFolder', {}).get('ServerRelativeUrl', 'N/A')
                    print(f"  [{count:,} items] {title}")
                    print(f"             {folder}\n")
                return libs
        except Exception as e:
            print(f"[ERROR] {e}")
        return []


def menu_interactivo():
    """Menu interactivo"""
    exp = ExploradorOficina()

    if not exp.token_valid():
        print("[ERROR] Token no valido")
        return

    while True:
        print(f"\n{'='*60}")
        print("  EXPLORADOR - Oficina de Depuración de Cartera")
        print(f"  Token: {exp.token_minutes():.0f} min restantes")
        print(f"{'='*60}")
        print()
        print("  1. Listar bibliotecas de documentos")
        print("  2. Generar/Actualizar mapa")
        print("  3. Mostrar arbol de carpetas")
        print("  4. Buscar en el mapa")
        print("  5. Explorar carpeta especifica")
        print("  6. Descargar archivo")
        print("  7. Ver mapa cacheado (JSON)")
        print("  0. Salir")
        print()

        opcion = input("  Opcion: ").strip()

        if opcion == '1':
            exp.listar_bibliotecas()

        elif opcion == '2':
            folder = input("  Carpeta (Enter para Documentos compartidos): ").strip()
            folder = folder or CONFIG["root_folder"]
            prof = input("  Profundidad [2]: ").strip()
            prof = int(prof) if prof else 2
            exp.generar_mapa(folder, max_profundidad=prof)

        elif opcion == '3':
            exp.mostrar_arbol()

        elif opcion == '4':
            termino = input("  Buscar: ").strip()
            if termino:
                exp.buscar(termino)

        elif opcion == '5':
            folder = input("  Ruta de la carpeta: ").strip()
            if folder:
                resultado = exp.explorar_carpeta(folder)
                if resultado:
                    print(f"\n  Subcarpetas: {len(resultado.get('folders', []))}")
                    for f in resultado.get('folders', [])[:10]:
                        print(f"    [DIR] {f['name']}/ ({f.get('item_count', '?')} items)")
                    print(f"\n  Archivos: {resultado.get('file_count', 0)}")
                    for f in resultado.get('files', [])[:10]:
                        size_kb = f['size'] / 1024
                        print(f"    [FILE] {f['name']} ({size_kb:.1f} KB)")

        elif opcion == '6':
            ruta = input("  Ruta del archivo: ").strip()
            if ruta:
                exp.descargar_archivo(ruta)

        elif opcion == '7':
            if exp.mapa.get("folders"):
                print(json.dumps(exp.mapa, indent=2, ensure_ascii=False)[:2000])
                print("\n... (truncado)")
            else:
                print("  No hay mapa cacheado")

        elif opcion == '0':
            print("  Hasta luego!")
            break


if __name__ == "__main__":
    menu_interactivo()
