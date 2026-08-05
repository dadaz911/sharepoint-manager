# Manual operativo — SharePoint Manager (OAuth)

El token de SharePoint/M365 se mantiene con OAuth2 refresh-token, **sin navegador**. El motor
corre en la Pi; gold solo vigila; los cargues corren donde estén los datos (carbon). Arquitectura
en `CLAUDE.md`.

## 1. Despliegue

**Pi (raspberrypi3) — el refrescador:**
```bash
# (una vez) copiar token_refresher.py + config a ~/sharepoint-token/ y la unit systemd --user
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py login'   # device-code + MFA
ssh raspberrypi3 'systemctl --user enable --now sharepoint-refresher.service'
```
**gold — el notificador de salud:**
```bash
bash deploy.sh    # instala sharepoint-oauth-health.timer y retira el stack de navegador
```

## 2. Operación diaria (sin intervención)

El token se refresca solo en la Pi cada ~50 min. Comandos útiles:
```bash
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py status'      # validez del token
ssh raspberrypi3 'journalctl --user -u sharepoint-refresher -n 20 --no-pager'
systemctl --user list-timers sharepoint-oauth-health.timer                    # vigilancia en gold
```

## 3. Re-login (solo cuando caduca el refresh token, ~90 días)

`oauth-health` (en gold) notifica *"El token de SharePoint dejó de refrescarse"* solo si falla
de forma sostenida. Entonces:
```bash
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py login'
# muestra una URL (microsoft.com/devicelogin) + un código; autenticar con la cuenta SHD + MFA
```
Es el **único** paso con navegador, y rara vez.

## 4. Cargar archivos a SharePoint (en el host de datos, p. ej. carbon)

`subir_masivo.py` — robusto para volúmenes grandes (Retry-After/back-off, resume, whitelist
`.pdf/.png`, server-side). Config por entorno (ver `upload.env` de ejemplo en el host):
```bash
SPM_BASE_URL="https://<host>.sharepoint.com/sites/<sitio>/_api/web"
SPM_DEST_FOLDER="/sites/<sitio>/Documentos compartidos/.../carpeta"   # server-relative
SPM_SOURCE_DIR="/ruta/local/a/subir"
SPM_TOKEN_FILE="/home/daniel/.cache/spm/.token"   # se jala del Pi: rsync raspberrypi3:~/.cache/spm/.token
SPM_THREADS=5                                      # óptimo medido en el tenant SHD (6 throttlea)
```
`run-upload.sh` lo corre detached + jala el token del Pi en loop. Resumible: re-lanzar continúa.

## 5. Consolidar carpetas (si vienen partidas por familia documental)

`consolidar_masivo.py` fusiona, por sujeto, las carpetas no-canónicas (`ID-`, etc.) en la
canónica `<TIPO>-<n>` con `SP.MoveCopyUtil.MoveFile` (server-side, paths en el cuerpo → evita el
límite de URL de `moveto`). Idempotente y resumible. `cleanup-redundant.py` recicla carpetas
totalmente redundantes (verificación local + live por tamaño, a papelera). `consolidar-origen.py`
replica la consolidación en el origen local.

## 6. Diagnóstico

