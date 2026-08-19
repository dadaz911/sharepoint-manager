# Manual operativo — SharePoint Manager (OAuth)

El token de SharePoint/M365 se mantiene con OAuth2 refresh-token, **sin navegador**. El motor
corre en la Pi (único productor); los consumidores lo jalan; los cargues corren donde estén los
datos (carbon), que además vigila y alerta. Arquitectura en §1 y en `CLAUDE.md`.

## 1. Despliegue

**Un productor, N consumidores.** Desde 2026-08-18 hay UNA sola fuente de token y todos los
hosts jalan de ella.

| Host | Rol | Qué corre |
|---|---|---|
| `raspberrypi3` | **Productor** — siempre encendida | `token_refresher.py run` (OAuth refresh-token) |
| `carbon` | Consumidor + **vigilante** | pull cada 10 min · `spm-watch.sh` cada 15 min · ntfy |
| `silver` | Consumidor | pull cada 10 min (lo lee el CRM) |
| `gold` | Consumidor | pull cada 10 min (workstation intermitente) |

```bash
# En cada host, desde el repo. Idempotente; detecta el host solo.
cd ~/claudecode/sharepoint-manager && ./deploy.sh
```

**Los consumidores JALAN, la Pi no empuja.** Dos razones: gold se apaga (un push fallaría contra
una máquina ausente), y empujar exigiría darle a la Pi claves de escritura hacia carbon, el
almacén de 1,6 TB — un camino de movimiento lateral desde el nodo menos endurecido hacia el más
valioso.

**Una ruta canónica por host** (`~/.cache/spm/.token`). Las rutas que los consumidores ya
esperaban son **symlinks** a ella, no copias:

| Consumidor | Ruta que espera | Resuelve a |
|---|---|---|
| CRM (silver) | `~/Desktop/Cargue a Onedrive/.token` | `~/.cache/spm/.token` |
| crm-preview (carbon) | `~/.local/state/sharepoint/.token` | `~/.cache/spm/.token` |
| `subir_masivo.py` (carbon) | `~/.cache/spm/.token` | (es el canónico) |

Enlaces y no copias porque **una copia puede quedar vieja sin que nadie lo note**, y eso ya
costó seis días de caída (ver §8.11).

### 1.1 Alertas: ntfy en la tailnet

El canal vive en carbon, en contenedor (ahí no hay `sudo` sin contraseña, pero docker sí
funciona), escuchando **solo en localhost** y publicado a la tailnet con `tailscale serve`.
Nada queda expuesto a internet; el control de acceso es el de Tailscale.

```bash
# Suscribirse desde el teléfono (app ntfy): servidor https://carbon.taild86bb7.ts.net
#                                            tema     shd-infra
# Probar el canal a mano:
curl -H "Title: prueba" -d "hola" https://carbon.taild86bb7.ts.net/shd-infra
```

**No se usa el correo del tenant ni Teams**: dependerían de la misma credencial que se vigila,
así que el aviso se caería exactamente cuando hace falta. Dependencia circular.

Hay un **latido semanal** "todo bien". Si dejás de recibirlo, lo que se rompió es el canal de
alertas, no el token — que es el modo de falla número uno de cualquier sistema de avisos.

## 2. Operación diaria (sin intervención)

El token se refresca solo en la Pi. La cadencia se **deriva del `exp` real** (~55% de la vida
restante, unos 45 min) y no de una constante: con 50 min fijos y tokens de 65-80, perder un solo
ciclo ya garantizaba un hueco sin token.

```bash
# ¿Está sano el refresco? Sale 1 si no, y dice por qué.
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py estado'
# ¿Este host tiene un token que SIRVE? (no solo que parece vigente)
cat ~/.local/state/sharepoint-manager/pull-state.json
systemctl --user list-timers sharepoint-token-pull.timer
# Vigilante y alertas (en carbon)
ssh carbon 'systemctl --user list-timers spm-watch.timer; ~/bin/spm-watch.sh'
```

