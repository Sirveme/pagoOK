# Canal de correo bancario y administración de bancos (zWeb-06)

## Qué hace

Cada empresa tiene una dirección propia `egresos+<código>@bancos.pagook.pro`
(panel → **Correo del banco**). El dueño configura en Gmail u Outlook el reenvío
de las alertas de su banco a esa dirección. Cloudflare recibe el correo, un
Email Worker lo pasa sin modificar a `POST /api/v1/correo/entrante`, y el backend:

1. Busca la empresa por el código (código inexistente o regenerado → `descartado`, sin guardar el texto).
2. Verifica DKIM. Solo acepta firmas de un dominio que esté en `dominios_correo` de un banco activo del país
   de la empresa. Si no → `rechazado` (el motivo dice qué dominios firmaron) y no crea nada.
3. Detecta la confirmación de reenvío de Gmail (`forwarding-noreply@google.com`, firmada por `google.com`) → `confirmacion_gmail`; el código se muestra en el panel.
4. Parsea el correo (`app/services/parser_correo.py`) → `procesado` (crea o completa un egreso con `origen='correo'`), `sin_formato` o `descartado` (promociones, avisos de seguridad).

**Lo que no cambia:** la captura por celular, el parser de notificaciones, `consultar`,
`marcar-usado` y `pago.confirmado`. Los egresos por correo **no** aparecen en
`consultar` ni disparan webhooks (solo los ingresos lo hacen).

**Sin duplicados:** si el mismo egreso llega por celular y por correo (misma empresa,
monto y moneda, ±3 minutos; si los dos tienen número de operación, también debe
coincidir), queda **un solo registro**. Gana el que llegó primero y el otro solo
completa los datos que faltaban (número de operación, últimos 4 dígitos, destino).

## Formatos soportados

| Banco | Correo | Tipo de egreso |
| --- | --- | --- |
| BCP | Consumo Tarjeta de **Débito** en comercio (ej. b) | `consumo_tarjeta` |
| BCP | Consumo Tarjeta de Débito en `PLIN-…` / `YAPE-…` (ej. a) | `envio_billetera`, destino Plin/Yape |
| BCP | Yapear a celular desde Banca Móvil BCP (ej. c, destino Yape o Interbank) | `envio_billetera` |
| BCP | Pago de servicios (ej. d, formato de fecha "Martes, 20 Octubre 2026 - 11:51 A. M.") | `pago_servicio` |

**Sin soporte (no hay ejemplos reales):** consumos con Tarjeta de **Crédito** BCP,
ingresos por correo (de cualquier banco) y todos los demás bancos. Esos correos
quedan como `sin_formato` y se ven en `/admin/bancos` → "Correos por revisar";
cuando haya ejemplos se agrega el formato y se usa **Reprocesar**.

## Bancos sin datos técnicos

- `dominios_correo` está **vacío para todos**, incluido el BCP: no inventé el dominio.
  El primer correo real del BCP quedará `rechazado` con el motivo
  "firmado por: …"; ese dominio se agrega en `/admin/bancos` y luego **Reprocesar**.
- `paquetes_android` se llenó solo con los que la app ya escucha (Yape, Tunki, BCP,
  Interbank, BBVA, Scotiabank, Banco de la Nación, Pichincha, BanBif).
  Vacíos: **Plin, Bim, Ligo, Mibanco y las 5 cajas** (Maynas, Arequipa, Huancayo, Piura, Trujillo).

## Variables de entorno (Railway)

| Variable | Valor |
| --- | --- |
| `CORREO_ENTRANTE_SECRETO` | Aleatorio, 32+ caracteres (`python -c "import secrets;print(secrets.token_urlsafe(32))"`). El mismo en el Worker. Vacío = el endpoint responde 503. |
| `CORREO_DOMINIO` | `bancos.pagook.pro` (por defecto). |
| `CODIGO_REVISION_PLAY` | Código para el revisor de Google Play (6+ caracteres). Vacío = desactivado. |
| `EMPRESA_REVISION_PLAY` | `demo-revision` (por defecto). Slug de la empresa demo donde caen esos celulares. |

## Orden de despliegue

