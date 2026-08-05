# Prompt: dashboard de monitoreo y trazabilidad de cargues

> **Cómo usar este archivo.** Pegá todo el contenido bajo la línea `--- INICIO DEL PROMPT ---`
> como primer mensaje de una sesión nueva en este repo. Está escrito para ejecutarse con un
> modelo de gama media: cada dato que hace falta está acá, no hay que deducir nada.

---

## --- INICIO DEL PROMPT ---

Construí un dashboard web para monitorear cargues masivos a SharePoint y dar trazabilidad de qué
archivo se subió, cuándo y desde dónde. Trabajás en `/home/daniel/claudecode/sharepoint-manager`.

Leé `MANUAL.md` completo antes de escribir código: el §4 explica cómo funciona el cargue, el §7
las trampas de la API de SharePoint y el §8 las lecciones de operación. No repitas errores que
ya están documentados ahí.

---

### 1. Qué problema resuelve

Hoy, para saber cómo va un cargue de 138.948 archivos que tarda 40 horas, hay que entrar por ssh
a la máquina que lo corre y hacer `tail` de un log. Eso tiene tres problemas:

1. **No hay historia.** El log dice dónde va ahora, no cuánto rindió ayer ni si se cayó de noche.
2. **No hay trazabilidad por archivo.** Si alguien pregunta "¿el soporte del comparendo X se
   subió?", hay que buscar a mano en un log de cientos de miles de líneas.
3. **No hay visibilidad del contexto.** El cargue depende de un token OAuth que se refresca en
   otra máquina, y de archivos de origen repartidos en tres hosts. Si algo falla, el log del
   cargue no dice por qué.

El dashboard tiene que responder estas cinco preguntas sin abrir una terminal:

- ¿Cuánto lleva subido y cuánto falta?
- ¿A qué velocidad va y cuándo va a terminar?
- ¿Está vivo el proceso, o murió hace horas?
- ¿Está sano el token, o el cargue va a empezar a fallar?
- ¿Este archivo concreto se subió? ¿Cuándo? ¿Desde qué host salió?

---

### 2. Dónde están los datos (rutas y formatos reales)

Todo vive en el host `carbon`, accesible por `ssh carbon` sin contraseña desde `gold`.

**2.1. Log del cargue** — `carbon:~/cargue-marzo2026.log`

Texto plano. Las líneas que importan tienen este formato exacto:

```
[11:28:21] Origen: /home/daniel/cargue-sharepoint/meta_marzo_2026
[11:28:21] Destino: /sites/OficinadeDepuracindeCartera/Documentos compartidos/...
[11:28:21] Extensiones: ('.pdf', '.png') | hilos: 4 | ya subidos (resume): 0
[11:28:23] Total ('.pdf', '.png'): 138948 | pendientes: 138948
[11:32:40]   250/138948 | ok=250 err=0 | 1.0/s | ETA ~39.6h
[11:37:22]   500/138948 | ok=500 err=0 | 0.9/s | ETA ~41.5h
[21:51:25] FIN. ok=220 err=104499 skip=0 | progreso: /home/.../.upload_progress.json
```

Notas imprescindibles:

- La hora es **solo `HH:MM:SS`, sin fecha**. Un cargue de 40 h cruza medianoche: si comparás
  horas como números vas a calcular velocidades negativas. Detectá el salto (hora menor que la
  anterior) y sumá un día.
- La línea de avance sale **cada 250 archivos**, no cada N segundos. Si el proceso va lento,
  pueden pasar minutos sin líneas nuevas. **Ausencia de líneas no significa que esté muerto.**
- En la línea `FIN.`, el campo `err=` **no son errores reales**: el motor vuelca ahí los
  pendientes no intentados al recibir la señal de parada. El contador fiable es el `err=` de las
  líneas de avance.

**2.2. Progreso** — `carbon:~/cargue-sharepoint/meta_marzo_2026/.upload_progress.json`

JSON. Estructura real:

