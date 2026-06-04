#!/usr/bin/env python3
"""
cleanup-redundant.py — Envía a la PAPELERA (recuperable) las carpetas NIT con ceros a la
izquierda que son 100% redundantes respecto a su canónica NIT-<n>.

SEGURIDAD (doble verificación + recuperable):
  1) Local: cada archivo de la carpeta con ceros está presente y del MISMO TAMAÑO en la canónica.
  2) Live en SharePoint: re-verifica vía API que cada archivo está en la canónica con el mismo Length.
  Solo si AMBAS pasan -> recycle() (papelera, recuperable). Cualquier duda -> SKIP + log.
  NUNCA toca la carpeta canónica (solo recicla la variante 'NIT-0+<n>').

Env: SPM_BASE_URL, SPM_DEST_FOLDER, SPM_SOURCE_DIR, SPM_TOKEN_FILE, DRY (1 = solo previsualiza)
"""

import os
import re
import urllib.parse

import requests

BASE = os.environ["SPM_BASE_URL"].rstrip("/")
DEST = os.environ["SPM_DEST_FOLDER"].rstrip("/")
SRC = os.environ["SPM_SOURCE_DIR"]
with open(os.environ["SPM_TOKEN_FILE"]) as _tf:
    TOK = _tf.read().strip()
DRY = os.environ.get("DRY", "") == "1"
H = {"Authorization": f"Bearer {TOK}", "Accept": "application/json;odata=nometadata"}


def enc(p):
    return urllib.parse.quote(p, safe="/")


def sp_files(folder):
    """{nombre: Length} de la carpeta en SharePoint; None si no se pudo listar/404."""
    url = f"{BASE}/GetFolderByServerRelativeUrl('{enc(DEST + '/' + folder)}')/Files?$select=Name,Length"
    r = requests.get(url, headers=H, timeout=30)
    if r.status_code != 200:
        return None
    return {x["Name"]: int(x["Length"]) for x in r.json().get("value", [])}


def recycle(folder):
    url = f"{BASE}/GetFolderByServerRelativeUrl('{enc(DEST + '/' + folder)}')/recycle"
    return requests.post(url, headers={**H, "Content-Length": "0"}, data=b"", timeout=60)


def recycle_file(folder, fname):
    rel = f"{DEST}/{folder}/{fname}"
    url = f"{BASE}/GetFileByServerRelativeUrl('{enc(rel)}')/recycle"
    return requests.post(url, headers={**H, "Content-Length": "0"}, data=b"", timeout=60)


def localfiles(d):
    dd = os.path.join(SRC, d)
    return {f: os.path.getsize(os.path.join(dd, f)) for f in os.listdir(dd) if os.path.isfile(os.path.join(dd, f))}


# Candidatas: 'NIT-0+<n>' con canónica 'NIT-<n>' y redundancia LOCAL total.
dirs = {d for d in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, d))}
cands = []
for p in dirs:
    if not re.match(r'^NIT-0+\d', p):
        continue
    num = p.split("-", 1)[1].lstrip("0")
    canon = f"NIT-{num}"
    if canon not in dirs:
        continue
    pf, cf = localfiles(p), localfiles(canon)
    if pf and all(f in cf and cf[f] == pf[f] for f in pf):
        cands.append((p, canon))
print(f"Candidatas (NIT-ceros, redundancia LOCAL 100%): {len(cands)}")

recycled = 0
skipped = []
for p, canon in sorted(cands):
    pf = sp_files(p)
    cf = sp_files(canon)
    if pf is None or cf is None:
        skipped.append((p, "no listable en SP"))
        continue
    bad = [f for f in pf if f not in cf or cf[f] != pf[f]]
    if bad:
        skipped.append((p, f"NO redundante en SP: {bad}"))
        continue  # 2a verificación falla -> NO borrar
    if DRY:
        recycled += 1
        continue
    # Vaciar: reciclar cada archivo (ya verificado redundante e idéntico en la canónica),
    # luego reciclar la carpeta vacía. Todo a papelera (recuperable).
    fail = None
    for f in pf:
        rf = recycle_file(p, f)
        if rf.status_code not in (200, 204):
            fail = f"archivo {f} -> HTTP {rf.status_code}"
            break
    if fail:
        skipped.append((p, fail))
        continue
    r = recycle(p)
    if r.status_code in (200, 204):
        recycled += 1
    else:
        skipped.append((p, f"carpeta recycle HTTP {r.status_code}: {r.text[:80]}"))

print(f"{'(DRY) ' if DRY else ''}{'a reciclar' if DRY else 'recicladas'}: {recycled} | saltadas: {len(skipped)}")
for s in skipped[:15]:
    print("  SKIP", s)
