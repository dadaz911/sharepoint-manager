#!/usr/bin/env python3
"""
subir_masivo.py — Uploader masivo y robusto para SharePoint (sitio de equipo).

Endurecido para migraciones grandes (cientos de miles de archivos / decenas de GB):
- Throttling: honra `Retry-After` (429/503) con back-off compartido entre hilos + jitter.
- Token: relee SPM_TOKEN_FILE en 401 (lo mantiene fresco un proceso externo: token_refresher).
- Resume: progress.json (set de rutas ya subidas), guardado atómico periódico + al salir/señal.
- Carpetas: crea la jerarquía bajo el destino de forma idempotente y cacheada (throttle-aware);
  en resume precarga las carpetas de archivos ya subidos para no recrearlas.
- Whitelist ESTRICTA de extensiones (.pdf/.png por defecto): NUNCA sube otros tipos.
- Concurrencia moderada (a esta escala, menos hilos + back-off rinden más que muchos hilos).
- Sube el CONTENIDO de SPM_SOURCE_DIR (no la carpeta raíz), preservando subcarpetas.

Config por entorno (todas requeridas salvo las que tienen default):
  SPM_BASE_URL    https://shdgov.sharepoint.com/sites/<sitio>/_api/web
  SPM_DEST_FOLDER /sites/<sitio>/Documentos compartidos/<.../carpeta destino>  (server-relative)
  SPM_SOURCE_DIR  carpeta local; se sube su contenido
  SPM_TOKEN_FILE  archivo con el Bearer token (lo refresca otro proceso)
  SPM_THREADS     hilos (default 5; en el tenant SHD 6 dispara throttling, 5 es el óptimo)
  SPM_EXTENSIONS  ".pdf,.png" (default; case-insensitive)
  SPM_PROGRESS    ruta del progress.json (default: <source>/.upload_progress.json)
"""

import json
import os
import signal
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE_URL = os.environ["SPM_BASE_URL"].rstrip("/")
DEST_FOLDER = os.environ["SPM_DEST_FOLDER"].rstrip("/")
SOURCE_DIR = Path(os.environ["SPM_SOURCE_DIR"])
TOKEN_FILE = Path(os.environ["SPM_TOKEN_FILE"])
THREADS = int(os.environ.get("SPM_THREADS", "5"))  # 5 = punto óptimo observado en SHD (6 throttlea)
EXTS = tuple(e.strip().lower() for e in os.environ.get("SPM_EXTENSIONS", ".pdf,.png").split(","))
PROGRESS = Path(os.environ.get("SPM_PROGRESS", str(SOURCE_DIR / ".upload_progress.json")))

MAX_RETRIES = 8
SAVE_EVERY = 200

# ── Estado compartido ─────────────────────────────────────────────────────────
_lock = threading.Lock()
_uploaded = set()  # rutas relativas ya subidas (resume)
_known_dirs = set()  # server-relative URLs de carpetas que ya existen
_token = {"v": None}
_pause_until = {"t": 0.0}  # back-off global por throttling
_stats = {"ok": 0, "skip": 0, "err": 0, "since_save": 0}
_stop = threading.Event()
_errors_log = None


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_token():
    with _lock:
        try:
            _token["v"] = TOKEN_FILE.read_text().strip()
        except Exception as e:
            log(f"WARN: no pude leer el token: {e}")
    return _token["v"]


def token():
    return _token["v"]


def _respect_pause():
    while True:
        with _lock:
            wait = _pause_until["t"] - time.time()
        if wait <= 0 or _stop.is_set():
            return
        time.sleep(min(wait, 5))


def _throttle(resp):
    """Devuelve segundos a esperar segun Retry-After (o back-off por defecto)."""
    ra = resp.headers.get("Retry-After")
    secs = 0
    if ra:
        try:
            secs = int(ra)
        except ValueError:
            secs = 30
    return max(secs, 10)


def _set_pause(secs):
    with _lock:
        _pause_until["t"] = max(_pause_until["t"], time.time() + secs)


def _req(session, method, url, **kw):
    """Request con manejo de 429/503 (Retry-After), 401 (reload token), reintentos."""
    for attempt in range(MAX_RETRIES):
        if _stop.is_set():
            return None
        _respect_pause()
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {token()}"
        try:
            r = session.request(method, url, headers=headers, timeout=120, **kw)
        except requests.RequestException:
            time.sleep(min(2**attempt, 30))
            kw["headers"] = headers
            continue
        if r.status_code in (429, 503):
            secs = _throttle(r)
            _set_pause(secs + attempt * 5)
            kw["headers"] = headers
            continue
        if r.status_code == 401:
            load_token()  # el token externo se refresca solo
            time.sleep(2)
            kw["headers"] = headers
            continue
        kw["headers"] = headers
        return r
    return None


def ensure_folder(session, rel_dir: str):
    """Crea (idempotente) la jerarquia de carpetas bajo DEST_FOLDER para rel_dir (posix)."""
    if not rel_dir or rel_dir == ".":
        return True
    parts = [p for p in rel_dir.split("/") if p and p != "."]
    cur = DEST_FOLDER
    for part in parts:
        cur = f"{cur}/{part}"
        with _lock:
            if cur in _known_dirs:
                continue
        enc = urllib.parse.quote(cur, safe="/")
        url = f"{BASE_URL}/folders/add('{enc}')"
        r = _req(session, "POST", url, headers={"Accept": "application/json;odata=nometadata"})
        if r is None:
            return False
        # 200/201 = creada; si ya existe SPO devuelve error -> lo tratamos como OK
        if r.status_code in (200, 201, 409) or "already exists" in r.text.lower():
            with _lock:
                _known_dirs.add(cur)
        else:
            # Reintento de verificacion: si existe, seguir
            chk = _req(
                session,
                "GET",
                f"{BASE_URL}/GetFolderByServerRelativeUrl('{enc}')?$select=Exists",
                headers={"Accept": "application/json;odata=nometadata"},
            )
            if chk is not None and chk.status_code == 200:
                with _lock:
                    _known_dirs.add(cur)
            else:
                return False
    return True


