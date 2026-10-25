# PagoOK — Multi-empresa (zWeb-04, octubre 2026)

PagoOK atiende a varias empresas, cada una con sus pagos, celulares, equipo y
estado de cuenta separados. Solo backend: la app Android se adapta en
zStore-05. La app 1.5.x sigue funcionando igual y cae en `perusistemas`.

## 1. Inventario previo (cómo estaba)

| Tema | Hallazgo |
|---|---|
| Empresa fija en el código | No hay. El slug `perusistemas` lo envía la **app** al registrarse (`SeguroPrefs.EMPRESA_SLUG`) y las páginas lo reciben en la URL. |
| Registro de dispositivo | `POST /api/v1/dispositivos/registrar`: la empresa sale del `empresa_slug` del cuerpo, permitido solo si la empresa tiene `acepta_autoregistro` (hoy solo perusistemas) o con un código de invitación de push. |
| Heartbeat, inbound, pagos/buscar | La empresa sale del token del dispositivo. |
| Consulta de pagos / API v1 | La empresa sale de la API key (`cuenta_api.empresa_id`). |
| Webhooks | Por suscripción de la empresa (`suscripcion_webhook.empresa_id`). |
| `/perusistemas/ingresos`, `/egresos`, sus JSON, confirmación, "Mis cuentas" | **Públicas por slug, sin login** (para cualquier empresa). |
| `/admin/dispositivos/estado` | Token admin (`ADMIN_TOKEN`). |
| Tablas con `empresa_id` | empresa (es la tabla), dispositivo, notificacion_raw, pago_detectado, pago_uso, cuenta_api, suscripcion_webhook, codigos_invitacion, push_suscripciones, persona_cuenta, titular_confirmado_externo, local, metodo_pago, cuenta_bancaria, config_impresion, sesion_admin. |
| Tablas sin `empresa_id` | device_estado_actual/historico (vía dispositivo), entrega_evento (vía suscripción), vendedor_local (vía local), persona, pais, tributo, regimen_tributario, migracion. |
| Producción | 7 empresas (no solo perusistemas); ayf y selvaviva tienen 1 celular cada una; todos los pagos son de perusistemas. |
| API de RUC | Todo el ecosistema usa **apis.net.pe v2** (`GET https://api.apis.net.pe/v2/sunat/ruc?numero=`, `Authorization: Bearer $APIS_NET_PE_TOKEN`). Referencia: `C:\alertape\app\services\ruc_validator.py`. |
| WhatsApp | Única integración real: **WhatsApp Cloud API de Meta** en Te Atiendo (`C:\teatiendo\app\meta.py`). No había ningún envío de OTP; hace falta una plantilla AUTHENTICATION aprobada. |

## 2. Despliegue (en este orden)

1. **SQL**: en PGAdmin, `sql/multiempresa_2026-10.sql` completo (trae `BEGIN; … COMMIT;`). Después, la sección "VERIFICACIÓN POSTERIOR" del mismo archivo:
   - empresas: perusistemas `activa`, sin `prueba_fin`, `RUC 20615446565`, `PERU SISTEMAS PRO E.I.R.L.`; las otras 6, `activa`;
   - conteos por empresa: los pagos NO cambian (perusistemas 162 + los que hayan entrado);
   - `dispositivos_sin_empresa = 0`; 11 bancos de Perú; tablas nuevas en 0.
2. **Variables de entorno en Railway** (tabla de abajo). Como mínimo `PAGOOK_PEPPER`.
3. **Código**: subir por GitHub. No arrastrar `no-deploy/`.
4. **Pruebas manuales** (sección 4).

## 3. Variables de entorno nuevas

| Variable | Valor recomendado en producción | Para qué |
|---|---|---|
| `PAGOOK_ENTORNO` | `produccion` (o no definirla) | `desarrollo` escribe el OTP en el log y quita `Secure` a la cookie. Nunca en Railway. |
| `PAGOOK_PEPPER` | 32+ caracteres aleatorios (`python -c "import secrets;print(secrets.token_urlsafe(32))"`) | Hash de OTP y códigos de vinculación. Si se cambia, los códigos vigentes dejan de servir (vencen en minutos, no pasa nada). |
| `PAGOOK_URL_PUBLICA` | `https://pagook.pro` | Base de los enlaces `/unirse/<token>`. |
| `LEGACY_PUBLICO_PERUSISTEMAS` | `true` | La app 1.5.x abre `/perusistemas/ingresos` sin sesión. Pasar a `false` en zStore-05. |
| `WA_ACCESS_TOKEN` | El token del sistema de WhatsApp Business (como en Te Atiendo) | Envío del OTP. |
| `WA_PHONE_NUMBER_ID` | El phone_number_id del número que envía | Envío del OTP. |
| `WA_OTP_PLANTILLA` | Nombre de la plantilla AUTHENTICATION aprobada (ej. `pagook_codigo`) | Envío del OTP. |
| `WA_OTP_IDIOMA` | `es` (o el código con el que se aprobó la plantilla) | |
| `WA_OTP_BOTON` | `1` | Las plantillas AUTHENTICATION llevan botón "Copiar código". |
| `GRAPH_API_VERSION` | `v23.0` | |
| `SLUGS_DEMO` | `perusistemas` | Únicos slugs con pantalla pública `/{slug}` y `/api/v1/demo/{slug}`. Cualquier otro: 404. En `/testers` solo estos se enlazan. |
| `ADMIN_TOKEN` | (ya existe) | Ahora también protege `/admin/empresas/nuevas` (`?token=`). Si está vacío, la ruta queda cerrada. |
| `APIS_NET_PE_TOKEN` | El mismo token del ecosistema | Lo usa `scripts/gestion_empresas.py` (en la PC donde se corre el script). |

