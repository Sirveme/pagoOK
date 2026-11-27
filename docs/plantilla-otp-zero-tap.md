# Plantilla OTP zero-tap para la app (zWeb-16)

Plantilla nueva **`pagook_acceso_app`** (AUTHENTICATION, idioma `es`) con botón
**zero-tap**. En Android, WhatsApp le entrega el código directamente a PagoOK y
el usuario no tiene que leerlo ni escribirlo. Si el zero-tap no funciona, el
mensaje muestra el botón **Autocompletar** (one-tap). Si tampoco funciona,
muestra **Copiar código**.

- Solo la usa `POST /api/v1/app/acceso/solicitar`.
- El panel web (`/panel/login/otp`), las invitaciones y Te Atiendo **no cambian**:
  siguen con `WA_OTP_PLANTILLA`.
- Comparte la WABA y el token con Te Atiendo. Por eso es una plantilla **nueva**
  y no se toca ninguna existente.

> **Estado:** preparada, **sin crear**. Duilio la crea o aprueba su creación.

---

## 1. Revisar la plantilla actual (solo lectura)

No tengo acceso al token desde el repo: Railway no está vinculado y no hay
`.env` con `WA_*`. Para ver nombre, categoría, botones y estado de la plantilla
actual sin mostrar el token, corre esto en el proyecto de Railway:

```bash
railway run sh -c 'curl -s -G "https://graph.facebook.com/${GRAPH_API_VERSION:-v23.0}/<WABA_ID>/message_templates" \
  --data-urlencode "name=$WA_OTP_PLANTILLA" \
  --data-urlencode "fields=name,language,status,category,components" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN"'
```

`<WABA_ID>` es el `WA_WABA_ID` de Te Atiendo. PagoOK no tiene esa variable
porque solo envía mensajes.

Lo que el código asume hoy, en `app/services/otp.py` (`WhatsAppEnviador`):

| | Valor |
|---|---|
| Nombre | `WA_OTP_PLANTILLA` (según la doc, `pagook_codigo`) |
| Idioma | `WA_OTP_IDIOMA` = `es` |
| Categoría | AUTHENTICATION |
| Botones | OTP "Copiar código" (`WA_OTP_BOTON=1`) |
| Envío | `body` con `{{1}}` = código y `button` `sub_type=url` `index=0` con el código |

El envío de la plantilla zero-tap usa **el mismo payload**. Meta lo define así
para todas las plantillas de autenticación (copiar código, one-tap y zero-tap).
Solo cambia el nombre de la plantilla.

---

## 2. Datos que faltan antes de crearla

### Paquete: `pro.pagook.app`

Confirmado en el celular con la app instalada desde Play (27-nov-2026).
`google-services.json` dice `pro.pagook.app`, pero ese no es el paquete de
la app publicada. Si el paquete no coincidiera, el zero-tap fallaría sin dar
ningún error: WhatsApp mostraría solo "Copiar código".

### Hashes de firma (11 caracteres)

| Par | Clave | Hash | Estado |
|---|---|---|---|
| 1 | **Firma de apps de Google Play**, no la de subida | `<HASH_PLAY>` | Pendiente: lo trae el reporte de zStore-16 (app 1.9.0) |
| 2 | Debug (`%USERPROFILE%\.android\debug.keystore` de este PC) | `Z/ys4SOz6I6` | Calculado con `pro.pagook.app` |

El hash depende del paquete: si el paquete cambia, hay que calcularlo de nuevo.

Para calcular o verificar cualquier hash sin conexión:

```bash
python scripts/hash_firma_app.py --paquete pro.pagook.app --cert deployment_cert.der
```

- **Play:** Play Console > Integridad de la app > Firma de apps > Certificado de
  la clave de firma de apps > Descargar (`deployment_cert.der`).
- **Debug:**
  `"C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe" -exportcert -alias androiddebugkey -keystore %USERPROFILE%\.android\debug.keystore -storepass android -file debug.der`

El hash de debug solo sirve para APK compiladas con la clave de debug de **este**
PC. Una build de debug hecha en otra máquina tiene otro hash.

---

## 3. JSON exacto de la plantilla

```json
{
  "name": "pagook_acceso_app",
  "language": "es",
  "category": "AUTHENTICATION",
  "message_send_ttl_seconds": 300,
  "components": [
    { "type": "BODY", "add_security_recommendation": true },
    { "type": "FOOTER", "code_expiration_minutes": 5 },
    {
      "type": "BUTTONS",
      "buttons": [
        {
          "type": "OTP",
          "otp_type": "ZERO_TAP",
          "text": "Copiar código",
          "autofill_text": "Autocompletar",
          "zero_tap_terms_accepted": true,
          "supported_apps": [
            { "package_name": "pro.pagook.app", "signature_hash": "<HASH_PLAY>" },
            { "package_name": "pro.pagook.app", "signature_hash": "Z/ys4SOz6I6" }
          ]
        }
      ]
    }
  ]
}
```

