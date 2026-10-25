# PagoOK — Egresos, /privacidad y /eliminar-cuenta (zWeb-05, octubre 2026)

## 1. Inventario de egresos reales (producción, solo lectura)

| raw | Fecha | App | Texto (anonimizado) | Tipo | Monto |
|---|---|---|---|---|---|
| 61 | 16/05 | Interbank | "Se realizó un pago recurrente de $26.19 en HOSTINGER* HOSTINGER.C con tu Tarjeta de Débito." | `cargo_recurrente` | USD 26.19 |
| 75 | 22/05 | Interbank | "Plineaste S/ 1.00 a A. R. C. N." | `envio_billetera` | PEN 1.00 |
| 76 | 22/05 | Interbank | "Plineaste S/ 1.00 a A. R. C. N." | `envio_billetera` | PEN 1.00 |
| 84 | 26/05 | Interbank | "Realizaste un consumo de S/.50.00 en COMPAÑÍA OPERADORA… con tu Tarjeta de Crédito." | `consumo_tarjeta` | PEN 50.00 |
| 91 | 28/05 | Interbank | "Realizaste un consumo de S/.42.00 en Chapos con tu Tarjeta de Crédito." | `consumo_tarjeta` | PEN 42.00 |
| 94 | 28/05 | Interbank | "Realizaste un consumo de S/.70.70 en EPS SEDALORETO… con tu Tarjeta de Crédito." | `consumo_tarjeta` | PEN 70.70 |

- En los últimos 30 días no hay ningún otro aviso bancario que no sea ingreso.
- **El "Yape de S/1 enviado hoy desde el Motorola" no llegó al servidor.** El Motorola es hoy el dispositivo 17 (app 1.5.1, egresos activados, Yape declarado). Su único aviso del 23/10 (13:16, hora de Lima) es un **ingreso** ("…te envió un pago por S/ 1").
- **En 142 avisos de Yape desde mayo hay 0 "Yapeaste":** Yape no notifica a quien paga. Un Yape enviado no se puede capturar por notificación.

## 2. Formatos soportados

| Banco / app | Formato | Estado |
|---|---|---|
| Interbank | "Plineaste S/ X a NOMBRE" | Verificado con avisos reales |
| Interbank | "Realizaste un consumo de S/.X en COMERCIO con tu Tarjeta de Crédito." | Verificado |
| Interbank | "Se realizó un pago recurrente de $X en COMERCIO con tu Tarjeta de Débito." | Verificado (USD) |
| Yape | "Yapeaste …" | Sin soporte comprobado: Yape no envía ese aviso |
| BCP, BBVA, Scotiabank, Banco de la Nación, Pichincha, BanBif | — | Sin ejemplos reales: solo los detectan los verbos genéricos que el parser ya tenía, sin garantía |
| `transferencia_enviada`, `retiro_cajero`, `pago_servicio` | — | Existen como tipos, pero no hay ningún aviso real todavía |

Reglas aplicadas:
- **Ingreso primero:** la señal de ingreso se evalúa antes que la de egreso.
- **Sin monto legible no hay egreso.**
- **Promociones, recordatorios y avisos de seguridad nunca son egreso.**
- **Últimos 4 de la tarjeta:** solo si el banco ya los muestra enmascarados; nunca se guarda el número completo.

**Ningún ingreso cambió.** Comparé el parser antes y después sobre las 169 notificaciones de producción: los mismos 159 ingresos y los mismos 6 egresos, que ahora llevan su tipo.

## 3. Despliegue (en este orden)

1. **SQL**: `sql/egresos_privacidad_2026-10.sql` en PGAdmin (`BEGIN; … COMMIT;`) y luego su verificación posterior.
   **Va antes que el código:** el código nuevo lee `pago_detectado.tipo_egreso`. Si el código se despliega primero, la ingesta falla hasta aplicar el SQL.
2. **Dependencia nueva**: `markdown==3.7` ya está en `requirements.txt`; Railway la instala sola al desplegar.
3. **Código**: subir por GitHub (sin `no-deploy/`). No hay variables de entorno nuevas.
4. **Reproceso** de los 6 egresos históricos (paso 4.2 de la prueba manual).
5. **Purga de crudos**: programarla cuando lo decidas (sección 5).

## 4. Prueba manual

1. **Las dos páginas, desde el celular:**
   - `https://pagook.pro/privacidad`: índice, tablas legibles y la versión 2026-10-v1.
   - `https://pagook.pro/eliminar-cuenta`: qué se borra, el botón "Entrar para descargar y eliminar" y el formulario.
2. **Simular el reproceso de perusistemas** (en tu PC, con `.env` de producción):
   `python scripts/reprocesar_egresos.py --empresa perusistemas --desde 2026-05-01`
   Debe listar 6 egresos: raws 61, 75, 76, 84, 91 y 94.
3. **Aplicarlo**: el mismo comando con `--aplicar`. Luego, en `https://pagook.pro/perusistemas/egresos` con rango "30 días" no aparece nada, porque son de mayo. Para verlos, entra con sesión de dueño y usa el filtro de fechas, o descarga el CSV.
4. **Un egreso nuevo**: un Yape enviado **no va a aparecer**, porque Yape no avisa. Para probar, haz un **Plin desde Interbank** o un consumo con tarjeta Interbank desde el celular con PagoOK (switch "Pagos que realizas" encendido) y míralo en `/perusistemas/egresos`.
5. **Solicitud de eliminación de prueba**: llena el formulario de `/eliminar-cuenta` y revísala en `https://pagook.pro/admin/eliminaciones?token=<ADMIN_TOKEN>`.
6. **Crear y eliminar una empresa de prueba con el script**:
   ```
   python scripts/gestion_empresas.py crear --pais PE --id-fiscal <RUC> --dueno-nombre "Prueba" --dueno-celular +51... --aplicar
   python scripts/gestion_empresas.py eliminar --empresa <slug>            # simulación: muestra lo que borraría
   python scripts/gestion_empresas.py eliminar --empresa <slug> --aplicar
   ```
   Si atiendes una solicitud del formulario, agrega `--solicitud <id>`: queda como `ejecutada` y se borran sus datos personales.

## 5. Retención de 90 días (purga del texto crudo)

`python scripts/purgar_crudos.py --dias 90` cuenta; con `--aplicar` borra solo `titulo` y `texto` de las notificaciones con más de 90 días. No toca pagos ni egresos.

Programarlo en Railway (no está programado):
1. New → Empty Service (o Cron Job) con el mismo repositorio.
2. Variables: las mismas del servicio web (al menos `DATABASE_URL`).
3. Start Command: `python scripts/purgar_crudos.py --dias 90 --aplicar`
4. Cron Schedule: `0 8 * * *` (todos los días a las 03:00 de Lima; Railway usa UTC).

## 6. URLs para Google Play

- Política de privacidad: **https://pagook.pro/privacidad**
- Eliminación de cuenta y datos: **https://pagook.pro/eliminar-cuenta**
