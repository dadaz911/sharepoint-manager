#!/usr/bin/env python3
"""
consolidar_masivo.py — Consolida en SharePoint las carpetas partidas por familia documental
en UNA carpeta por sujeto (la del tipo real), moviendo archivos SERVER-SIDE (MoveTo: sin
transferir datos) y borrando las carpetas sobrantes.

Regla: agrupa por número de identificación normalizado (sin ceros). La carpeta CANÓNICA es la
de tipo real (CC/NIT/CE/PA/TI) sin ceros a la izquierda. Las demás (ID-, variantes con ceros)
se vacían hacia la canónica (overwrite) y se borran. Nombres malformados o sin carpeta de tipo
real se SALTAN (se listan para revisión manual).

Endurecido: back-off Retry-After (429/503), relee token en 401, resumible, log de errores.
Lee la estructura del SOURCE local (== SharePoint, verificado 1:1) para armar el plan; ejecuta
MoveTo/Delete contra SharePoint.

Env: SPM_BASE_URL, SPM_DEST_FOLDER, SPM_SOURCE_DIR, SPM_TOKEN_FILE, SPM_THREADS (def 5),
     SPM_PROGRESS, SPM_ONLY (procesa solo esa carpeta no-canónica; para validar 1 caso),
     SPM_DRYRUN (1 = solo muestra el plan, no toca nada)
"""

import collections
import json
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = os.environ["SPM_BASE_URL"].rstrip("/")
DEST = os.environ["SPM_DEST_FOLDER"].rstrip("/")
SRC = os.environ["SPM_SOURCE_DIR"]
TOKEN_FILE = os.environ["SPM_TOKEN_FILE"]
THREADS = int(os.environ.get("SPM_THREADS", "5"))
PROGRESS = os.environ.get("SPM_PROGRESS", os.path.join(SRC, ".consolidate_progress.json"))
ONLY = os.environ.get("SPM_ONLY", "")
DRYRUN = os.environ.get("SPM_DRYRUN", "") == "1"
REAL = {"CC", "NIT", "CE", "PA", "TI"}
MAX_RETRIES = int(os.environ.get("SPM_MAX_RETRIES", "15"))  # subido de 8: reduce los 'noresp' por throttling
HOST = "https://" + BASE.split("/")[2]  # https://shdgov.sharepoint.com
SITE = BASE.rsplit("/web", 1)[0]  # .../_api
MOVE_EP = SITE + "/SP.MoveCopyUtil.MoveFile"  # rutas en el cuerpo -> evita el límite de URL

_lock = threading.Lock()
_tok = {"v": None}
_pause = {"t": 0.0}
_done = set()
_stats = collections.Counter()
_errlog = None


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_token():
    with _lock:
        _tok["v"] = open(TOKEN_FILE).read().strip()


def norm(num):
    return num.strip().lstrip("0") or "0"


def enc(p):
    return urllib.parse.quote(p, safe="/")


def _req(s, method, url, **kw):
    for a in range(MAX_RETRIES):
        with _lock:
            w = _pause["t"] - time.time()
        if w > 0:
            time.sleep(min(w, 5))
        h = kw.pop("headers", {})
        h["Authorization"] = f"Bearer {_tok['v']}"
        try:
            r = s.request(method, url, headers=h, timeout=60, **kw)
        except requests.RequestException:
            time.sleep(min(2**a, 30))
            kw["headers"] = h
            continue
        if r.status_code in (429, 503):
            ra = r.headers.get("Retry-After")
            secs = int(ra) if ra and ra.isdigit() else 30
            with _lock:
                _pause["t"] = max(_pause["t"], time.time() + secs + a * 5)
            kw["headers"] = h
            continue
        if r.status_code == 401:
            load_token()
            time.sleep(2)
            kw["headers"] = h
            continue
        return r
    return None


