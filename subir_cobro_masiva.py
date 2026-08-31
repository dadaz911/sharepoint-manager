#!/usr/bin/env python3
"""
Subida paralela de PDFs a SharePoint - Cobro Tributario No Cobrable
Destino: P 1 MASIVA / {documento} / archivos

Uso:
    python3 subir_cobro_masiva.py            # Subir todo
    python3 subir_cobro_masiva.py --test 5   # Test con 5 archivos
    python3 subir_cobro_masiva.py --refresh-cookies  # Solo refrescar cookies
"""

import json
import sys
import time
import argparse
import threading
import urllib.parse
import requests
import websocket
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── CONFIG ────────────────────────────────────────────────────────────────────
SOURCE_DIR = Path("/home/daniel/Desktop/cobro tributario No cobrable")
SP_BASE    = "https://shdgov.sharepoint.com/sites/OficinadeDepuracindeCartera/_api/web"
SP_FOLDER  = "/sites/OficinadeDepuracindeCartera/Documentos compartidos/Procesos ODC 2026/5 Gestion de Depuracion Dificil Cobro 2026/P 1 MASIVA/cobro tributario No cobrable"
CDP_PORT   = 9222
THREADS    = 4
COOKIES_FILE  = Path("/tmp/sp_cookies.json")
DIGEST_FILE   = Path("/tmp/sp_digest.txt")
PROGRESS_FILE = SOURCE_DIR / ".upload_progress_masiva.json"
# ─────────────────────────────────────────────────────────────────────────────

lock = threading.Lock()
uploaded = 0
errors   = 0
skipped  = 0


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def refresh_from_browser():
    """Fuerza reload en Chrome, espera, extrae cookies frescas y nuevo digest."""
    try:
        tabs = requests.get(f"http://localhost:{CDP_PORT}/json", timeout=5).json()
        sp_tab = next((t for t in tabs if "shdgov.sharepoint.com" in t.get("url", "")), None)
        if not sp_tab:
            log("⚠️  No hay pestaña SharePoint en Chrome")
            return False

        ws = websocket.create_connection(
            sp_tab["webSocketDebuggerUrl"], timeout=15,
            header={"Origin": f"http://localhost:{CDP_PORT}"}
        )

        # Forzar reload para renovar FedAuth/rtFa en el browser
        ws.send(json.dumps({"id": 1, "method": "Page.reload", "params": {"ignoreCache": False}}))
        json.loads(ws.recv())  # ack

        # Esperar a que la página cargue (Page.loadEventFired)
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        json.loads(ws.recv())
        deadline = time.time() + 20
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("method") == "Page.loadEventFired":
                break

        # Extraer cookies frescas
        ws.send(json.dumps({"id": 3, "method": "Network.getCookies",
                            "params": {"urls": ["https://shdgov.sharepoint.com"]}}))
        r = json.loads(ws.recv())
        cookies = {c["name"]: c["value"] for c in r["result"]["cookies"]}
        COOKIES_FILE.write_text(json.dumps(cookies))
        ws.close()

        # Digest fresco via API con cookies nuevas
        s = requests.Session()
        s.cookies.update(cookies)
        resp = s.post(
            "https://shdgov.sharepoint.com/sites/OficinadeDepuracindeCartera/_api/contextinfo",
            headers={"Accept": "application/json;odata=verbose"},
            timeout=15
        )
        digest = resp.json()["d"]["GetContextWebInformation"]["FormDigestValue"]
        DIGEST_FILE.write_text(digest)
        log("✅ Cookies y digest refrescados (reload forzado)")
        return True
    except Exception as e:
        log(f"❌ Error al refrescar: {e}")
        return False


def get_session():
    cookies = json.loads(COOKIES_FILE.read_text())
    s = requests.Session()
    s.cookies.update(cookies)
    s.headers.update({"Accept": "application/json;odata=verbose"})
    return s


def get_digest():
    return DIGEST_FILE.read_text().strip()


def ensure_folder(session, folder_rel_url):
    """Crea la carpeta en SharePoint si no existe."""
    url = f"{SP_BASE}/Folders/add('{urllib.parse.quote(folder_rel_url)}')"
    r = session.post(url, headers={"X-RequestDigest": get_digest()})
    # 200 = creada, 500 con "already exists" = ok
    if r.status_code in (200, 201):
        return True
    if "already exists" in r.text.lower() or r.status_code == 409:
        return True
    log(f"⚠️  Folder create {r.status_code}: {r.text[:200]}")
    return False