- `code_expiration_minutes: 5` es la vigencia actual del OTP (`VIGENCIA_OTP` en
  `app/services/otp.py`). Si esa vigencia cambia, cambia también este valor y
  `message_send_ttl_seconds`.
- `message_send_ttl_seconds: 300`: si WhatsApp no logra entregar el mensaje en
  5 minutos, lo descarta, porque el código ya habría vencido.
- `add_security_recommendation`: añade el aviso "Por tu seguridad, no lo compartas".
- El cuerpo y el pie de una plantilla AUTHENTICATION los redacta Meta en español.
  No se puede poner texto propio.
- `zero_tap_terms_accepted: true` significa aceptar los
  [términos de zero-tap](https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates/zero-tap-authentication-templates)
  de Meta. Al crearla, Duilio acepta esos términos en nombre de PagoOK.
- `text` y `autofill_text` son obligatorios: WhatsApp los usa cuando el zero-tap
  no funciona.

## 4. Comando de creación (no lo corrí)

Guarda el JSON del paso 3, con los hashes ya puestos, como `plantilla.json`, y
corre:

```bash
railway run sh -c 'curl -s -X POST "https://graph.facebook.com/${GRAPH_API_VERSION:-v23.0}/<WABA_ID>/message_templates" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN" -H "Content-Type: application/json" \
  --data @plantilla.json'
```

Si Meta la acepta, responde `{"id": "...", "status": "PENDING" | "APPROVED", "category": "AUTHENTICATION"}`.
Las plantillas de autenticación suelen aprobarse en minutos.

**Permiso del token:** crear plantillas exige `whatsapp_business_management`.
Enviar mensajes exige solo `whatsapp_business_messaging`. Si el comando del paso
1 o este responden con error `200` o `10` (permiso), créala a mano:

1. WhatsApp Manager > **Plantillas de mensajes** > **Crear plantilla**.
2. Categoría **Autenticación**. Nombre `pagook_acceso_app`. Idioma **Español (es)**,
   no "Español (PER)".
3. Entrega del código: **Autocompletar sin tocar (zero-tap)**. Acepta los términos.
4. Agrega la app dos veces: paquete y hash de Play, y paquete y hash de debug.
5. Marca "Agregar recomendación de seguridad" y "Agregar vencimiento: 5 minutos".
6. Textos de los botones: "Copiar código" y "Autocompletar".
7. Enviar.

---

## 5. Activarla en PagoOK (cuando esté APPROVED)

1. Confirma el estado con el comando del paso 1 cambiando `name=pagook_acceso_app`.
2. Opcional: envía una prueba solo con la plantilla nueva, sin respaldo:
   `railway run python scripts/probar_otp.py --celular +51XXXXXXXXX --app`
   (con `OTP_PLANTILLA_APP` ya cargada).
3. Carga en Railway `OTP_PLANTILLA_APP=pagook_acceso_app`.

No se necesita SQL ni deploy para activarla o desactivarla. Para volver atrás,
borra la variable.

**Respaldo automático:** si Meta rechaza la plantilla nueva con un error de
plantilla (`132xxx`: no existe, no aprobada, pausada o parámetros), el mismo
código se reenvía con `WA_OTP_PLANTILLA`. La plantilla nueva se salta durante
10 minutos (`PAUSA_PLANTILLA_APP`) para no intentar dos envíos en cada pedido.
El log muestra
`plantilla de la app 'pagook_acceso_app' rechazada (code=...); se usa '...'`.
Los errores que no son de plantilla (número sin WhatsApp, token) no se
reenvían.

## 6. Lo que tiene que hacer la app (zStore-16)

El backend no cambia el contrato: `solicitar` y `verificar` son los mismos. Para
que funcione el zero-tap, la app debe:

- Antes de llamar a `solicitar`, enviar el handshake a WhatsApp: un
  `PendingIntent` a `com.whatsapp` y `com.whatsapp.w4b` con la acción
  `com.whatsapp.otp.OTP_REQUESTED`.
- Recibir el código con un receptor de `com.whatsapp.otp.OTP_RETRIEVED` (extra
  `code`) y mandarlo a `verificar` como si el usuario lo hubiera escrito.
- Mantener la casilla manual como respaldo.