## 3. Re-login (solo cuando caduca el refresh token, ~90 días)

El vigilante de carbon avisa por ntfy (§1.1) *"El token de SharePoint dejó de refrescarse"*
cuando el productor lleva más de 2 h sin un refresco exitoso. Si la clase es **HUMANO**, el
remedio es este; si es **RED** o **TRANSITORIO**, no toques la credencial: revisá conectividad.

Ojo: en la práctica no son ~90 días. Los dos incidentes reales no fueron por inactividad sino
por política del tenant — `AADSTS50173` (cambio de contraseña, 25-jun) y `AADSTS50078` (frescura
de MFA, 14-ago). Ver §8.13.
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

### 6.2 Dashboard de monitoreo del cargue

`dashboard.py` sirve el dashboard Flask del cargue masivo. En `gold` consulta `carbon` por SSH
en un hilo de fondo cada 30 segundos; en `carbon` puede leer los mismos archivos en modo local.
Las peticiones HTTP no abren SSH. El frontend es HTML, CSS y JavaScript propio en
`templates/dashboard.html`, `static/dashboard.css` y `static/dashboard.js`; no depende de CDN,
Tailwind, Chart.js ni Socket.IO en el navegador.

Configuración mínima:

```bash
SPM_DASH_MODE=remoto SPM_DASH_HOST=carbon python3 dashboard.py   # dashboard en gold
SPM_DASH_MODE=local python3 dashboard.py                        # dashboard en carbon
```

Se pueden cambiar `SPM_UPLOAD_LOG_FILE`, `SPM_SOURCE_DIR`, `SPM_PROGRESS_FILE`,
`SPM_ERRORS_FILE`, `SPM_MANIFEST_DIR` y `SPM_TOKEN_FILE` si las rutas no son las predeterminadas.
El modo remoto usa una consulta SSH por ciclo que ejecuta un agregador en `carbon`: calcula allí
los totales por fuente, progreso, velocidad, ETA, token y proceso, y devuelve sólo JSON compacto.
No copia los manifiestos al dashboard. Si carbon no responde, conserva el último dato bueno y lo
marca como obsoleto con su antigüedad. La búsqueda `/api/trace/search` ejecuta `grep` remoto bajo
demanda y devuelve sólo las filas coincidentes.
Los manifiestos, el progreso y la cola de errores del cargue actual se leen; el dashboard no
escribe en ninguno de esos archivos.
En modo remoto el botón de refresco no intenta usar Chrome en gold: el refresco sigue siendo
responsabilidad de `raspberrypi3` y su servicio OAuth.

La portada muestra proceso, progreso, velocidad de las últimas tres mediciones, media, ETA con la
última hora, token y errores reales del log. `/api/trace/search?q=...` busca documento, comparendo
o nombre de archivo; `/api/source-summary` resume las filas de los seis manifiestos. Ambas rutas
son de solo lectura. `/api/history` usa las mediciones del log y conserva el eje relativo correcto
cuando el cargue cruza medianoche.

Para instalarlo como servicio de usuario en gold, usar el instalador idempotente. Reutiliza el
entorno virtual, conserva `~/.config/sharepoint-dashboard.env` si ya existe y puede ejecutarse
varias veces:

```bash
bash install-dashboard.sh
```

La configuración inicial queda en `~/.config/sharepoint-dashboard.env`; el instalador no la
sobrescribe. Si systemd de usuario no está disponible, prepara el entorno y la unidad e informa
el comando de activación manual.

El servicio asume que `ssh carbon` funciona sin contraseña, igual que las tareas operativas del
repositorio. El navegador se conecta a `http://localhost:5000` (o mediante el túnel SSH elegido
para administrar gold).

Comprobación del primer agregado remoto, desde gold:

```bash
time curl -fsS http://127.0.0.1:5000/api/status >/tmp/sharepoint-dashboard-status.json
python3 -m json.tool /tmp/sharepoint-dashboard-status.json | sed -n '1,80p'
```

