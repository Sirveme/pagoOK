# PagoOK — API pública v1

Contrato para que otros productos del ecosistema (ej. **alerta.pe**) consulten y
reclamen pagos detectados por PagoOK. Diseño **aditivo**: no toca la ingesta
Android, el parser, la PWA Caja ni el panel admin.

## Autenticación

Todas las llamadas requieren el header **`X-API-Key`** con una credencial emitida
para una empresa. La key se entrega **una sola vez** al crearla (en BD solo se
guarda su hash SHA-256).

- Falta header o key inválida/inactiva → **401**.
- Cada credencial solo ve y reclama pagos de **su** empresa (aislamiento por
  `empresa_id`).

> **Consumo backend-a-backend.** La API Key vive en el backend de alerta.pe
> (variable de entorno), **nunca** en el navegador del cliente. Por eso no se
> habilita CORS abierto: no se consume desde el front.

## Rate limit

Límite por credencial: **120 solicitudes por minuto** (ventana fija). Al exceder
→ **429** con `{"detail": "..."}`. Reintentar tras un minuto.

## Endpoints

### `GET /api/v1/pagos`

Lista los pagos visibles para la cuenta: **no reclamados** o **reclamados por
ella misma**, ordenados por fecha descendente.

Query params (todos opcionales):

| Param | Tipo | Descripción |
|---|---|---|
| `monto` | decimal | Monto exacto (ej. `15.00`) |
| `desde` | ISO-8601 | Límite inferior de `recibido_en` (`>=`) |
| `hasta` | ISO-8601 | Límite superior de `recibido_en` (`<=`) |
| `limit` | int 1–200 | Máximo de resultados (default 50) |
| `offset` | int ≥0 | Desplazamiento para paginar (default 0) |

Respuesta `200`:

```json
{
  "pagos": [
    {"id": "123", "nombre": "Juan Perez", "monto": 15.0, "hora": "2026-06-07T14:32:00", "banco": "yape"}
  ],
  "limit": 50,
  "offset": 0,
  "count": 1
}
```

- `banco`: `yape` | `plin` | nombre del banco (transferencias) | `null`.
- `nombre`: titular del pago, o `null` si vino vacío.
- Fecha/monto inválidos en los filtros → **400**.

**Paginar:** subir `offset` en pasos de `limit` hasta recibir `count < limit`.

### `POST /api/v1/pagos/{id}/reclamar`

Reclama un pago de forma **atómica e idempotente**. Permite que varios
consumidores compitan por el mismo pool sin doble-contar.

| Código | Significado |
|---|---|
| `200` | Reclamado OK (o ya reclamado por **esta misma** cuenta → idempotente) |
| `409` | Ya reclamado por **otra** cuenta |
| `404` | No existe o no pertenece a la empresa de la credencial |

Respuesta `200`:

```json
{"id": "123", "reclamado": true, "reclamado_por": 7}
```

> `reclamado_por` es independiente de `consumido` (que usa internamente la PWA
> Caja). Un pago puede estar consumido por la caja y aun así ser reclamable vía
> API, y viceversa.

## Emisión de credenciales

Sin UI; vía script CLI (no imprime la key en logs de producción):

```bash
python scripts/gestion_cuentas.py crear --empresa-id <ID> --nombre "alerta.pe"
```

Por defecto imprime la key + el `INSERT` para PGAdmin (regla SQL-primero). Con
`--aplicar` lo ejecuta contra `DATABASE_URL`. Rotar: `rotar --cuenta-id <ID>`.

## Esquema en BD

DDL aditivo e idempotente en [`sql/api_v1.sql`](../sql/api_v1.sql): tabla
`cuenta_api` + columnas `reclamado_por`/`reclamado_en` en `pago_detectado`
(con FK e índices). Se aplica **a mano** en PGAdmin (Railway), no con migraciones
automáticas.
