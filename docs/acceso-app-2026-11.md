# Contrato: entrar con el número y autorregistro desde la app (zWeb-14)

Base: `https://pagook.pro`. Todo es JSON (`Content-Type: application/json`).
Servidor: `app/api/router_acceso_app.py`. Tests: `tests/test_acceso_app.py`.

**Supuesto:** el celular donde se instala PagoOK es del **dueño**. Solo un dueño
vincula su celular con su número. Cajeros, contadores y celulares dedicados
(MITA, clínicas) siguen con **«Tengo un código»** → `POST /api/v1/dispositivos/vincular`,
que **no cambia**. El código de revisión de Google (`CODIGO_REVISION_PLAY`) tampoco.

## Errores (todos los endpoints de este documento)

```json
{"detail": {"codigo": "celular_invalido", "mensaje": "Revisa el número: en Perú debe tener 9 dígitos y empezar con 9 (ej. 987654321), sin el +51."}}
```

Muestra `mensaje` tal cual. Decide con `codigo`. Valores posibles:

| HTTP | `codigo` | Cuándo | Qué hace la app |
|---|---|---|---|
| 400 | `json_invalido` | Body que no es un objeto JSON | Error de programación |
| 400 | `pais_no_disponible` | País fuera de la configuración | Volver al selector de país |
| 400 | `celular_invalido` | No cumple el formato del país | Marcar el campo |
| 400 | `consentimiento_requerido` | Falta `consentimiento_version` en verificar | Error de programación |
| 400 | `codigo_invalido` | OTP incorrecto, vencido o sin intentos (máx. 5) | Dejar reintentar o pedir otro |
| 400 | `ticket_invalido` | Ticket vencido, ya usado, inventado, de otro tipo o de otro número | Volver a la pantalla del número |
| 400 | `nombre_invalido` / `nombre_comercial_invalido` / `rubro_invalido` | Validación del registro | Marcar el campo (el ticket NO se gasta) |
| 400 | `pais_no_coincide` | `pais` de registro/crear ≠ país del número verificado | Error de programación |
| 400 | `empresa_invalida` | `empresa_id` no numérico | Error de programación |
| 403 | `sin_permiso` | `empresa_id` que no es del dueño | Volver a la lista |
| 403 | `registro_cerrado` | Autorregistro apagado al crear | Mostrar `mensaje` |
| 404 | `pais_no_disponible` | `GET config?pais=XX` desconocido | Volver al selector |
| 409 | `ya_registrado` | El número ya tiene cuenta | Volver a la pantalla del número |
| 429 | `demasiados_intentos` | Límite por IP | Esperar |
| 429 | `demasiados_codigos` | Más de 3 códigos para el número en 10 min (cuenta también los del panel) | Esperar 10 min |
| 502 | `envio_fallido` | WhatsApp rechazó el envío | Reintentar más tarde |
| 503 | `otp_no_disponible` | WhatsApp no configurado en el servidor | Reintentar más tarde |

Límites por IP, en ventanas de 10 minutos: `solicitar` 10, `verificar` 20, `vincular` y `registro/crear` 20.

---

## 0. `GET /api/v1/app/registro/config[?pais=XX]`

Sin `pais`, devuelve todos los países (para el selector):

```json
{
  "paises": [
    {"pais": "PE", "nombre": "Perú", "prefijo": "+51",
     "celular": {"digitos": 9, "empieza_con": "9", "patron": "^9\\d{8}$", "ejemplo": "987654321"}},
    {"pais": "BO", "nombre": "Bolivia", "prefijo": "+591",
     "celular": {"digitos": 8, "empieza_con": null, "patron": "^\\d\\d{7}$", "ejemplo": "71234567"}}
  ],
  "rubros": [{"clave": "transporte_personas", "nombre": "Transporte de personas",
              "incluye": "Taxi, mototaxi, transporte público", "icono": "local_taxi", "color": "#F59E0B"}],
  "autoregistro_activo": true,
  "prueba_dias": 14,
  "otp": {"canal": "whatsapp", "digitos": 6, "vence_en_seg": 300}
}
```

Con `?pais=PE`, devuelve el objeto de ese país al primer nivel más `rubros`,
`autoregistro_activo`, `prueba_dias` y `otp`.