```json
{"uploaded": ["CC1000984095/1000984095_LICO.pdf", "CC1000984095/1000984095_REGIS.pdf", "..."]}
```

Rutas relativas al directorio de origen. La clave es `uploaded` y su valor es una lista de
strings. Puede tener cientos de miles de entradas: no la cargues entera en memoria en cada
petición HTTP, cacheala y refrescá por `mtime` del archivo.

**2.3. Errores** — `carbon:~/cargue-sharepoint/meta_marzo_2026/.upload_errors.log`

Texto, una ruta por línea. **Cuidado:** el motor lo usa como cola de pendientes, así que su
número de líneas **no** es el número de fallos. Mostralo como "cola pendiente", nunca como
"errores".

**2.4. Manifiestos** — `carbon:~/cargue-sharepoint/manifiestos/*.csv`

Son la fuente de trazabilidad. Seis archivos, todos CSV con encabezado. Columnas relevantes
(no todos los archivos tienen todas):

| Columna | Significado |
|---|---|
| `host_origen` | `gold`, `silver` o `carbon` — de qué máquina salió el archivo |
| `ruta_origen` | ruta absoluta en ese host |
| `existe` | `true`/`false` o `SI`/`NO` — si el origen existía al inventariar |
| `tipo_documento` | `CC`, `CE`, `PA` o el nombre largo (`Cédula Ciudadanía`) |
| `numero_documento` | número del sujeto |
| `comparendo` | solo en los manifiestos de RNMC |
| `fuente` | `LICO`, `RNMC`, `SIPROJJ-CONS`, `SIPROJC`, `REGIS`, … |
| `nombre_destino` | nombre final del archivo en SharePoint |

Los seis archivos: `manifiesto-siproj-sinfecha.csv`, `manifiesto-rnmc-3hosts-sinfecha.csv`,
`manifiesto-lico-sinfecha.csv`, `manifiesto-registraduria-sinfecha.csv`,
`manifiesto-rnmc-detalle.csv`, `manifiesto-lico-detalle.csv`.

Hay copias de solo lectura en `manifiestos/archivo-20260805/`. **No escribas en ese directorio.**

**2.5. Salud del token** — dos fuentes:

```bash
ssh raspberrypi3 'python3 ~/sharepoint-token/token_refresher.py status'
journalctl --user -u sharepoint-oauth-health -n 20 --no-pager   # en gold
```

El archivo del token es un JWT en `carbon:~/.cache/spm/.token`. Su vencimiento sale de decodificar
el segundo segmento (base64url) y leer el campo `exp` (epoch en segundos). El `MANUAL.md` §6.1
explica los estados que reporta el vigilante.

**2.6. ¿Está vivo el proceso?**

```bash
ssh carbon 'pgrep -f "[s]ubir_masivo" >/dev/null && echo vivo || echo muerto'
```

**Los corchetes son obligatorios.** Sin ellos el patrón se encuentra a sí mismo en la línea de
comando de ssh y el resultado es siempre "vivo". Está explicado en `MANUAL.md` §8.1.

---

### 3. Qué construir — **extender `dashboard.py`, NO crear uno nuevo**

**Esto es una ampliación, no una aplicación nueva.** `dashboard.py` ya existe en este repo: son
~59 KB en **Flask + Flask-SocketIO** (`async_mode='threading'`), con actualización en tiempo real
por websocket. Ya expone:

```
/  ·  /api/status  ·  /api/logs  ·  /api/errors  ·  /api/history
/api/start  ·  /api/stop  ·  /api/retry-errors  ·  /api/token/refresh  ·  /api/config
/api/explorer/{sites,browse,search,details,download,folder-contents,...}
```

**Leelo antes de escribir una línea.** Tu trabajo es que ese dashboard sirva para el cargue
actual y agregarle trazabilidad, reutilizando su estructura, su estilo de plantillas y sus
convenciones de API. No dupliques lo que ya hace.

