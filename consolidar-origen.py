#!/usr/bin/env python3
"""
consolidar-origen.py — Consolida el ORIGEN LOCAL en carbon igual que SharePoint: una carpeta
por sujeto (canónica = tipo real CC/NIT/... sin ceros). Mueve los archivos de las carpetas no
canónicas (ID-, NIT con ceros, etc.) a la canónica y elimina las carpetas vacías.

SEGURO: un archivo solo se ELIMINA si ya existe idéntico (mismo tamaño) en la canónica; si no
existe en la canónica se MUEVE (no se pierde nada). Carpetas sin canónica o con archivos no
resolubles se SALTAN (se listan). LOCAL (no toca SharePoint, que ya quedó consolidado y verificado).

Uso: python3 consolidar-origen.py [--dry]
"""

import os
import re
import sys

BASE = "/home/daniel/Documents/cargues/costo-beneficio_jun-2026"
REAL = {"CC", "NIT", "CE", "PA", "TI"}
DRY = "--dry" in sys.argv


def canon_of(folders):
    reals = [(nm, pre) for nm, pre in folders if pre in REAL]
    if not reals:
        return None
    # tipo real, preferir nombre sin ceros a la izquierda, luego más corto
    reals.sort(key=lambda x: (x[0].split("-", 1)[1] != x[0].split("-", 1)[1].lstrip("0"), len(x[0])))
    return reals[0][0]


import collections

num2 = collections.defaultdict(list)
skipped = []
for d in os.listdir(BASE):
    if not os.path.isdir(os.path.join(BASE, d)):
        continue
    m = re.match(r"^([A-Za-z]+)-(.+)$", d)
    if not m or not (m.group(2).lstrip("0") or "0").isdigit():
        skipped.append((d, "nombre no estándar"))
        continue
    num2[(m.group(2).lstrip("0") or "0")].append((d, m.group(1)))

moved = removed = folders = 0
for _n, folders_ in num2.items():
    canon = canon_of(folders_)
    if canon is None:
        skipped += [(nm, "sin canónica") for nm, _ in folders_]
        continue
    cpath = os.path.join(BASE, canon)
    csizes = {
        f: os.path.getsize(os.path.join(cpath, f)) for f in os.listdir(cpath) if os.path.isfile(os.path.join(cpath, f))
    }
    for nm, _ in folders_:
        if nm == canon:
            continue
        npath = os.path.join(BASE, nm)
        leftover = False
        for f in list(os.listdir(npath)):
            sp = os.path.join(npath, f)
            if not os.path.isfile(sp):
                leftover = True
                continue
            sz = os.path.getsize(sp)
            if f in csizes:
                if csizes[f] == sz:
                    if not DRY:
                        os.remove(sp)
                    removed += 1
                else:
                    skipped.append((f"{nm}/{f}", "tamaño distinto en canónica"))
                    leftover = True
            else:
                if not DRY:
                    os.replace(sp, os.path.join(cpath, f))
                    csizes[f] = sz
                moved += 1
        # borrar carpeta si quedó vacía
        try:
            if not leftover and not os.listdir(npath):
                if not DRY:
                    os.rmdir(npath)
                folders += 1
            elif leftover:
                skipped.append((nm, "quedaron archivos no resolubles"))
        except OSError as e:
            skipped.append((nm, str(e)))

print(
    f"{'(DRY) ' if DRY else ''}archivos movidos: {moved} | archivos redundantes eliminados: {removed} | carpetas eliminadas: {folders} | saltadas: {len(skipped)}"  # noqa: E501
)
for s in skipped[:12]:
    print("  SKIP", s)