**Sin las variables `WA_*`, el ingreso al panel en producción responde `503 otp_no_disponible`.** Antes del primer piloto hay que crear y hacer aprobar en Meta una plantilla de categoría **AUTHENTICATION** (ej. "Tu código de PagoOK es {{1}}", con botón de copiar código).

## 4. Prueba manual

1. **Crear una empresa de prueba** (en tu PC, con `.env` apuntando a producción):
   ```
   python scripts/gestion_empresas.py crear --pais PE --id-fiscal <RUC real de prueba> \
       --dueno-nombre "Tu nombre" --dueno-celular +51XXXXXXXXX
   ```
   Revisa la simulación (razón social traída de SUNAT, slug, prueba hasta…) y repite con `--aplicar`.
   `python scripts/gestion_empresas.py listar` debe mostrarla en `prueba` con 7 días.
2. **Entrar al panel con OTP**: `https://pagook.pro/panel/login` → tu celular → código por WhatsApp. Debes ver el aviso "Estás en periodo de prueba: te quedan 7 días".
3. **Código de vinculación**: Dispositivos → "Vincular un celular" → aparece `XXX-XXX` y el QR (vence en 10 min). La app que lo use llega en zStore-05; mientras, se puede probar con:
   ```
   curl -X POST https://pagook.pro/api/v1/dispositivos/vincular -H "Content-Type: application/json" \
     -d '{"codigo":"XXX-XXX","instalacion_id":"prueba-1","consentimiento_version":"2026-10","consentimiento_fecha":"2026-10-23T10:00:00-05:00"}'
   ```
   Responde el token y los datos de la empresa; el segundo intento con el mismo código da 400.
4. **Invitar a un colaborador**: Equipo → rol "Cajero" → "Crear enlace" → abrir el enlace en otro navegador o celular → nombre + celular → código por WhatsApp → entra al panel como Cajero (no ve Dispositivos ni Equipo).
5. **Revocarlo**: Equipo → "Quitar acceso". En el navegador del cajero, cualquier acción lo manda al login en el acto.
6. **El Motorola sigue capturando en perusistemas**: hacer un Yape de S/ 1 al número de PSP y verlo en `https://pagook.pro/perusistemas/ingresos` (sigue pública mientras `LEGACY_PUBLICO_PERUSISTEMAS=true`) y en `/admin/dispositivos/estado` (heartbeat verde).
7. Al terminar: `python scripts/gestion_empresas.py suspender --empresa <slug> --aplicar` (o dejarla vencer).

## 5. Rutas cerradas antes del despliegue

| Ruta | Acceso ahora |
|---|---|
| `/admin/empresas/nuevas` (GET y POST) | Token de administrador (`ADMIN_TOKEN`), igual que `/admin/dispositivos/estado`. |
| `/flujo-caja/{slug}`, `/api/v1/flujo-caja/{slug}` | Sesión de esa empresa con permiso de reportes (dueño o contador). Excepción: perusistemas con `LEGACY_PUBLICO_PERUSISTEMAS=true`. |
| `/{slug}/receptores` y sus acciones (`codigo-nuevo`, `codigo/{id}/desactivar`, `{id}/revocar`) | Igual que flujo de caja. |
| `/api/v1/push/test/{slug}` | Sesión de esa empresa con rol de **dueño**. Excepción: perusistemas con `LEGACY_PUBLICO_PERUSISTEMAS=true`. |
| `/{slug}`, `/api/v1/demo/{slug}`, `/api/v1/demo/{slug}/buscar` | Solo slugs de `SLUGS_DEMO`; el resto, 404 (igual que una empresa inexistente). |

## 5. Contratos que NO cambiaron (solo se agregaron campos)

- `POST /api/v1/notificaciones/inbound`, `/api/v1/pagos/buscar|recientes|{id}/consumir`: iguales.
- `POST /api/v1/device/heartbeat`: misma respuesta + bloque `cuenta {estado, dias_restantes, puede_ampliar}`; acepta opcionales `consentimiento_version`, `consentimiento_fecha`, `pais`, `bancos_declarados`, `nivel_equipo`, `egresos_activo`.
- `POST /api/v1/dispositivos/registrar`: igual; también acepta esos opcionales.
- `POST /api/v1/pagos/consultar`: igual; **nuevo `402 cuenta_vencida`** si la cuenta está vencida o suspendida.
- `marcar-usado`, `reclamar`, `GET /api/v1/pagos`, webhooks: mismos contratos. Un pago marcado como usado desde el panel ya no aparece disponible en la API.
