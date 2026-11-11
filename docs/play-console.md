# PagoOK — Play Console (acceso de revisión, seguridad de datos y permisos)

Documento de apoyo para completar la ficha de Google Play Console. Basado en el
código real del backend (`app/`) y en `docs/legal/privacidad.md`. Los valores
secretos no van aquí: Duilio reemplaza los marcadores `<CÓDIGO>` y `<CELULAR>`
en Play Console, y carga las variables en Railway (ver el final).

> **Un solo código para el revisor.** Para simplificar, Duilio usa el **mismo**
> valor `<CÓDIGO>` como código de vinculación de la app (`CODIGO_REVISION_PLAY`)
> y como código del panel (su hash va en `REVISION_PANEL_CODIGO_HASH`). El
> `<CELULAR>` es el celular ficticio del revisor (`REVISION_PANEL_CELULAR`).

---

## 1. App access (instructions for the reviewer) — English

PagoOK links each phone to a business using a one-time code, so the reviewer
needs a dedicated review code. No username/password login exists.

**To test the app (reading payment notifications):**

1. Open PagoOK and allow **Notification access** when prompted (Android
   Settings → Notification access → PagoOK). This is required for the core
   feature; see the justification in section 3.
2. On the **"Vincular" (Link device)** screen, enter the review code:
   **`<CÓDIGO>`**. This code links the phone to the demo business "Revisión
   Google Play". It does not expire and can be reused.
3. In the app, tap **"Simular pago de prueba" (Simulate a test payment)** to
   generate sample income without any real bank notification. Test payments are
   clearly labeled **"PRUEBA"** and never affect real data, reports or webhooks.

**To view the payments on the web panel ("Ver mis pagos"):**

1. Go to **https://pagook.pro/panel**.
2. Enter the phone number **`<CELULAR>`** and tap to continue.
3. Enter the review code **`<CÓDIGO>`** to sign in. The account is read-only
   (role *contador*): the reviewer can see payments and reports but cannot
   delete data or invite users. The demo list is pre-seeded with sample
   payments over the last 7 days, all labeled "PRUEBA".

No real customer data is involved: the review account ("revision-play") is
isolated, never expires, and is excluded from all business metrics.

---

## 2. Data safety (Seguridad de los datos)

**Qué se recopila.** Solo las notificaciones de las apps de **bancos y
billeteras que el usuario configura** (ingresos y egresos). De cada aviso se
guardan: la app que lo emitió, el título y el texto del aviso, la fecha y hora,
y los datos que PagoOK extrae (monto, contraparte, método, banco). Además, datos
de la empresa (RUC/razón social), del dispositivo (modelo, versión) y del
usuario del panel (celular). No se leen avisos de otras apps (mensajes, correo,
redes), ni contraseñas, claves ni OTP.

**Si se comparte.** Los pagos **confirmados** se envían a la cuenta de la empresa
y a los sistemas que el propio usuario conecta por API/webhook (p. ej.
facturación). No se venden datos ni se usan para publicidad.

**Cifrado en tránsito.** Toda la comunicación entre la app y el servidor viaja
cifrada por **HTTPS/TLS**.

**Eliminación y cómo pedirla.** El usuario puede **descargar** y **eliminar**
todos sus datos desde la propia app o en **https://pagook.pro/eliminar-cuenta**;
la eliminación es automática y ocurre en segundos (confirma escribiendo la frase
de seguridad). Desinstalar la app no borra lo ya enviado: para eso se usa la
opción de eliminación.

**Retención.** El **texto completo del aviso** (texto crudo) se conserva **90
días** y luego se borra automáticamente. El resto se conserva mientras la cuenta
esté activa y se borra al solicitar la eliminación.

**Telemetría de ML Kit (Code Scanner).** La app usa **Google ML Kit Code
Scanner** (escaneo de códigos QR). Este componente de Google puede enviar
**datos de diagnóstico/telemetría a Google** para su funcionamiento. Declararlo
en Data safety como recopilación por parte de una librería de terceros (Google),
con finalidad de funcionalidad de la app, según lo que señaló el informe de
Android. *Duilio: confirmar en la ficha el detalle exacto que Google exige para
ML Kit.*

---

## 3. Declaración del acceso a notificaciones

### Español
PagoOK usa el acceso a notificaciones (`BIND_NOTIFICATION_LISTENER_SERVICE`)
para su función central: confirmar pagos.

- Lee las notificaciones de las apps de **bancos y billeteras configuradas** por
  el usuario: tanto **ingresos** (pagos recibidos) como **egresos** (pagos y
  consumos realizados).
