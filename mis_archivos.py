#!/usr/bin/env python3
"""
Utilidad para gestionar SOLO mis archivos en OneDrive/SharePoint.
Solo permite operaciones en archivos donde el usuario actual es el Author.
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
    "dest_folder": "/personal/dzuniga_shd_gov_co1/Documents/Pruebas",
    "token_file": Path("/home/daniel/Desktop/Cargue a Onedrive/.token"),
}

class MisArchivos:
    def __init__(self):
        self.token = None
        self.my_email = None
        self.load_token()

    def load_token(self):
        """Cargar token y extraer email del usuario"""
        if CONFIG["token_file"].exists():
            self.token = CONFIG["token_file"].read_text().strip()
            # Extraer email del token
            try:
                parts = self.token.split('.')
                payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
                data = json.loads(base64.urlsafe_b64decode(payload))
                self.my_email = data.get('upn', '').lower()
                print(f"[OK] Usuario: {self.my_email}")
            except:
                print("[ERROR] No se pudo extraer el usuario del token")
                return False
        return True

    def token_valid(self):
        """Verificar si el token es valido"""
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

    def listar_archivos(self, folder_path=None, mostrar_todos=False, limite=None):
        """
        Listar archivos en una carpeta.
        Por defecto solo muestra MIS archivos.
        """
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return []

        folder = folder_path or CONFIG["dest_folder"]
        encoded_folder = urllib.parse.quote(folder, safe='/')

        # Obtener archivos con metadatos expandidos (Author)
        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files"
        url += "?$expand=Author,ListItemAllFields&$select=Name,Length,TimeCreated,Author/Email,Author/Title,ServerRelativeUrl"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code != 200:
                print(f"[ERROR] {r.status_code}: {r.text[:200]}")
                return []

            data = r.json()
            files = data.get('d', {}).get('results', [])

            mis_archivos = []
            otros_archivos = []

            for f in files:
                author_email = f.get('Author', {}).get('Email', '').lower()
                author_name = f.get('Author', {}).get('Title', 'Desconocido')
                es_mio = author_email == self.my_email

                file_info = {
                    "nombre": f.get('Name'),
                    "tamano": f.get('Length', 0),
                    "creado": f.get('TimeCreated'),
                    "autor_email": author_email,
                    "autor_nombre": author_name,
                    "ruta": f.get('ServerRelativeUrl'),
                    "es_mio": es_mio
                }

                if es_mio:
                    mis_archivos.append(file_info)
                else:
                    otros_archivos.append(file_info)

            # Mostrar resultados
            print(f"\n{'='*60}")
            print(f"  ARCHIVOS EN: {folder}")
            print(f"{'='*60}")

            print(f"\n[MIS ARCHIVOS] ({len(mis_archivos)})")
            print("-" * 60)
            for f in mis_archivos[:20]:  # Mostrar max 20
                tamano_kb = int(f['tamano']) / 1024 if f['tamano'] else 0
                print(f"  {f['nombre'][:40]:<40} {tamano_kb:>8.1f} KB")
            if len(mis_archivos) > 20:
                print(f"  ... y {len(mis_archivos) - 20} mas")

            if mostrar_todos:
                print(f"\n[ARCHIVOS DE OTROS] ({len(otros_archivos)})")
                print("-" * 60)
                for f in otros_archivos[:10]:
                    print(f"  {f['nombre'][:30]:<30} por {f['autor_nombre']}")
                if len(otros_archivos) > 10:
                    print(f"  ... y {len(otros_archivos) - 10} mas")

            print(f"\n[RESUMEN]")
            print(f"  Mis archivos: {len(mis_archivos)}")
            print(f"  De otros: {len(otros_archivos)}")
            print(f"  Total: {len(files)}")

            return mis_archivos

        except Exception as e:
            print(f"[ERROR] {e}")
            return []

    def listar_archivos_paginado(self, folder_path=None, mostrar_todos=False, max_archivos=5000):
        """
        Listar archivos con PAGINACION para carpetas grandes.
        Obtiene archivos en lotes de 500 para evitar throttling.
        """
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return []

        folder = folder_path or CONFIG["dest_folder"]
        encoded_folder = urllib.parse.quote(folder, safe='/')

        mis_archivos = []
        otros_archivos = []
        skip = 0
        page_size = 500  # Lote seguro para evitar throttling
        total_obtenidos = 0

        print(f"\n{'='*60}")
        print(f"  LISTANDO (paginado): {folder}")
        print(f"{'='*60}")

        while total_obtenidos < max_archivos:
            # URL con paginacion
            url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files"
            url += f"?$expand=Author&$select=Name,Length,TimeCreated,Author/Email,Author/Title,ServerRelativeUrl"
            url += f"&$top={page_size}&$skip={skip}"

            try:
                print(f"\r  Obteniendo archivos {skip+1} - {skip+page_size}...", end="", flush=True)
                r = requests.get(url, headers=self.get_headers(), timeout=60)

                if r.status_code == 500:
                    # Throttling - reducir tamano de pagina
                    if page_size > 100:
                        page_size = page_size // 2
                        print(f"\n  [THROTTLE] Reduciendo a {page_size} por pagina...")
                        continue
                    else:
                        print(f"\n  [ERROR] Throttling persistente")
                        break

                if r.status_code != 200:
                    print(f"\n  [ERROR] {r.status_code}")
                    break

                data = r.json()
                files = data.get('d', {}).get('results', [])

                if not files:
                    # No hay mas archivos
                    break

                for f in files:
                    author_email = f.get('Author', {}).get('Email', '').lower()
                    author_name = f.get('Author', {}).get('Title', 'Desconocido')
                    es_mio = author_email == self.my_email

                    file_info = {
                        "nombre": f.get('Name'),
                        "tamano": f.get('Length', 0),
                        "creado": f.get('TimeCreated'),
                        "autor_email": author_email,
                        "autor_nombre": author_name,
                        "ruta": f.get('ServerRelativeUrl'),
                        "es_mio": es_mio
                    }

                    if es_mio:
                        mis_archivos.append(file_info)
                    else:
                        otros_archivos.append(file_info)

                total_obtenidos += len(files)
                skip += page_size

                # Si obtuvimos menos que page_size, no hay mas
                if len(files) < page_size:
                    break

            except Exception as e:
                print(f"\n  [ERROR] {e}")
                break

        print(f"\r  Obtenidos: {total_obtenidos} archivos" + " " * 20)

        # Mostrar resultados
        print(f"\n[MIS ARCHIVOS] ({len(mis_archivos)})")
        print("-" * 60)
        for f in mis_archivos[:30]:
            tamano_kb = int(f['tamano']) / 1024 if f['tamano'] else 0
            print(f"  {f['nombre'][:45]:<45} {tamano_kb:>8.1f} KB")
        if len(mis_archivos) > 30:
            print(f"  ... y {len(mis_archivos) - 30} mas")

        if mostrar_todos:
            print(f"\n[ARCHIVOS DE OTROS] ({len(otros_archivos)})")
            print("-" * 60)
            # Agrupar por autor
            por_autor = {}
            for f in otros_archivos:
                autor = f['autor_nombre']
                por_autor[autor] = por_autor.get(autor, 0) + 1
            for autor, count in sorted(por_autor.items(), key=lambda x: -x[1])[:10]:
                print(f"  {autor:<35} {count:>6} archivos")
            if len(por_autor) > 10:
                print(f"  ... y {len(por_autor) - 10} autores mas")

        print(f"\n[RESUMEN]")
        print(f"  Mis archivos: {len(mis_archivos)}")
        print(f"  De otros: {len(otros_archivos)}")
        print(f"  Total escaneados: {total_obtenidos}")

        return mis_archivos

    def contar_archivos(self, folder_path=None):
        """Contar archivos en una carpeta sin descargar todo"""
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return 0

        folder = folder_path or CONFIG["dest_folder"]
        encoded_folder = urllib.parse.quote(folder, safe='/')

        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/ItemCount"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code == 200:
                data = r.json()
                count = data.get('d', {}).get('ItemCount', 0)
                print(f"[INFO] {folder}: {count} items")
                return count
        except:
            pass

        return 0

    def listar_nombres_rapido(self, folder_path, max_archivos=1000):
        """
        Listar SOLO nombres de archivos (sin autor) - para carpetas muy grandes.
        No tiene el limite de 5000 items porque no usa $expand.
        """
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return []

        encoded_folder = urllib.parse.quote(folder_path, safe='/')

        archivos = []
        skip = 0
        page_size = 500

        print(f"\n{'='*60}")
        print(f"  LISTADO RAPIDO (sin autor): {folder_path}")
        print(f"{'='*60}")

        while len(archivos) < max_archivos:
            # URL simple sin $expand=Author
            url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Files"
            url += f"?$select=Name,Length,ServerRelativeUrl&$top={page_size}&$skip={skip}"

            try:
                print(f"\r  Obteniendo {skip+1} - {skip+page_size}...", end="", flush=True)
                r = requests.get(url, headers=self.get_headers(), timeout=60)

                if r.status_code != 200:
                    print(f"\n  [ERROR] {r.status_code}")
                    break

                data = r.json()
                files = data.get('d', {}).get('results', [])

                if not files:
                    break

                for f in files:
                    archivos.append({
                        "nombre": f.get('Name'),
                        "tamano": f.get('Length', 0),
                        "ruta": f.get('ServerRelativeUrl')
                    })

                skip += page_size

                if len(files) < page_size:
                    break

            except Exception as e:
                print(f"\n  [ERROR] {e}")
                break

        print(f"\r  Total: {len(archivos)} archivos" + " " * 20)

        # Mostrar muestra
        print(f"\n[ARCHIVOS] (primeros 30)")
        print("-" * 60)
        for f in archivos[:30]:
            tamano_kb = int(f['tamano']) / 1024 if f['tamano'] else 0
            print(f"  {f['nombre'][:45]:<45} {tamano_kb:>8.1f} KB")
        if len(archivos) > 30:
            print(f"  ... y {len(archivos) - 30} mas")

        print(f"\n[NOTA] Para verificar propiedad de un archivo especifico:")
        print(f"       Usa la opcion 6 del menu con la ruta completa")

        return archivos

    def listar_subcarpetas(self, folder_path=None):
        """Listar subcarpetas"""
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return []

        folder = folder_path or CONFIG["dest_folder"]
        encoded_folder = urllib.parse.quote(folder, safe='/')

        url = f"{CONFIG['base_url']}/GetFolderByServerRelativeUrl('{encoded_folder}')/Folders"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code != 200:
                print(f"[ERROR] {r.status_code}")
                return []

            data = r.json()
            folders = data.get('d', {}).get('results', [])

            print(f"\n[SUBCARPETAS en {folder}]")
            print("-" * 60)
            for f in folders:
                name = f.get('Name')
                if not name.startswith('_'):  # Ignorar carpetas de sistema
                    print(f"  📁 {name}")

            return [f.get('Name') for f in folders if not f.get('Name', '').startswith('_')]

        except Exception as e:
            print(f"[ERROR] {e}")
            return []

    def verificar_propiedad(self, file_path):
        """Verificar si un archivo es mio antes de cualquier operacion"""
        encoded_path = urllib.parse.quote(file_path, safe='/')
        url = f"{CONFIG['base_url']}/GetFileByServerRelativeUrl('{encoded_path}')"
        url += "?$expand=Author&$select=Name,Author/Email"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=30)
            if r.status_code != 200:
                return False, "Archivo no encontrado"

            data = r.json()
            author_email = data.get('d', {}).get('Author', {}).get('Email', '').lower()

            if author_email == self.my_email:
                return True, "Es tu archivo"
            else:
                return False, f"Archivo creado por: {author_email}"

        except Exception as e:
            return False, str(e)

    def eliminar_archivo(self, file_path, forzar=False):
        """
        Eliminar un archivo SOLO si es mio.
        Requiere confirmacion a menos que forzar=True
        """
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return False

        # Verificar propiedad
        es_mio, mensaje = self.verificar_propiedad(file_path)

        if not es_mio:
            print(f"[BLOQUEADO] No puedes eliminar este archivo")
            print(f"  Razon: {mensaje}")
            return False

        file_name = file_path.split('/')[-1]

        if not forzar:
            print(f"\n[CONFIRMAR] Eliminar: {file_name}")
            confirm = input("  Escribir 'SI' para confirmar: ")
            if confirm != 'SI':
                print("  Cancelado")
                return False

        encoded_path = urllib.parse.quote(file_path, safe='/')
        url = f"{CONFIG['base_url']}/GetFileByServerRelativeUrl('{encoded_path}')"

        headers = self.get_headers()
        headers["X-HTTP-Method"] = "DELETE"
        headers["If-Match"] = "*"

        try:
            r = requests.post(url, headers=headers, timeout=30)
            if r.status_code in [200, 204]:
                print(f"[OK] Eliminado: {file_name}")
                return True
            else:
                print(f"[ERROR] {r.status_code}: {r.text[:200]}")
                return False
        except Exception as e:
            print(f"[ERROR] {e}")
            return False

    def descargar_archivo(self, file_path, destino_local=None):
        """Descargar un archivo (no requiere ser el autor)"""
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return False

        encoded_path = urllib.parse.quote(file_path, safe='/')
        url = f"{CONFIG['base_url']}/GetFileByServerRelativeUrl('{encoded_path}')/$value"

        try:
            r = requests.get(url, headers=self.get_headers(), timeout=120)
            if r.status_code != 200:
                print(f"[ERROR] {r.status_code}")
                return False

            file_name = file_path.split('/')[-1]
            destino = destino_local or f"/tmp/{file_name}"

            with open(destino, 'wb') as f:
                f.write(r.content)

            print(f"[OK] Descargado: {destino}")
            return True

        except Exception as e:
            print(f"[ERROR] {e}")
            return False

    def mover_archivo(self, file_path, nueva_carpeta):
        """Mover un archivo SOLO si es mio"""
        if not self.token_valid():
            print("[ERROR] Token expirado")
            return False

        # Verificar propiedad
        es_mio, mensaje = self.verificar_propiedad(file_path)

        if not es_mio:
            print(f"[BLOQUEADO] No puedes mover este archivo")
            print(f"  Razon: {mensaje}")
            return False

        file_name = file_path.split('/')[-1]
        new_path = f"{nueva_carpeta}/{file_name}"

        encoded_path = urllib.parse.quote(file_path, safe='/')
        encoded_new = urllib.parse.quote(new_path, safe='/')

        url = f"{CONFIG['base_url']}/GetFileByServerRelativeUrl('{encoded_path}')/MoveTo(newurl='{encoded_new}',flags=1)"

        try:
            r = requests.post(url, headers=self.get_headers(), timeout=30)
            if r.status_code in [200, 204]:
                print(f"[OK] Movido a: {nueva_carpeta}")
                return True
            else:
                print(f"[ERROR] {r.status_code}: {r.text[:200]}")
                return False
        except Exception as e:
            print(f"[ERROR] {e}")
            return False


def menu_interactivo():
    """Menu interactivo para gestionar archivos"""
    ma = MisArchivos()

    if not ma.token_valid():
        print("[ERROR] Token no valido. Actualiza el token primero.")
        return

    while True:
        print(f"\n{'='*60}")
        print("  GESTOR DE MIS ARCHIVOS EN ONEDRIVE")
        print(f"  Usuario: {ma.my_email}")
        print(f"{'='*60}")
        print()
        print("  1. Listar mis archivos (carpetas pequenas)")
        print("  2. Listar subcarpetas")
        print("  3. Listar todos los archivos (incluyendo de otros)")
        print("  4. Descargar un archivo")
        print("  5. Eliminar un archivo (solo mios)")
        print("  6. Verificar propiedad de un archivo")
        print("  7. Listar carpeta GRANDE (paginado)")
        print("  8. Contar archivos en carpeta")
        print("  9. Listado RAPIDO (solo nombres, sin autor)")
        print("  0. Salir")
        print()

        opcion = input("  Opcion: ").strip()

        if opcion == '1':
            folder = input("  Carpeta (Enter para raiz): ").strip()
            ma.listar_archivos(folder or None)

        elif opcion == '2':
            folder = input("  Carpeta (Enter para raiz): ").strip()
            ma.listar_subcarpetas(folder or None)

        elif opcion == '3':
            folder = input("  Carpeta (Enter para raiz): ").strip()
            ma.listar_archivos(folder or None, mostrar_todos=True)

        elif opcion == '4':
            ruta = input("  Ruta completa del archivo: ").strip()
            if ruta:
                ma.descargar_archivo(ruta)

        elif opcion == '5':
            ruta = input("  Ruta completa del archivo a eliminar: ").strip()
            if ruta:
                ma.eliminar_archivo(ruta)

        elif opcion == '6':
            ruta = input("  Ruta completa del archivo: ").strip()
            if ruta:
                es_mio, msg = ma.verificar_propiedad(ruta)
                print(f"  {'[TUYO]' if es_mio else '[DE OTRO]'} {msg}")

        elif opcion == '7':
            folder = input("  Carpeta grande: ").strip()
            if folder:
                mostrar = input("  Mostrar archivos de otros? (s/N): ").strip().lower() == 's'
                max_arch = input("  Max archivos a escanear [5000]: ").strip()
                max_arch = int(max_arch) if max_arch else 5000
                ma.listar_archivos_paginado(folder, mostrar_todos=mostrar, max_archivos=max_arch)

        elif opcion == '8':
            folder = input("  Carpeta: ").strip()
            if folder:
                ma.contar_archivos(folder)

        elif opcion == '9':
            folder = input("  Carpeta: ").strip()
            if folder:
                max_arch = input("  Max archivos [1000]: ").strip()
                max_arch = int(max_arch) if max_arch else 1000
                ma.listar_nombres_rapido(folder, max_archivos=max_arch)

        elif opcion == '0':
            print("  Hasta luego!")
            break

        else:
            print("  Opcion no valida")


if __name__ == "__main__":
    menu_interactivo()