def upload_file(session, local_path: Path, dest_folder_rel: str, progress: dict):
    global uploaded, errors, skipped

    rel = str(local_path.relative_to(SOURCE_DIR))
    if rel in progress.get("done", set()):
        with lock:
            skipped += 1
        return "skip"

    filename = local_path.name
    url = (f"{SP_BASE}/GetFolderByServerRelativeUrl"
           f"('{urllib.parse.quote(dest_folder_rel)}')"
           f"/Files/add(url='{urllib.parse.quote(filename)}',overwrite=true)")

    try:
        data = local_path.read_bytes()
        r = session.post(
            url,
            headers={
                "X-RequestDigest": get_digest(),
                "Content-Type": "application/octet-stream",
            },
            data=data,
            timeout=120
        )
        if r.status_code in (200, 201):
            with lock:
                uploaded += 1
                progress["done"].add(rel)
            return "ok"
        else:
            with lock:
                errors += 1
                progress["errors"].append({"file": rel, "status": r.status_code, "msg": r.text[:200]})
            log(f"❌ {filename}: {r.status_code} {r.text[:100]}")
            return "error"
    except Exception as e:
        with lock:
            errors += 1
            progress["errors"].append({"file": rel, "error": str(e)})
        log(f"❌ {filename}: {e}")
        return "error"


def save_progress(progress: dict):
    data = {"done": list(progress["done"]), "errors": progress["errors"]}
    PROGRESS_FILE.write_text(json.dumps(data))


def load_progress():
    if PROGRESS_FILE.exists():
        d = json.loads(PROGRESS_FILE.read_text())
        d["done"] = set(d.get("done", []))
        return d
    return {"done": set(), "errors": []}


def collect_files(limit=None):
    files = []
    for doc_dir in sorted(SOURCE_DIR.iterdir()):
        if not doc_dir.is_dir() or doc_dir.name.startswith("."):
            continue
        for f in sorted(doc_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                files.append(f)
        if limit and len(files) >= limit:
            files = files[:limit]
            break
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=int, help="Subir solo N archivos para prueba")
    parser.add_argument("--refresh-cookies", action="store_true")
    parser.add_argument("--threads", type=int, default=THREADS)
    args = parser.parse_args()

    if args.refresh_cookies:
        refresh_from_browser()
        return

    if not COOKIES_FILE.exists() or not DIGEST_FILE.exists():
        log("No hay cookies/digest. Refrescando desde Chrome...")
        if not refresh_from_browser():
            log("❌ No se pudo obtener autenticación. Asegúrate de que Chrome esté abierto.")
            sys.exit(1)

    progress = load_progress()
    files = collect_files(limit=args.test)
    total = len(files)
    log(f"📁 Archivos a subir: {total} | Ya subidos antes: {len(progress['done'])}")

    # Crear subcarpetas únicas
    doc_dirs_needed = set()
    for f in files:
        rel = str(f.relative_to(SOURCE_DIR))
        if rel not in progress["done"]:
            doc_dirs_needed.add(f.parent.name)

    if doc_dirs_needed:
        log(f"📂 Creando carpeta raíz + {len(doc_dirs_needed)} subcarpetas...")
        session = get_session()
        # Crear primero la carpeta raíz (cobro tributario No cobrable)
        ensure_folder(session, SP_FOLDER)
        for doc in sorted(doc_dirs_needed):
            folder_rel = f"{SP_FOLDER}/{doc}"
            ensure_folder(session, folder_rel)

    log(f"🚀 Iniciando subida con {args.threads} hilos...")
    start = time.time()
    last_save = time.time()
    last_refresh = time.time()

    session = get_session()

    def worker(f: Path):
        nonlocal session
        doc_name = f.parent.name
        dest = f"{SP_FOLDER}/{doc_name}"
        return upload_file(session, f, dest, progress)

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(worker, f): f for f in files}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1

            # Refrescar cookies y digest cada 15 min (FedAuth expira ~1h)
            if time.time() - last_refresh > 900:
                log("🔄 Refrescando cookies y digest...")
                refresh_from_browser()
                session = get_session()
                last_refresh = time.time()

            # Guardar progreso cada 50 archivos
            if done_count % 50 == 0 or time.time() - last_save > 60:
                save_progress(progress)
                last_save = time.time()
                elapsed = time.time() - start
                rate = (uploaded + skipped) / elapsed if elapsed > 0 else 0
                remaining = (total - done_count) / rate if rate > 0 else 0
                log(f"📊 {done_count}/{total} | ✅{uploaded} ⏭️{skipped} ❌{errors} | "
                    f"{rate:.1f}/s | ETA {remaining/60:.0f}min")

    save_progress(progress)
    elapsed = time.time() - start
    log(f"")
    log(f"✅ COMPLETADO en {elapsed/60:.1f} min")
    log(f"   Subidos: {uploaded} | Omitidos: {skipped} | Errores: {errors}")
    if errors:
        log(f"   Ver errores en: {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