La primera petición debe completar en menos de dos segundos y mostrar `data.mode=remoto`,
`progress`, `metrics`, `process` y `source_summary` sin transferir los CSV al navegador.
La traza remota se prueba bajo demanda:

```bash
curl -fsS --get --data-urlencode 'q=DOCUMENTO' http://127.0.0.1:5000/api/trace/search
```

Después de actualizar el repositorio, reiniciar el servicio para que cargue la plantilla y el
agregador nuevos:

```bash
systemctl --user daemon-reload
systemctl --user restart sharepoint-dashboard.service
```

Acceso desde el propio gold:

```text
http://localhost:5000
```

Acceso desde otro equipo, sin exponer el puerto públicamente:

```bash
ssh -N -L 5000:127.0.0.1:5000 gold
```

Mientras esa sesión permanezca abierta, navegar a `http://localhost:5000` en el equipo local.
Si el puerto local 5000 está ocupado, usar `ssh -N -L 5001:127.0.0.1:5000 gold` y abrir
`http://localhost:5001`.

Diagnóstico del servicio:

```bash
systemctl --user status sharepoint-dashboard.service
journalctl --user -u sharepoint-dashboard.service -f
```

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

### 8.10 El token del host de datos caduca con la corrida, no con el reloj

El 2026-08-17, al verificar un cargue terminado diez días antes, **todo devolvió `HTTP 401`**.
Parecía pérdida de acceso o de datos. No era ninguna de las dos: el daemon de la Pi venía
refrescando bien —su token tenía 13 minutos— pero **el `token-pull` que lo copiaba al host de
datos estaba atado al ciclo de vida de la corrida del cargue** y murió con ella. Carbon quedó con
un access token congelado de 8 días.

```bash
# Antes de CUALQUIER operación contra SharePoint desde el host de datos
ssh carbon 'rsync -q raspberrypi3:~/.cache/spm/.token ~/.cache/spm/.token'
```

Es la misma forma de fallo que §6.1, en otra capa: **el componente que funciona no se entera de
que el que lo consume dejó de escuchar.** El refresco estaba sano; el eslabón roto era el jalón.

Dos consecuencias prácticas:

- Un `401` masivo en una verificación posterior es **lo esperable**, no una alarma. Refrescá el
  token y repetí antes de concluir nada sobre los datos.
- Si el host de datos va a consultarse después del cargue, el `token-pull` debe tener vida propia
  (timer o daemon), no colgar del proceso de subida.

Efecto colateral útil: el 401 forzó repetir la verificación **diez días después** del cargue en
vez de en caliente, lo que comprobó permanencia y no solo escritura exitosa.

### 8.11 Copiar bien un archivo inservible no es éxito

El 12-ago un corte de luz reinició silver. Su productor de token (`sharepoint-wayland.service`,
basado en navegador) quedó **`active (running)` sin producir nada**: depende de una sesión
gráfica y, tras el reinicio, en seat0 solo estaba el greeter de lightdm. No falló — no emitió un
solo error en seis días. `Restart=on-failure` nunca se disparó porque el proceso estaba vivo.

Mientras tanto, `sync-sharepoint-token.sh` en carbon copió ese archivo cada 3 minutos y escribió
**"OK: token sincronizado" unas 2.100 veces**. Cada capa reportaba el éxito de su propia acción,
y ninguna la validez del resultado.

La regla que salió de ahí, aplicada en `pull-token.sh` y en `token_refresher.py estado`:

> Un componente debe afirmar la **frescura de su resultado**, no el éxito de su intento.
> Si el `estado` dice OK pero el token de disco está vencido, el `estado` sale 1.

Corolario de diseño: **preferí un symlink a una copia.** Una copia puede quedar vieja; un enlace
no puede. La clase entera de bugs "el archivo estaba viejo" desaparece por construcción.

### 8.12 El vigilante tiene que estar más disponible que lo vigilado