1. PGAdmin: `sql/correo_bancos_2026-10.sql` (va después de `egresos_privacidad_2026-10.sql`). Es idempotente; al final están las consultas de verificación.
2. Railway: agregar las variables de arriba.
3. Push a GitHub (incluye `dkimpy==1.1.8` y `dnspython==2.7.0` en `requirements.txt`).
4. Configurar Cloudflare (abajo).

## Cloudflare paso a paso

El correo de Hostinger (`@pagook.pro`, MX de Hostinger) **no se toca**: todo va en el subdominio.

1. Cloudflare → `pagook.pro` → **Email** → **Email Routing**. Si pide activar Email Routing
   para el dominio raíz, **no** aceptes que cambie los MX de `pagook.pro`. Ve a
   **Settings** → **Subdomains** (o "Add subdomain") y agrega `bancos`.
   Cloudflare crea los registros MX/TXT de `bancos.pagook.pro`; confirma que los MX de
   `pagook.pro` siguen siendo `mx1/mx2.hostinger.com`.
2. **Workers & Pages** → **Create** → **Worker** → nombre `pagook-correo-bancos`.
   Pega el código de `cloudflare/correo-bancos-worker.js` y despliega.
3. En el Worker → **Settings** → **Variables and Secrets**:
   - `PAGOOK_URL` (texto) = `https://pagook.pro/api/v1/correo/entrante`
   - `CORREO_ENTRANTE_SECRETO` (secreto) = el mismo de Railway
   - `CORREO_RESPALDO` (opcional) = un buzón que ya esté verificado en Email Routing →
     **Destination addresses**. Solo recibe el correo si PagoOK no responde.
4. Email Routing → **Routing rules** → **Catch-all address** del subdominio
   `bancos.pagook.pro` → acción **Send to a Worker** → `pagook-correo-bancos` → activar.
5. Prueba: desde cualquier correo, escribe a `egresos+prueba@bancos.pagook.pro`.
   En `/admin/bancos?token=…` → "Correos por revisar" no aparece (código inexistente →
   `descartado`), pero en los logs del Worker debe verse la llamada sin error 4xx/5xx.

El Worker nunca usa `setReject()`: en Cloudflare es un rebote permanente.

## Prueba manual

1. Panel de perusistemas → **Correo del banco** → copia la dirección.
2. Gmail → Configuración → **Reenvío y correo POP/IMAP** → **Agregar una dirección de reenvío** → pega la dirección.
3. Vuelve al panel (recarga): aparece "Gmail pide confirmar el reenvío. Tu código es …". Ingresa ese código en Gmail.
4. Gmail → crea un **filtro** `De: <remitente de alertas del BCP>` → **Reenviar a** la dirección de PagoOK.
5. Haz una compra pequeña con la tarjeta de **débito** BCP.
6. El primer correo quedará **rechazado** (el BCP aún no tiene dominio): en `/admin/bancos` → "Correos por revisar" verás "firmado por: …". Agrega ese dominio al BCP, guarda y toca **Reprocesar**.
7. Abre `/perusistemas/egresos`: el egreso aparece con **Origen ✉️ correo**. Las compras siguientes entran solas.

Outlook: usar **Redirigir** (no "Reenviar"), para que llegue la firma DKIM original del banco.

## Revisor de Google Play

Con `CODIGO_REVISION_PLAY` lleno, el revisor escribe ese código en **Vincular**: no vence,
sirve para varios celulares y siempre los vincula a la empresa `demo-revision`
(nunca a perusistemas). Igual debe aceptar el consentimiento. Esa empresa no se
puede eliminar desde el autoservicio. Al terminar la revisión, vacía la variable.

## Registro antiguo (sin código)

`POST /api/v1/dispositivos/registrar` sin `codigo_invitacion` responde **403**
"Esta versión de PagoOK se vincula con el código de tu panel…" si la app es 1.6.0
o mayor (`version_code >= 6000` o `app_version >= 1.6.0`). Las 1.5.x siguen igual. Un
celular que ya estaba registrado (misma `instalacion_id`) conserva su token al actualizarse.
La app 1.6.0 hoy solo manda `app_version` (versionName); conviene que también mande `version_code`.

## Retención

`scripts/purgar_crudos.py` borra a los 90 días el asunto, el texto y el MIME de
`correo_raw` (el egreso registrado se conserva). La propuesta de cambio a la Política
de Privacidad está en `docs/legal/propuesta-cambio-privacidad-correo.md` (no se aplicó).