**3.1. Defecto conocido que hay que arreglar primero.** El dashboard detecta el proceso con
`pgrep -f 'subir_paralelo.py'` (líneas ~781, ~888, ~1046), pero **el motor actual es
`subir_masivo.py`**. Por eso hoy no ve el cargue en curso. Corregilo de forma que soporte ambos
nombres, no que reemplace uno por otro: puede haber cargues viejos corriendo.

Al hacerlo, aplicá `MANUAL.md` §8.1: el patrón necesita corchete (`[s]ubir_masivo`) o encuentra
su propia línea de comando. Con `subprocess.run(['pgrep', '-f', ...])` el riesgo es menor que por
ssh, pero verificá el comportamiento real en vez de asumirlo.

**3.2. Lo que hay que agregar** (el resto de §3 detalla cada vista):

| Vista | Estado |
|---|---|
| Panel de estado | **ya existe** — adaptarlo al motor y las métricas nuevas |
| Gráfico de avance | **ya existe** `/api/history` — verificar que sirva para 40 h de cargue |
| Buscador de trazabilidad | **nuevo** — es el aporte principal |
| Resumen por fuente | **nuevo** |

**Restricciones firmes:**

- **Seguí el stack existente: Flask + Flask-SocketIO.** No introduzcas otro framework.
- **Las vistas nuevas son de solo lectura.** No agregues controles de escritura nuevos. Los
  botones de start/stop/retry que ya existen se conservan — son parte del diseño del repo — pero
  la trazabilidad y el resumen por fuente solo consultan.
- **Nunca escribas sobre los manifiestos ni sobre los archivos de progreso del cargue.** El
  directorio `manifiestos/archivo-20260805/` está en modo `444`: es el registro de trazabilidad
  y debe quedar intacto.
- **Sin credenciales en el código.** El token se lee de su archivo; nada de secretos embebidos.
- **Tolerante a que los datos no estén.** Si el log no existe, si el JSON está a medio escribir,
  si carbon no responde: la página muestra el estado degradado y sigue funcionando. Nunca una
  traza de error al usuario.

**3.3. Dashboards hermanos, para coherencia.** Hay otros dos en el ecosistema, con el mismo
propósito sobre otras fuentes:

- `~/claudecode/rnmc/dashboard/server.py` — expone la evidencia de RNMC, incluido
  `detalle_evidencia_pdf_path` mediante `LEFT JOIN LATERAL`
- `~/claudecode/siproj-dashboard/` — repo propio

Mirá cómo resuelven navegación y presentación **antes** de inventar tu propio patrón. Si ya
existe una convención razonable, seguila; si te apartás, escribí por qué en el commit.

**Vistas requeridas, en orden de importancia:**

**A. Panel de estado (la portada).** Debe caber en una pantalla sin scroll:

- Proceso: vivo / muerto, y desde hace cuánto
- Progreso: `N de M` archivos, con barra y porcentaje
- Velocidad actual (últimas 3 mediciones) y velocidad media
- ETA calculado con la velocidad **de la última hora**, no la media global — si el cargue estuvo
  detenido 8 horas, la media global miente
- Token: minutos hasta el vencimiento, con semáforo (verde >30 min, amarillo 10-30, rojo <10)
- Errores reales (del contador `err=` de la última línea de avance), no la cola pendiente

**B. Gráfico de avance en el tiempo.** Archivos subidos contra hora. Con 40 horas de cargue,
esto es lo que revela paradas nocturnas, caídas de token y degradaciones de velocidad. Un SVG
inline generado en el backend está bien; no hace falta una librería de gráficos.

**C. Buscador de trazabilidad.** Un campo de texto que acepta:

- Un número de documento (`1023880381`) → lista todos sus archivos, cuáles están subidos y cuáles no
- Un número de comparendo (`11-001-6-2022-37924`) → ídem
- Un nombre de archivo (`1023880381_LICO.pdf`) → su ficha

La ficha de cada archivo muestra: fuente, sujeto, comparendo si aplica, host de origen, ruta de
origen, nombre de destino, y si ya está en `uploaded`.

