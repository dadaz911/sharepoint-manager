# Prompt: ajustes al dashboard de cargues (frontend + transporte)

> **Cómo usar este archivo.** Pegá todo lo que está bajo `--- INICIO DEL PROMPT ---` como primer
> mensaje de una sesión nueva en este repo. Continúa el trabajo de `PROMPT_DASHBOARD_CARGUES.md`,
> que ya se implementó. Está escrito para un modelo de gama media: cada dato está acá.

---

## --- INICIO DEL PROMPT ---

Trabajás en `/home/daniel/claudecode/sharepoint-manager`. El dashboard de cargues ya se
implementó (cambios sin commitear en `dashboard.py`, `templates/dashboard.html`,
`requirements.txt`, `MANUAL.md`, más archivos nuevos). **Funciona, pero tiene dos problemas de
fondo y hay que corregirlos.** No empieces de cero: lo que existe está bien encaminado.

Leé `MANUAL.md` completo antes de tocar código, en especial §4.1 (por qué el cargue va a 0,9
archivos/s) y §8 (lecciones de operación, con trampas concretas que ya costaron tiempo).

---

### PROBLEMA 1 — El frontend no sigue la línea de los dashboards hermanos

Este es el motivo principal de estos ajustes. Hay dos dashboards en el ecosistema que **son la
referencia estética y estructural**, y el nuevo no se parece a ellos:

- `~/claudecode/rnmc/dashboard/static/` → `index.html` (4,7 KB), `styles.css` (6,2 KB), `app.js` (18,7 KB)
- `~/claudecode/lico/dashboard/explorer/static/` → `index.html`, `charts.js`

**Abrí los tres archivos de RNMC antes de escribir una línea de HTML o CSS.** Son pequeños y se
leen en minutos. Ese es el destino al que tenés que llegar.

**Qué los caracteriza — replicalo:**

1. **Cero librerías externas.** No hay ni un `<script src="https://...">` ni un CSS de CDN. Todo
   es HTML, CSS y JavaScript propios, servidos localmente. **No agregues Bootstrap, Tailwind,
   Chart.js, jQuery ni nada por el estilo.** Si el dashboard actual los trajo, sacalos.

2. **CSS propio y compacto**, en su archivo, sin utilidades atómicas embebidas en el HTML. RNMC
   resuelve todo en 6 KB.

3. **Estructura semántica**, exactamente esta jerarquía:

   ```html
   <header class="topbar">
     <h1>…</h1>
     <div class="topbar-controls">…</div>
   </header>
   <main>
     <section id="kpi-section" aria-label="Indicadores">
       <div class="kpi-row" id="kpi-cards">…</div>
     </section>
     <div class="panel-row">
       <div class="panel" id="panel-…">
         <h2>…</h2>
         <div class="bars" id="bars-…">…</div>
       </div>
     </div>
   </main>
   ```

   Tarjetas de indicadores arriba, paneles con desglose en barras debajo. Usá los mismos nombres
   de clase (`topbar`, `kpi-row`, `panel-row`, `panel`, `bars`) para que los tres dashboards se
   sientan del mismo sistema.

4. **Las barras se dibujan con `div`s y CSS**, no con una librería de gráficos. Mirá cómo RNMC
   arma `bars-semaforo`, `bars-host`, `bars-clasificacion` en su `app.js` y hacé lo mismo.

5. **Accesibilidad mínima**: `aria-label` en las secciones, como ya hace RNMC.

6. **JavaScript a pelo**, sin framework. Fetch a las rutas `/api/...`, render por manipulación
   del DOM.

**Qué paneles poner, traducido a esa estructura:**

| Elemento | Contenido |
|---|---|
| `kpi-row` | Subidos / total · velocidad actual · ETA · minutos de token · proceso vivo o muerto · errores reales |
| `panel` "Avance en el tiempo" | Barras por hora: cuántos archivos se subieron en cada una. Revela paradas nocturnas y caídas de token |
| `panel` "Por fuente" | Una barra por fuente (LICO, RNMC, SIPROJJ-CONS, SIPROJC, REGIS, …) con subidos sobre total |
| `panel` "Trazabilidad" | El buscador: campo de texto y resultados |

**Nota sobre plantillas.** El dashboard actual usa `templates/dashboard.html` de Flask, mientras
RNMC y LICO sirven estáticos. Podés mantener la plantilla de Flask —no vale la pena reescribir el
servidor— pero **su HTML y su CSS deben verse como los de RNMC**. Lo que importa es la línea
visual y estructural, no el mecanismo de entrega.

---

### PROBLEMA 2 — El transporte remoto trae 49 MB por refresco