- **Países hoy:** PE, BO, CO, EC, MX, CL.
- **Rubros**, en este orden. Cada uno trae `clave`, `nombre`, `incluye` (texto de ayuda bajo el nombre), `icono` (nombre de Material Symbols) y `color` (hex):

  | `clave` | `nombre` | `incluye` | `icono` | `color` |
  |---|---|---|---|---|
  | `transporte_personas` | Transporte de personas | Taxi, mototaxi, transporte público | `local_taxi` | `#F59E0B` |
  | `tecnico_profesional` | Técnico o profesional | Técnicos de servicios, profesionales independientes | `handyman` | `#2563EB` |
  | `peluqueria_barberia` | Peluquería o barbería | Peluquería, barbería, estética | `content_cut` | `#DB2777` |
  | `salud` | Salud | Profesionales de la salud, consultorios | `medical_services` | `#059669` |
  | `bodega_tienda` | Bodega o tienda | Bodega, minimarket, tienda de barrio | `storefront` | `#EA580C` |
  | `restaurante_comida` | Restaurante o comida | Restaurante, venta de comida, delivery | `restaurant` | `#DC2626` |
  | `comercio` | Comercio | Venta de productos, emprendimientos | `shopping_bag` | `#7C3AED` |
  | `otro` | Otro | Cualquier otro trabajo independiente | `more_horiz` | `#6B7280` |

  Si llega un `icono` que la app no conoce (un rubro nuevo), debe usar un ícono genérico: los rubros pueden crecer sin publicar otra versión.
- La app **no** debe tener estas listas fijas en el código: agregar un país solo cambia el servidor.
- `patron` se aplica al número local, sin prefijo. Es solo una ayuda: el servidor valida de nuevo.

## 1. `POST /api/v1/app/acceso/solicitar`

```json
{"pais": "PE", "celular": "987654321"}
```

`celular` acepta el número local o con prefijo, con o sin espacios: `987 654 321`, `+51 987654321`, `51987654321`.

**200**, igual exista o no el número:

```json
{"ok": true, "modo": "otp", "mensaje": "Te enviamos un código por WhatsApp al +51 ••• ••• 321.", "vence_en_seg": 300}
```

El código llega con la plantilla zero-tap `OTP_PLANTILLA_APP` (o la de siempre como respaldo); el contrato no cambia. Ver `docs/plantilla-otp-zero-tap.md`.

**Revisor de Google**: si el número es el ficticio de revisión, no se envía nada por WhatsApp:

```json
{"ok": true, "modo": "revision", "mensaje": "Ingresa el código de revisión.", "vence_en_seg": null}
```

Con `modo: "revision"`, la casilla del código es **alfanumérica y larga**, no de 6 dígitos.

## 2. `POST /api/v1/app/acceso/verificar`

```json
{
  "pais": "PE", "celular": "987654321", "codigo": "123456",
  "consentimiento_version": "2026-11-v3", "consentimiento_fecha": "2026-11-22T10:00:00-05:00",
  "instalacion_id": "uuid-de-la-instalacion", "manufacturer": "motorola", "modelo": "moto g06",
  "android_version": "15", "app_version": "1.8.0", "version_code": 8000,
  "bancos_declarados": ["yape"], "nivel_equipo": "verificado", "egresos_activo": true
}
```

- **Obligatorios:** `pais`, `celular`, `codigo` y `consentimiento_version`, que es la versión de `GET /api/v1/privacidad/version`.
- **Opcionales:** todos los demás, que son los mismos de `/dispositivos/vincular`. Si falta `consentimiento_fecha`, se usa la hora del servidor.
- Si falta el consentimiento, la respuesta es 400 **sin gastar el código**.

**200** con un `estado`. La app hace un `switch` sobre `estado`:

**`vinculado`**: es dueño de una sola empresa, o es el revisor de Google. Ya quedó vinculado.

```json
{
  "estado": "vinculado",
  "token": "tok_...", "dispositivo_id": 42,
  "empresa": {"razon_social": "TALLER B", "id_fiscal": "20100000002", "id_fiscal_tipo": "RUC", "pais": "PE",
              "id": 7, "nombre": "TALLER B", "slug": "taller-b", "tipo": "negocio"},
  "cuenta": {"estado": "prueba", "dias_restantes": 5, "puede_ampliar": false}
}
```

Guarda `token` igual que el de `/dispositivos/vincular`, como `X-Device-Token`.

**`elegir_empresa`**: es dueño de varias empresas.