**D. Resumen por fuente.** Tabla con una fila por fuente (LICO, RNMC, SIPROJJ-CONS, …) y
columnas: total en manifiesto, subidos, pendientes, porcentaje.

---

### 4. Cómo obtener los datos: dos modos

El dashboard puede correr **en gold** (y leer de carbon por ssh) o **en carbon** (leer local).
Hacelo configurable con una variable de entorno `SPM_DASH_MODE=local|remoto` y `SPM_DASH_HOST`.

En modo remoto, **no hagas un ssh por cada petición HTTP**: un refresco del navegador dispararía
seis conexiones. Usá un hilo de fondo que consulte cada 30 segundos y guarde el resultado en
memoria; las peticiones HTTP leen de ahí. Si una consulta falla, conservá el dato anterior y
marcalo como obsoleto con su antigüedad.

---

### 5. Verificación antes de darlo por terminado

No declares el trabajo hecho sin comprobar cada punto y **mostrar la salida**:

0. **Lo que ya funcionaba sigue funcionando.** Antes de tocar nada, levantá `dashboard.py` tal
   como está y anotá qué muestra. Al terminar, comprobá que ninguna de sus rutas existentes se
   rompió — en particular el explorador de SharePoint, que es la parte más grande del archivo.
1. Con el cargue **corriendo**, la portada muestra el progreso real. Contrastalo contra
   `ssh carbon 'tail -3 ~/cargue-marzo2026.log'`: tienen que coincidir. **Antes de tu arreglo
   esto falla**, porque el dashboard busca `subir_paralelo.py` y el motor es `subir_masivo.py`:
   comprobá que falla primero, así sabés que tu corrección hizo algo.
2. Con el cargue **detenido**, la portada dice "muerto" y no se cuelga.
3. Sin el archivo de log, la página carga igual y lo reporta como faltante.
4. El buscador encuentra un documento que **sí** está subido y otro que **no**, y los distingue
   correctamente. Elegí los casos de prueba mirando el progress file, no al azar.
5. El cruce de medianoche no rompe el cálculo de velocidad. Probalo con un log de prueba que
   tenga `[23:58:00]` seguido de `[00:03:00]`.
6. El total por fuente cuadra con el conteo de filas del manifiesto correspondiente.

---

### 6. Cómo entregarlo

- **Modificaciones sobre `dashboard.py`**, no un archivo nuevo, salvo que separar un módulo de
  trazabilidad tenga una razón que puedas escribir en una línea. Si lo separás, que se importe
  desde `dashboard.py` y comparta su app Flask — un segundo proceso escuchando en otro puerto es
  exactamente lo que no queremos.
- Las plantillas nuevas van donde están las actuales, con su mismo estilo.
- Una sección nueva en `MANUAL.md` explicando qué se agregó y cómo se usa.
- Si el dashboard todavía no tiene unidad systemd, agregala siguiendo el modelo de
  `sharepoint-oauth-health.service`, que ya existe en el repo.
- Commits en español, con mensaje que explique **por qué**, no solo qué. Mirá `git log` para el
  tono.
- **Nunca `git add -A`**: el repo tiene archivos sin trackear con datos personales. Agregá rutas
  exactas.

---

### 7. Contexto que te va a evitar preguntas

- El cargue en curso es de **138.948 archivos, 24,57 GB, 17.006 carpetas** (una por sujeto).
- Va a **0,9 archivos/s** y tarda unas **40 horas**. Ese techo es del tenant de SharePoint, no
  del cliente: subir hilos no ayuda (`MANUAL.md` §4.1).
- Los archivos son evidencia probatoria de un proceso administrativo. La trazabilidad no es una
  comodidad: es el requisito por el que existe este dashboard.
- Si algo del prompt contradice lo que ves en el código o en los datos, **el dato manda**.
  Reportá la contradicción en vez de programar contra una suposición.

## --- FIN DEL PROMPT ---