`oauth-health.sh` corría en gold, una laptop que se apaga, vigilando un servicio en la Pi, que
está siempre encendida. El 14-ago el refresco se rompió a las 15:06; gold arrancó el 16-ago a
las 13:16 y avisó a las 14:01. **46,5 horas de latencia**, exactamente iguales a "cuánto tardó
en encenderse la laptop". La lógica del vigilante era correcta; su disponibilidad no.

Tres reglas, ya implementadas en `spm-watch.sh` (que corre en carbon):

1. **Vigilá desde el consumidor siempre encendido**, no desde la estación de trabajo.
2. **Alertá por AUSENCIA DE ÉXITO RECIENTE, no por evento de error.** Un vigilante que reacciona
   a errores es ciego al fallo de §8.11, que no produjo ninguno. La ausencia cubre proceso
   muerto, host apagado y partición de red con una sola regla.
3. **El chequeo tiene que ser funcional.** Decodificar el JWT y mirar `exp` responde "¿el archivo
   parece vigente?", no "¿puedo hablar con SharePoint?". El 17-ago todo devolvió HTTP 401 con un
   token que `exp` daba por bueno. Ahora se hace un GET real contra el sitio.

Y el remedio del aviso debe depender de la **causa**: ante la Pi inalcanzable, el vigilante viejo
decía "token vencido hace 99999 min — re-login", mandando a rehacer una autenticación con MFA por
lo que era un corte de red.

### 8.13 Una credencial de sesión humana no sostiene un proceso desatendido

Los dos incidentes reales del token tienen **la misma causa raíz**, y no es un bug:

| Fecha | Código | Qué pasó |
|---|---|---|
| 2026-06-25 | `AADSTS50173` | Cambio de contraseña: revoca el refresh token al instante. **40 días** sin detectar. |
| 2026-08-14 | `AADSTS50078` | La frescura de MFA venció por política de Conditional Access. **52,5 h**, 64 fallos. |

Los dos son el tenant aplicando, correctamente, políticas diseñadas para **sesiones humanas
interactivas** a una credencial que sostiene un proceso automatizado. El modelo mental de este
manual decía "re-login cada ~90 días"; los dos incidentes cayeron fuera de ese modelo, porque
ninguno tuvo que ver con la inactividad.

**Nada de lo construido en §8.11 y §8.12 previene la próxima ocurrencia.** Todo eso mejora la
*detección*. La prevención exige otro flujo de autenticación.

Y hay un agravante que cierra la puerta a la solución fácil. Este daemon usa
`CLIENT_ID=d3590ed6-52b3-4102-aeff-aad2292ab01c`, que es el **cliente público de Microsoft
Office**. Para Entra, este proceso *es* Office. Consecuencias:

- **No se puede pedir una excepción de política "para el daemon"**, porque exceptuarlo sería
  exceptuar Office entero para ese usuario. Se hereda toda política dirigida a Office, sin
  ninguna palanca de exclusión.
- Miles de inicios de sesión **no interactivos fallidos** con el `appid` de Office desde una IP
  residencial son la firma de las herramientas de robo de token. Por eso el sondeo ante fallo
  que exige un humano se hizo **decreciente** (30 min, luego 60) en vez de agresivo: el remedio
  no puede ser lo que dispare una revocación administrativa — o sea, el incidente de junio otra
  vez, causado por nosotros.
- Microsoft está bloqueando el *device code flow* por política gestionada. Si el tenant la
  activa, **el único camino de bootstrap desaparece** y no hay plan B.

La salida es un **registro de aplicación propio** con flujo `client_credentials` + certificado y
permiso de aplicación `Sites.Selected` sobre el sitio específico: las políticas de Conditional
Access de usuario no aplican a identidades de carga de trabajo. Depende de la DTIC de Hacienda,
así que es lo que más tarda y lo que conviene radicar primero. La solicitud técnica redactada
está en `docs/solicitud-registro-aplicacion-dtic.md`.

## 9. Detener / desinstalar

```bash
ssh raspberrypi3 'systemctl --user disable --now sharepoint-refresher.service'   # Pi
systemctl --user disable --now sharepoint-oauth-health.timer                      # gold
```