```json
{"estado": "elegir_empresa", "ticket": "tk_...", "vence_en_seg": 600,
 "empresas": [{"empresa_id": 3, "nombre": "CLINICA A", "pais": "PE"},
              {"empresa_id": 5, "nombre": "BOTICA C", "pais": "PE"}]}
```

→ La app sigue con el paso 3.

**`nuevo`**: el número no tiene cuenta y el autorregistro está encendido.

```json
{"estado": "nuevo", "ticket": "tk_...", "vence_en_seg": 900, "pais": "PE", "prueba_dias": 14}
```

→ La app sigue con el paso 4. Tiene 15 minutos.

**`registro_cerrado`**: número nuevo con el autorregistro apagado.

```json
{"estado": "registro_cerrado", "mensaje": "Por ahora el registro es por invitación. Escríbenos por WhatsApp."}
```

**`sin_permiso`**: es cajero, contador o almacén, y no dueño.

```json
{"estado": "sin_permiso", "mensaje": "Tu número está registrado como cajero de CLINICA A. Solo el dueño vincula celulares con su número. Pídele un código de vinculación desde el panel (Celulares) e ingrésalo en «Tengo un código»."}
```

→ Ofrece el botón «Tengo un código».

**`cuenta_desactivada`**: el usuario está desactivado.

```json
{"estado": "cuenta_desactivada", "mensaje": "Tu cuenta está desactivada. Escríbenos por WhatsApp."}
```

El código OTP es de un solo uso. Si el estado no es `vinculado`, el código ya se gastó igual: para volver a empezar hay que pedir otro.

## 3. `POST /api/v1/app/acceso/vincular` (dueño de varias)

```json
{"ticket": "tk_...", "empresa_id": 5, "celular": "987654321"}
```

- `celular` es opcional. Si se envía, debe ser el mismo que se verificó.
- **200**: el mismo cuerpo que `estado: "vinculado"`.
- **403** `sin_permiso`: la empresa no es suya, y el ticket sigue sirviendo.
- **400** `ticket_invalido`: el ticket venció o ya se usó.

## 4. `POST /api/v1/app/registro/crear` (número nuevo)

```json
{"ticket": "tk_...", "pais": "PE", "nombre": "Rosa Quispe", "rubro": "transporte_personas",
 "nombre_comercial": "Taxi Rosa", "celular": "987654321"}
```

- `nombre`: de 2 a 60 caracteres, con al menos una letra. Se unen los espacios repetidos. No se valida contra ninguna fuente.
- `nombre_comercial`: opcional, de 2 a 80 caracteres.
- `rubro`: una clave de la configuración.
- `pais`: el mismo del número verificado.
- `celular`: opcional. Si se envía, debe ser el mismo que se verificó.

**200**: igual que `vinculado`, pero con `"estado": "creado"`. La empresa queda así:

```json
"empresa": {"razon_social": "Rosa Quispe", "id_fiscal": null, "id_fiscal_tipo": null, "pais": "PE",
            "id": 12, "nombre": "Taxi Rosa", "slug": "taxirosa-a1b2c3", "tipo": "independiente"},
"cuenta": {"estado": "prueba", "dias_restantes": 14, "puede_ampliar": false}
```

- `nombre` es el nombre visible: el comercial si lo dio, si no el de la persona.
- Sin RUC ni documento de identidad.
- Prueba de 14 días (`PRUEBA_DIAS_INDEPENDIENTE`). La ampliación única de +15 días sigue igual: `POST /api/v1/cuenta/ampliar-prueba` cuando `puede_ampliar` es true.
- Con ese mismo número, la persona entra al panel web: `/panel/login`.

**Errores:**

- **400** de validación: el ticket **no** se gasta y la persona puede corregir el dato.
- **400** `ticket_invalido`: el ticket venció, ya se usó o es de otro número.
- **403** `registro_cerrado`: el autorregistro se apagó después del paso 2.
- **409** `ya_registrado`: el número ya tiene cuenta.

## Flujo sugerido en la app

```
Bienvenida → [Entrar con mi número]  (principal)
           → [Tengo un código]        (secundario: /dispositivos/vincular, sin cambios)

País (config) → Número → solicitar → Código (6 dígitos, o alfanumérico si modo=revision)
  → verificar → vinculado          → Listo
              → elegir_empresa     → lista → vincular → Listo
              → nuevo              → Nombre + Rubro (+ nombre del negocio) → registro/crear → Listo
              → registro_cerrado / sin_permiso / cuenta_desactivada → mensaje (+ «Tengo un código»)
```
