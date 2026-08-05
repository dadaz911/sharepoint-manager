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
SPM_THREADS=4                                      # ver §4.1: el techo es del tenant, no del cliente
```
`run-upload.sh` lo corre detached + jala el token del Pi en loop. Resumible: re-lanzar continúa.

### 4.1 Cuántos hilos: el techo lo pone el tenant, no la concurrencia

Medido el 2026-08-05 en el tenant SHD, cargue de 138.948 PDFs (~24,6 GB, promedio 177 KB):

| Hilos | Tasa | ETA del lote |
|---|---|---|
| 4 | **0,9 – 1,0 arch/s** | ~40 h |
| 5 | 0,8 arch/s | ~47 h |
| 8 | 0,8 – 0,9 arch/s | ~43 h |

**Duplicar de 4 a 8 hilos no cambió nada.** Eso descarta las dos hipótesis habituales: no es
throttling por concurrencia (8 habría sido claramente peor) ni latencia por petición (ocho en
paralelo habrían dado el doble que cuatro). Un techo plano frente a la concurrencia apunta a un
**límite del lado del tenant por unidad de tiempo**: ~1 escritura por segundo en la biblioteca,
sin importar por cuántos canales se pida. Contra eso no hay ajuste de cliente que sirva.

Consecuencias prácticas:

- **No subas hilos esperando ir más rápido.** Perdés tiempo midiendo y podés empezar a recibir 429.
- **El ancho de banda no es el problema.** 24,6 GB en 40 h son 166 KB/s: irrisorio. El costo es
  por *archivo*, no por byte — con archivos pequeños el overhead del protocolo domina.
- **Si hace falta acortar, reducí el número de archivos, no aumentes hilos.** Consolidar varios
  soportes en un PDF por sujeto tendría un efecto de orden de magnitud; ajustar concurrencia, no.
- **Planificá para días, no horas**, y confiá en el resume: el proceso sobrevive a cortes de red,
  vencimientos de token y reinicios de la sesión.

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

## 8. Lecciones aprendidas en operación

Cada una salió de un incidente real, con su fecha. Están acá porque volverían a morder.

### 8.1 `pkill -f` se mata a sí mismo

`ssh host 'pkill -f subir_masivo.py'` **no detiene el proceso**: la cadena del comando remoto
contiene el patrón, así que `pkill` encuentra primero el bash que lo ejecuta y se suicida. El
síntoma es desconcertante — el comando devuelve *sin salida ninguna* y el proceso sigue vivo.

```bash
# MAL — se autodestruye, el objetivo sobrevive
ssh carbon 'pkill -f subir_masivo.py'

# BIEN — el corchete impide que el patrón se encuentre a sí mismo
ssh carbon 'P=$(pgrep -f "[s]ubir_masivo" | head -1); [ -n "$P" ] && kill -TERM $P'
```

Vale para `pgrep`, `pkill` y cualquier `grep` sobre la tabla de procesos ejecutado por ssh.

### 8.2 Planificar y ejecutar tienen que ser dos comandos, no dos ramas

Todo script que borre o mueva lleva `--ejecutar`. Sin la bandera hace una **pasada en seco**:
cuenta, resuelve rutas, detecta colisiones y no toca nada.

El 2026-08-05 esa pasada evitó un desastre: al re-armar un cargue de 138.948 archivos detectó
**32.446 orígenes irresolubles** *antes* de borrar el staging anterior. Ejecutar directo habría
borrado 104.735 archivos buenos para reconstruir solo 106.502, sin material y sin diagnóstico.
Costo de la pasada en seco: dos minutos.

### 8.3 La idempotencia convierte un fallo de infraestructura en un no-evento

El borrado de un cargue interrumpido se cortó tres veces (dos caídas de API, un `timeout` que
mató el cliente ssh). El resultado fue correcto igual, y **verificable**, porque relanzar un
borrado idempotente no es un riesgo: es una comprobación. La segunda pasada reportó
`borrados=0 ya_ausentes=236 fallos=0`, que es exactamente la firma de una operación ya hecha.

Reglas que lo hacen posible:
- Un `404` al borrar es **"ya ausente"**, no un fallo.
- Copiar salta si el destino existe con el mismo tamaño.
- El estado vive en un archivo de progreso, no en la memoria del proceso.

Un script con "ya iba por el N, sigo desde ahí" habría dejado el estado en duda tras el corte.

### 8.4 Dos contabilidades del mismo hecho: usá la que erra hacia lo recuperable

El motor de cargue lleva un contador incremental **y** una lista de rutas. Al interrumpirse
reportó `ok=220` pero la lista tenía **236** entradas — los que estaban en vuelo al llegar la
señal. En una lectura es una curiosidad; en un **borrado** es la diferencia entre limpiar bien y
dejar 16 duplicados invisibles.

Criterio: en una operación destructiva, cuando dos registros del mismo hecho discrepan, usá el
que erra hacia el lado recuperable. Acá, la lista más larga: borrar de más contra un archivo
inexistente no cuesta nada; borrar de menos, sí.

### 8.5 Verificá el objetivo con dos filtros independientes

Antes de borrar los 236, se comprobó (a) que estuvieran en el archivo de progreso y (b) que su
nombre siguiera la nomenclatura vieja que se iba a reemplazar. **236/236 en ambos.** Si el
archivo de progreso hubiera arrastrado algo de otra corrida, el patrón del nombre lo habría
delatado. Cuando la operación es irreversible, que la lista de objetivos pase dos filtros que no
dependan uno del otro cuesta poco y ataja mucho.

### 8.6 `ItemCount` antes de tocar: ¿hay contenido ajeno?

En un sitio compartido, siempre `?$select=ItemCount` sobre la carpeta destino **antes** de
borrar. En el caso del 2026-08-05 dio **38**, exactamente las 38 carpetas que había creado
nuestro propio cargue: prueba de que no había contenido de otro proceso en riesgo. Si hubiera
dado más, el borrado se detenía.

### 8.7 No borres carpetas: dejalas vacías

El recargue las reutiliza, y `delete` sobre una carpeta no vacía devuelve un 500 con un mensaje
engañoso (§6). Borrá archivos por su ruta exacta, uno por uno, nunca con comodines.

### 8.8 Un directorio de trabajo no debe contener sus propios insumos

Los manifiestos viven en `~/cargue-sharepoint/manifiestos/`, **hermana** del directorio de
staging `~/cargue-sharepoint/meta_marzo_2026/`, no dentro de él. Por eso "borrar y re-armar el
staging" es una operación segura. Si los manifiestos hubieran estado adentro, esa misma orden
habría destruido el inventario que costó horas levantar.

Corolario: archivá los manifiestos con permisos `444` y una copia fechada. Son el único registro
de qué archivo salió de dónde.

### 8.9 Indexá por nombre en vez de adivinar rutas

Al traer evidencia de varios hosts a un staging local, la disposición del destino **no** replica
la del origen. Reconstruir la ruta esperada falla en silencio y devuelve cero. Recorré el
staging una vez, armá un índice `basename → ruta real`, y resolvé por ahí. Barato, y funciona
sea cual sea la estructura.