def upload_one(session, file_path: Path):
    rel = file_path.relative_to(SOURCE_DIR).as_posix()
    with _lock:
        if rel in _uploaded:
            _stats["skip"] += 1
            return
    rel_dir = file_path.parent.relative_to(SOURCE_DIR).as_posix()
    if not ensure_folder(session, rel_dir):
        with _lock:
            _stats["err"] += 1
        _errors_log.write(f"folder_fail\t{rel}\n")
        _errors_log.flush()
        return

    server_folder = DEST_FOLDER if rel_dir in ("", ".") else f"{DEST_FOLDER}/{rel_dir}"
    enc_folder = urllib.parse.quote(server_folder, safe="/")
    enc_name = urllib.parse.quote(file_path.name)
    url = f"{BASE_URL}/GetFolderByServerRelativeUrl('{enc_folder}')/Files/add(url='{enc_name}',overwrite=true)"
    try:
        data = file_path.read_bytes()
    except Exception as e:
        with _lock:
            _stats["err"] += 1
        _errors_log.write(f"read_fail\t{rel}\t{e}\n")
        _errors_log.flush()
        return
    r = _req(
        session,
        "POST",
        url,
        data=data,
        headers={"Accept": "application/json;odata=nometadata", "Content-Type": "application/octet-stream"},
    )
    if r is not None and r.status_code in (200, 201):
        with _lock:
            _uploaded.add(rel)
            _stats["ok"] += 1
            _stats["since_save"] += 1
            need_save = _stats["since_save"] >= SAVE_EVERY
        if need_save:
            save_progress()
    else:
        code = r.status_code if r is not None else "noresp"
        with _lock:
            _stats["err"] += 1
        _errors_log.write(f"upload_fail\t{rel}\t{code}\n")
        _errors_log.flush()


def save_progress():
    with _lock:
        data = sorted(_uploaded)
        _stats["since_save"] = 0
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"uploaded": data}))
    os.replace(tmp, PROGRESS)


def load_progress():
    if PROGRESS.exists():
        try:
            data = json.loads(PROGRESS.read_text())
            up = set(data.get("uploaded", []))
            _uploaded.update(up)
            # precargar carpetas de archivos ya subidos (existen) para no recrearlas
            for rel in up:
                d = str(Path(rel).parent.as_posix())
                cur = DEST_FOLDER
                if d not in ("", "."):
                    for part in d.split("/"):
                        cur = f"{cur}/{part}"
                        _known_dirs.add(cur)
        except Exception as e:
            log(f"WARN: progress ilegible: {e}")


def enumerate_files():
    for p in SOURCE_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS:
            yield p


def main():
    if not SOURCE_DIR.is_dir():
        log(f"ERROR: SPM_SOURCE_DIR no existe: {SOURCE_DIR}")
        sys.exit(1)
    load_token()
    if not token():
        log("ERROR: token vacio")
        sys.exit(1)
    load_progress()

    def _sig(*_):
        log("Señal recibida — guardando progreso y saliendo...")
        _stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    global _errors_log
    _errors_log = open(SOURCE_DIR / ".upload_errors.log", "a")

    log(f"Origen: {SOURCE_DIR}")
    log(f"Destino: {DEST_FOLDER}")
    log(f"Extensiones: {EXTS} | hilos: {THREADS} | ya subidos (resume): {len(_uploaded)}")
    log("Enumerando archivos (puede tardar)...")
    files = [p for p in enumerate_files()]
    pending = [p for p in files if p.relative_to(SOURCE_DIR).as_posix() not in _uploaded]
    total = len(files)
    log(f"Total {EXTS}: {total} | pendientes: {len(pending)}")
    limit = int(os.environ.get("SPM_LIMIT", "0"))
    if limit > 0:
        pending = pending[:limit]
        log(f"SPM_LIMIT={limit} -> corriendo solo {len(pending)} archivos (prueba)")
    if not pending:
        log("Nada pendiente. Listo.")
        return

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        _tl = threading.local()

        def work(p):
            s = getattr(_tl, "s", None)
            if s is None:
                s = requests.Session()
                _tl.s = s
            upload_one(s, p)

        futs = [ex.submit(work, p) for p in pending]
        done = 0
        for _ in as_completed(futs):
            done += 1
            if done % 250 == 0 or _stop.is_set():
                with _lock:
                    ok, sk, er = _stats["ok"], _stats["skip"], _stats["err"]
                rate = ok / max(time.time() - t0, 1)
                eta = (len(pending) - done) / max(rate, 0.01) / 3600
                log(f"  {done}/{len(pending)} | ok={ok} err={er} | {rate:.1f}/s | ETA ~{eta:.1f}h")
                save_progress()
            if _stop.is_set():
                break

    save_progress()
    with _lock:
        log(f"FIN. ok={_stats['ok']} err={_stats['err']} skip={_stats['skip']} | progreso: {PROGRESS}")
    _errors_log.close()


if __name__ == "__main__":
    main()
