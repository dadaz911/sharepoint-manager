# Solicitud técnica — Registro de aplicación para integración automatizada con SharePoint Online

**Dirigido a:** Dirección de Tecnologías de la Información y las Comunicaciones (DTIC)
**Solicitante:** Oficina de Depuración de Cartera — Subdirección de Cobro
**Asunto:** Provisión de una identidad de carga de trabajo (*workload identity*) para el proceso
automatizado de cargue de soportes probatorios a SharePoint Online
**Fecha:** _____________

---

## 1. Qué se solicita

Un **registro de aplicación** en Entra ID del tenant de la Secretaría, de un solo tenant, con:

| Elemento | Valor solicitado |
|---|---|
| Tipo de credencial | **Certificado** (X.509). No se solicita *client secret*. |
| Flujo de autenticación | `client_credentials` (OAuth 2.0), sin interacción de usuario |
| Permiso | **`Sites.Selected`** (permiso de aplicación, SharePoint / Microsoft Graph) |
| Alcance efectivo | Únicamente el sitio `/sites/OficinadeDepuracindeCartera` |
| Consentimiento | De administrador, limitado al permiso anterior |

**No se solicita** `Sites.FullControl.All`, `Sites.ReadWrite.All` ni ningún permiso de alcance
global. `Sites.Selected` no otorga acceso a ningún sitio por sí mismo: requiere que la DTIC
conceda explícitamente el acceso al sitio indicado, y puede revocarse sitio por sitio.

El certificado puede ser generado por la DTIC y entregado al solicitante, o generado por el
solicitante y registrada únicamente su parte pública. Se prefiere lo primero si existe una
política de emisión.

## 2. Por qué se solicita

El proceso lleva operando con una solución provisional que **debe reemplazarse**, por dos razones
independientes: una de continuidad del servicio y otra de seguridad.

### 2.1 Continuidad: la credencial actual no sostiene un proceso desatendido

La integración usa hoy un flujo de *refresh token* asociado a la cuenta de usuario del
funcionario. Ese modelo es incompatible con un proceso automatizado, porque el tenant aplica
—correctamente— políticas diseñadas para sesiones humanas. Dos interrupciones documentadas:

| Fecha | Código de Entra | Causa | Indisponibilidad |
|---|---|---|---|
| 2026-06-25 | `AADSTS50173` | Cambio de contraseña de la cuenta: revoca el *refresh token* de inmediato | 40 días |
| 2026-08-14 | `AADSTS50078` | Venció la frescura de MFA exigida por política de acceso condicional | 52,5 horas |

Ninguna de las dos fue una falla del software. Ambas son el resultado esperable de someter una
credencial de usuario a las políticas de sesión de un usuario. **Volverán a ocurrir**, y cada
ocurrencia interrumpe el cargue de material probatorio de un proceso administrativo en curso.

Las identidades de carga de trabajo con certificado no están sujetas a políticas de acceso
condicional de usuario, ni a expiración de contraseña, ni a requisitos de MFA. Es el mecanismo
que Microsoft define para este caso de uso.

### 2.2 Seguridad: la solución provisional es indistinguible de un patrón de ataque

Por no existir un registro de aplicación propio, el proceso se autentica con el identificador de
cliente público de **Microsoft Office** (`d3590ed6-52b3-4102-aeff-aad2292ab01c`). Esto tiene tres
consecuencias que conviene que la DTIC conozca:

1. **No es posible aplicar políticas diferenciadas.** Para Entra, este proceso *es* Office.
   Cualquier excepción que se le concediera sería una excepción a Office completo para esa
   cuenta. La DTIC no tiene hoy ninguna palanca para gobernar este tráfico por separado.
2. **Genera un patrón de telemetría que parece robo de token.** Durante la interrupción de
   agosto se registraron decenas de inicios de sesión no interactivos fallidos con el `appid` de
   Office desde una IP residencial. Es la firma característica de herramientas de extracción de
   tokens. Un analista del SOC que lo revise tiene motivos para revocar la cuenta.
3. **El único mecanismo de arranque está en vía de bloqueo.** El proceso depende del *device code
   flow*, que Microsoft está deshabilitando por política gestionada. Si el tenant la activa, la
   integración queda sin forma de re-autenticarse.

Un registro propio con `Sites.Selected` resuelve las tres: tráfico identificable y gobernable,
alcance mínimo verificable, y sin dependencia del *device code flow*.

## 3. Qué hace el proceso

| | |
|---|---|
| **Propósito** | Cargar y verificar soportes probatorios de la depuración de cartera por remisibilidad (Art. 820 E.T.) |
| **Volumen de referencia** | 138.948 archivos PDF (24,57 GB) en 17.006 carpetas, cargados entre el 5 y el 7 de agosto de 2026 |
| **Operaciones** | Crear carpetas, cargar archivos, y lecturas de verificación (`ItemCount`, existencia) |
| **Destino** | Exclusivamente `/sites/OficinadeDepuracindeCartera/Documentos compartidos/...` |
| **Ritmo** | ~1 archivo/segundo. El límite lo impone el tenant, no el cliente |
| **Dónde corre** | Infraestructura del solicitante, en red privada; el certificado no sale de ese host |

El proceso **no borra** contenido ajeno ni accede a otros sitios. Toda operación destructiva del
sistema opera en modo simulación por defecto y exige una bandera explícita.

## 4. Custodia del certificado

- Almacenamiento en el host de ejecución con permisos `0600`, propietario el usuario de servicio.
- No se versiona en ningún repositorio ni se transmite fuera de la red privada del solicitante.
- Rotación: el solicitante se compromete a rotar con la periodicidad que la DTIC establezca, y a
  notificar de inmediato cualquier sospecha de compromiso para su revocación.
- La revocación desde Entra deja el proceso sin acceso de forma inmediata y total, sin requerir
  ninguna acción del solicitante.

## 5. Datos que la DTIC debe devolver

Para completar la configuración se requieren únicamente:

```
TENANT_ID      = (ya conocido)
CLIENT_ID      = ______________________________   (del nuevo registro)
CERTIFICADO    = ruta/entrega del .pfx o .pem      (o confirmación de la huella registrada)
THUMBPRINT     = ______________________________
SITIO AUTORIZADO = /sites/OficinadeDepuracindeCartera
```

## 6. Alternativa, si no procede

Si la DTIC no considera viable un registro de aplicación, la alternativa es una **cuenta de
servicio dedicada** excluida de las políticas de acceso condicional que exigen MFA y frescura de
sesión, con contraseña de expiración larga o nula.

Se deja constancia de que esta alternativa es **menos segura** que lo solicitado: mantiene una
credencial de contraseña reutilizable y con alcance de usuario, en lugar de un certificado con
alcance limitado a un solo sitio y revocable de forma independiente. Se menciona solo por
completitud.

## 7. Situación mientras tanto

La integración sigue operando con el mecanismo provisional, al que se le añadieron controles de
detección: verificación funcional periódica contra SharePoint, alerta a un canal propio cuando el
refresco lleva más de dos horas sin éxito, y registro persistente de la causa de cada falla.
Esos controles **reducen el tiempo de detección, no evitan la interrupción**. La solicitud de este
documento es lo único que ataca la causa.

---

_Documento técnico de respaldo. La descripción completa de los incidentes y del diseño está en
`MANUAL.md` §8.13 del repositorio de la herramienta._