| Síntoma | Causa | Solución |
|---------|-------|----------|
| `oauth-health` avisa "dejó de refrescarse" | refresh token caducó/revocado | `token_refresher.py login` en la Pi |
| Cargue da 401 | token vencido en el host | re-jalar del Pi (`rsync ...`); el daemon del Pi lo refresca |
| 429/503 en cargue | throttling de SharePoint | normal; el back-off lo maneja; bajar `SPM_THREADS` |
| `maxUrlLength` en move | usar `moveto` con 2 rutas en URL | usar `SP.MoveCopyUtil.MoveFile` (paths en el cuerpo) |
| delete de carpeta da 500 "suprimir todos los elementos" | carpeta no vacía | vaciar (reciclar archivos) y luego la carpeta |
| `GET .../GetFolderByServerRelativeUrl(...)?$select=Name` da **200 en una carpeta borrada** | `$select` lee de un metadato cacheado, no resuelve el objeto | usar **GET limpio** sin `$select` → `404 = borrada`, `200 = existe` (ver §7) |
| `SPQueryThrottledException` (HTTP 500) al listar `/Folders` | la carpeta tiene **>5000 hijos** (list-view threshold) | no enumerar; usar `?$select=ItemCount` + aritmética, o muestrear nombres reales (ver §7) |
| `AADSTS50173: The provided grant has expired due to it being revoked` en el journal del refrescador | **cambio o reseteo de contraseña** de la cuenta SHD: revoca el refresh token de inmediato, sin esperar los 90 días | `token_refresher.py login` en la Pi (§3). No hay forma de evitarlo: cada cambio de contraseña obliga a un re-login |
| `oauth-health` corre y sale 0 pero el token está muerto | comportamiento **normal** entre alertas: hay cooldown de 6 h | `journalctl --user -u sharepoint-oauth-health -n 20` muestra el estado real en cada corrida (ver §6.1) |

### 6.1 Observabilidad del vigilante

`oauth-health.sh` escribe su veredicto al journal **en cada corrida**: `OK: token válido N min`,
`FALLO n: …`, `Fallo aún no sostenido (n/3)`, `Ya se alertó hace N min; en cooldown` o
`ALERTA: …`. Para ver qué está pasando:

```bash
journalctl --user -u sharepoint-oauth-health -n 20 --no-pager
```

**Por qué existe esta sección.** Hasta 2026-08-04 el script no imprimía nada: solo llamaba a
`notify-send` y salía 0. Consecuencia real: el refresh token murió el **2026-06-25** por un
cambio de contraseña y **nadie lo notó durante 40 días**. En `journalctl` se veían 110
ejecuciones "SUCCESS" idénticas, y el único rastro del fallo vivía en un contador dentro de
`$XDG_RUNTIME_DIR` — que además se borra al reiniciar, así que el conteo tampoco era fiable
como historia.

La lección generaliza más allá de este script: **una alerta cuyo único canal es una notificación
de escritorio no es un mecanismo de vigilancia**, es una cortesía. El toast dura segundos, el
cooldown de 6 h lo hace raro, y si la sesión gráfica no está activa se pierde del todo. El
registro persistente —journal, archivo, métrica— es lo que permite responder "¿desde cuándo?",
que es la primera pregunta cuando algo se rompió hace rato.

## 7. Verificar estado de carpetas en SharePoint (gotchas de la API)

Verificar existencia/conteo de carpetas en este tenant tiene dos trampas que hacen reportar
resultados **falsos** si se usa el método ingenuo. Reglas:

- **Existe / fue borrada → GET LIMPIO, sin `$select`.**
  `GET .../GetFolderByServerRelativeUrl('<rel>')` → `404` = borrada, `200` = existe.
  ⚠️ `?$select=Name` devuelve **200 para carpetas YA borradas** (falso positivo); `/Files` de una
  borrada devuelve `{"value":[]}` con 200. `?$select=Name,ItemCount` sí da 404 correcto, pero el
  GET limpio es la fuente primaria.
- **Conteo de hijos → `?$select=ItemCount`** sobre la carpeta. Es autoritativo (subcarpetas + archivos directos).
- **"¿Quedan N carpetas de tipo X?" con decenas de miles de hijos:** NO enumerar `/Folders`
  (throttlea con >5000 ítems y sin columna indexada no se pagina). En su lugar: `ItemCount` +
  aritmética del plan, o muestrear nombres **reales** (de un progress file) y comprobarlos uno a
  uno con GET limpio.

> Regla general: si dos métodos de verificación se contradicen (p. ej. `ItemCount` vs un listado),
> **uno miente** — buscar la fuente primaria, no el que confirma lo que esperabas.

## 8. Detener / desinstalar

```bash
ssh raspberrypi3 'systemctl --user disable --now sharepoint-refresher.service'   # Pi
systemctl --user disable --now sharepoint-oauth-health.timer                      # gold
```