- De cada aviso **extrae** monto, contraparte (titular), fecha y hora.
- Las **envía a la cuenta de la empresa** del usuario para registrar el pago.
- No lee notificaciones de ninguna otra app.

### English
PagoOK uses notification access (`BIND_NOTIFICATION_LISTENER_SERVICE`) for its
core feature: confirming payments.

- It reads notifications from the **bank and wallet apps configured** by the
  user: both **incoming** payments and **outgoing** payments/charges.
- From each notification it **extracts** amount, counterparty, date and time.
- It **sends them to the user's business account** to record the payment.
- It does not read notifications from any other app.

---

## 4. Justificación de permisos especiales

### `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`

**Español.** La confirmación de pagos debe ser **inmediata y continua**: el
negocio necesita saber en segundos si un pago llegó. Las optimizaciones de
batería de Android pueden suspender el servicio en segundo plano y hacer que la
app deje de leer las notificaciones (el equipo queda "vivo pero sordo"). Pedir
excluir a PagoOK de la optimización de batería mantiene el servicio activo para
no perder ningún aviso de pago. El usuario puede negarlo; es opcional.

**English.** Payment confirmation must be **immediate and continuous**: the
business needs to know within seconds whether a payment arrived. Android battery
optimizations can suspend the background service, so the app stops reading
notifications (the device is "alive but deaf"). Asking to exclude PagoOK from
battery optimization keeps the service running so no payment notification is
missed. The user can decline; it is optional.

### Foreground Service tipo `specialUse`

**Español.** PagoOK corre un **servicio en primer plano** (foreground service)
para escuchar las notificaciones de pago de forma continua mientras el negocio
opera. El tipo declarado es **`specialUse`** porque la función —escuchar
notificaciones de apps de bancos/billeteras configuradas por el usuario para
confirmar pagos— no encaja en los tipos estándar de FGS de Android. El servicio
muestra una notificación persistente informando que PagoOK está activo.

**English.** PagoOK runs a **foreground service** to continuously listen for
payment notifications while the business operates. The declared type is
**`specialUse`** because the function —listening to notifications from
user-configured bank/wallet apps to confirm payments— does not fit Android's
standard FGS types. The service shows a persistent notification indicating that
PagoOK is active.

---

## 5. Revisión de `docs/legal/privacidad.md` (NO se modificó)

Qué **sí** menciona hoy la política:

- ✅ **Lectura de notificaciones** de apps de bancos/billeteras (secciones "En 30
  segundos" y 2).
- ✅ **Egresos** (pagos realizados y consumos con tarjeta), con opción de
  desactivar su registro (sección 3, ingresos y egresos).
- ✅ **Retención de 90 días** del texto completo del aviso (sección de retención).
- ✅ **Cifrado en tránsito (HTTPS)** y **eliminación** de datos (secciones 9 y 10).

Qué **falta** o conviene reforzar (pendiente de decisión; no se tocó el archivo):

- ⚠️ **Avisos no reconocidos.** La política describe que se guarda el aviso de la
  app financiera, pero **no aclara** que también se conservan (como texto crudo,
  90 días) los avisos de esas apps que **no** se reconocen como un pago
  (notificaciones que el parser marca "ignorada"/"fallida"). Conviene decirlo
  explícitamente.
- ⚠️ **Telemetría de ML Kit (Code Scanner).** La política **no menciona** que la
  librería de escaneo de códigos de Google (ML Kit) puede enviar datos de
  diagnóstico a Google. Si se declara en Data safety, debería reflejarse también
  en la política.
- ⚠️ **Base del acceso a notificaciones en Play.** Conviene que la política
  enlace o resuma la declaración de "notification access" exigida por Google
  (sección 3 de este documento), para que la ficha y la política sean coherentes.

*(No modificar `privacidad.md` como parte de este trabajo; esta lista es para
decidir el cambio por separado.)*

---

## Variables de Railway (solo nombres)

| Variable | Para qué |
|---|---|
| `EMPRESA_REVISION_PLAY` | Slug de la cuenta de revisión (`revision-play`). La bandera `es_cuenta_revision` ya la resuelve; esta variable es respaldo. |
| `CODIGO_REVISION_PLAY` | Código `<CÓDIGO>` que el revisor escribe en "Vincular" (app). 6+ caracteres. |
| `REVISION_PANEL_CELULAR` | Celular ficticio `<CELULAR>` del revisor para el panel. Debe ser EXACTO al sembrado en el SQL. |
| `REVISION_PANEL_CODIGO_HASH` | `sha256(hex)` de `<CÓDIGO>` (el código del panel). Generar: `python -c "import hashlib;print(hashlib.sha256(b'<CÓDIGO>').hexdigest())"`. |