def build_plan():
    num2 = collections.defaultdict(list)
    skipped = []
    for name in os.listdir(SRC):
        if not os.path.isdir(os.path.join(SRC, name)):
            continue
        m = re.match(r"^([A-Za-z]+)-(.+)$", name)
        if not m or not (m.group(2).lstrip("0") or "0").isdigit():
            skipped.append(name)
            continue
        num2[norm(m.group(2))].append((name, m.group(1)))
    plan = []
    for n, folders in num2.items():
        reals = [(nm, pre) for nm, pre in folders if pre in REAL]
        if not reals:
            skipped += [f"{nm} (sin canónica)" for nm, _ in folders]
            continue
        # canónica: tipo real, preferir el nombre sin ceros a la izquierda, luego el más corto
        reals.sort(key=lambda x: (x[0].split("-", 1)[1] != x[0].split("-", 1)[1].lstrip("0"), len(x[0])))
        canon = reals[0][0]
        for nm, _ in folders:
            if nm == canon:
                continue
            files = [f for f in os.listdir(os.path.join(SRC, nm)) if os.path.isfile(os.path.join(SRC, nm, f))]
            plan.append((nm, canon, files))
    return plan, skipped


def _file_exists(s, rel):
    url = f"{BASE}/GetFileByServerRelativeUrl('{enc(rel)}')?$select=Name"
    r = _req(s, "GET", url, headers={"Accept": "application/json;odata=nometadata"})
    return r is not None and r.status_code == 200


def process(s, noncanon, canon, files):
    for f in files:
        src_rel = f"{DEST}/{noncanon}/{f}"
        dst_rel = f"{DEST}/{canon}/{f}"
        body = {"srcUrl": HOST + src_rel, "destUrl": HOST + dst_rel}
        r = _req(
            s,
            "POST",
            MOVE_EP,
            json=body,
            headers={
                "Accept": "application/json;odata=nometadata",
                "Content-Type": "application/json;odata=nometadata",
            },
        )
        if r is not None and r.status_code == 200:
            with _lock:
                _stats["moved"] += 1
            continue
        # sin 200: ¿el archivo ya está en destino? (re-run o nombre redundante) -> OK
        if _file_exists(s, dst_rel):
            with _lock:
                _stats["already"] += 1
            continue
        code = r.status_code if r is not None else "noresp"
        _errlog.write(f"move_fail\t{noncanon}/{f}\t{code}\t{(r.text[:120] if r is not None else '')}\n")
        _errlog.flush()
        with _lock:
            _stats["err"] += 1
        return False
    # borrar carpeta vacía (Content-Length:0 via data=b'' -> evita el 411)
    url = f"{BASE}/GetFolderByServerRelativeUrl('{enc(DEST + '/' + noncanon)}')"
    r = _req(s, "POST", url, data=b"", headers={"X-HTTP-Method": "DELETE", "IF-MATCH": "*"})
    if r is None or r.status_code not in (200, 204):
        code = r.status_code if r is not None else "noresp"
        _errlog.write(f"del_fail\t{noncanon}\t{code}\n")
        _errlog.flush()
        with _lock:
            _stats["err"] += 1
        return False
    with _lock:
        _stats["folders"] += 1
        _done.add(noncanon)
    return True


def save_progress():
    with _lock:
        data = sorted(_done)
    tmp = PROGRESS + ".tmp"
    open(tmp, "w").write(json.dumps({"done": data}))
    os.replace(tmp, PROGRESS)


def main():
    load_token()
    if os.path.exists(PROGRESS):
        try:
            _done.update(json.load(open(PROGRESS)).get("done", []))
        except Exception:
            pass
    plan, skipped = build_plan()
    if ONLY:
        plan = [x for x in plan if x[0] == ONLY]
        log(f"SPM_ONLY={ONLY} -> {len(plan)} carpeta(s)")
    pending = [x for x in plan if x[0] not in _done]
    log(f"Plan: {len(plan)} no-canónicas | pendientes: {len(pending)} | saltadas (manual): {len(skipped)}")
    if DRYRUN:
        for nc, cn, fs in pending[:10]:
            log(f"  DRYRUN  {nc}  ->  {cn}   ({len(fs)} archivos)")
        log(f"(dry-run; saltadas ej.: {skipped[:3]})")
        return
    global _errlog
    _errlog = open(os.path.join(SRC, ".consolidate_errors.log"), "a")
    if not pending:
        log("nada pendiente")
        return
    tl = threading.local()

    def work(item):
        s = getattr(tl, "s", None)
        if s is None:
            s = requests.Session()
            tl.s = s
        process(s, *item)
        with _lock:
            n = _stats["folders"]
        if n and n % 200 == 0:
            save_progress()

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        list(ex.map(work, pending))
    save_progress()
    log(
        f"FIN. carpetas consolidadas={_stats['folders']} | archivos movidos={_stats['moved']} | errores={_stats['err']}"
    )


if __name__ == "__main__":
    main()