`dashboard.py` en modo remoto ejecuta un script en `carbon` que hace `base64` de **cada archivo
completo** y lo manda por stdout de ssh (ver `_remote_script()` y `_refresh_remote()`, cerca de
las líneas 495-545).

Medido el 2026-08-05: los seis manifiestos pesan **49 MB**, que en base64 son unos **65 MB** en
una sola línea, que Python arma en memoria y parsea. **El primer snapshot no volvió en más de dos
minutos.** Y empeora: `.upload_progress.json` cambia cada 250 archivos subidos, así que su firma
`mtime:size` se invalida sin parar durante un cargue activo y se retransmite en cada ciclo.

**El error conceptual es traer el dato para procesarlo en vez de procesarlo donde está.** Para
responder "cuántos van y a qué velocidad" no hace falta el manifiesto: hacen falta dos números.

**Qué hacer:**

1. **Reemplazá el script remoto por uno que calcule los agregados en `carbon`** y devuelva un
   **JSON compacto**, de unos pocos KB: totales por fuente, subidos, pendientes, últimas líneas
   de avance del log, minutos de token, proceso vivo. Un `python3 -c` de pocas líneas alcanza.

2. **El buscador de trazabilidad no debe copiar los manifiestos.** Cuando alguien busca, ejecutá
   un `grep` remoto sobre los CSV **bajo demanda** y devolvé solo las filas que coinciden. Nadie
   busca 138.948 archivos a la vez.

3. **Conservá lo que ya está bien resuelto**: el hilo de fondo que refresca cada 30 s, el marcado
   de datos obsoletos con su antigüedad, y el modo `local`/`remoto` por variable de entorno.

Objetivo medible: **el primer snapshot en modo remoto debe volver en menos de 2 segundos.**

---

### PROBLEMA 3 — Limpiezas menores

1. **`PROGRESS_FILE` está definido dos veces** en `dashboard.py`, líneas 63 y 84. Dejá una sola.

2. **Rutas duplicadas en dos idiomas.** Hay cuatro decoradores para dos funciones:
   `/api/source-summary` + `/api/fuentes`, y `/api/trace/search` + `/api/trazabilidad/search`.
   Son alias sobre la misma función, no lógica repetida, pero son dos nombres para mantener.
   **Elegí uno de los dos idiomas y quedate con él.** Mirá cómo nombran sus rutas RNMC y LICO y
   seguí esa convención; la coherencia con los hermanos vale más que la preferencia personal.

---

### Lo que NO hay que romper

El dashboard existente tenía **21 rutas** antes de estos cambios y ahora tiene 25, sin ninguna
eliminada. Entre ellas está el explorador de SharePoint (`/api/explorer/*`), que es la parte más
grande del archivo.

**Antes de tocar nada**, levantá el dashboard tal como está, anotá qué muestra y qué rutas
responden. Al terminar, comprobá que todas siguen respondiendo. Una regresión en el explorador
sería peor que los tres problemas juntos.

---

### Verificación antes de dar por terminado

Comprobá cada punto y **mostrá la salida**, no lo afirmes:

1. `grep -c "https://" templates/dashboard.html` → **0 referencias a CDN**.
2. Las clases `topbar`, `kpi-row`, `panel-row`, `panel` y `bars` existen en el HTML y en el CSS.
3. Primer snapshot en modo remoto: **menos de 2 segundos**, cronometrado.
4. Las 25 rutas responden. Listalas y probá al menos `/`, `/api/status` y una de `/api/explorer/`.
5. El buscador encuentra un documento que **sí** está subido y otro que **no**, y los distingue.
   Elegí los casos mirando el archivo de progreso, no al azar.
6. Con el cargue corriendo, los números de la portada coinciden con
   `ssh carbon 'tail -3 ~/cargue-marzo2026.log'`.
7. Poné el dashboard al lado del de RNMC en dos pestañas. **Si no se ven de la misma familia, no
   terminaste.**

---

### Contexto que te evita preguntas

- El cargue en curso es de **138.948 archivos, 24,57 GB, 17.006 carpetas**, va a **0,9 arch/s**
  y tarda unas **40 horas**. Está corriendo ahora mismo en `carbon`, así que tenés datos vivos
  para probar.
- Los archivos son evidencia probatoria de un proceso administrativo. La trazabilidad es el
  motivo por el que existe este dashboard, no un adorno.
- **Nunca `git add -A`**: el repo tiene archivos sin trackear con datos personales. Rutas exactas.
- Commits en español, explicando **por qué**. Mirá `git log` para el tono.
- Si algo de este prompt contradice lo que ves en el código o en los datos, **el dato manda**:
  reportá la contradicción en vez de programar contra una suposición.

## --- FIN DEL PROMPT ---
