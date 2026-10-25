# PagoOK — Contrato de eventos v1 (webhooks salientes)

PagoOK **avisa** a tu sistema cuando entra un pago. PagoOK no maneja productos ni
inventario: qué hacer con el aviso (cerrar el pedido, descontar stock, emitir
comprobante) lo decide tu sistema.

Para integrarte no hay que modificar PagoOK: se registra una **suscripción**
(tu URL HTTPS + un secreto + los tipos de evento que quieres recibir) y desde
ese momento PagoOK te hace `POST` firmado por cada evento.

---

## 1. El sobre (común a todos los eventos)

```json
{
  "evento_id": "8f3c2b1e-5d4a-4c1b-9e7f-0a1b2c3d4e5f",
  "tipo": "pago.confirmado",
  "version": "v1",
  "ocurrido_en": "2026-10-02T14:32:05-05:00",
  "empresa": { "id_fiscal": "20123456789", "pais": "PE", "id_fiscal_tipo": "RUC" },
  "moneda": "PEN",
  "datos": { "...": "detalle propio del tipo de evento" }
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `evento_id` | UUID | Único por evento. Úsalo para **idempotencia**. |
| `tipo` | string | `pago.confirmado` (por ahora). |
| `version` | string | `v1`. Un cambio incompatible sale como `v2`; `v1` no se rompe. |
| `ocurrido_en` | ISO 8601 con zona | Hora de Lima (`-05:00`). |
| `empresa.id_fiscal` | string | Identificador fiscal de la empresa (RUC, NIT, ...). |
| `empresa.pais` | string | ISO 3166 alfa-2 (`PE`; a futuro `BO`, `CO`). |
| `empresa.id_fiscal_tipo` | string | Qué es `id_fiscal`: `RUC` en Perú, `NIT` en Bolivia, etc. |
| `moneda` | string | ISO 4217: `PEN` (a futuro `BOB`, `COP`, `USD`). |
| `datos` | objeto | Ver cada evento. |

Tu sistema **debe ignorar campos que no conozca**: agregaremos campos nuevos sin
cambiar de versión.

## 2. Evento `pago.confirmado`

Se emite cuando PagoOK detecta un **ingreso** con certeza (Yape, Plin,
transferencia, etc.) en el celular del negocio.

```json
"datos": {
  "pago_id": 4821,
  "monto": "15.00",
  "canal": "yape",
  "institucion": "Yape",
  "contraparte": "Juan Perez",
  "codigo_operacion": "123456",
  "fecha_hora_banco": "2026-10-02T14:32:04-05:00",
  "referencia_pedido": null
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `pago_id` | entero | Id del pago en PagoOK (sirve para `POST /api/v1/pagos/{id}/reclamar`). |
| `monto` | string decimal | Siempre con 2 decimales (`"15.00"`). Es string para no perder precisión: conviértelo a decimal, no a float. |
| `canal` | string | `yape`, `plin`, `transferencia`, `tarjeta`, `cajero`, `otro`. |
| `institucion` | string | `Yape`, `Plin`, `BCP`, `Interbank`, `BBVA`, ... |
| `contraparte` | string | Nombre del titular que paga (puede venir abreviado o vacío). |
| `codigo_operacion` | string \| null | Si la notificación lo trae. |
| `fecha_hora_banco` | ISO 8601 | Nombre definitivo. Deuda conocida: hoy trae la hora en que el **servidor** recibió la notificación (no la del celular); el parser la llenará con la hora del banco en un sprint futuro. |
| `referencia_pedido` | string \| null | Reservado: id del pedido que tu sistema registró como pendiente. Hoy llega `null` (se llenará con el panel de conciliación); concilia por `monto` + hora + `contraparte`. |

## 3. Cabeceras y firma

```
POST <tu url_destino>
Content-Type: application/json; charset=utf-8
X-PagoOK-Firma: sha256=5f2b...e9
X-PagoOK-Evento-Id: 8f3c2b1e-5d4a-4c1b-9e7f-0a1b2c3d4e5f
X-PagoOK-Evento-Tipo: pago.confirmado
```

`X-PagoOK-Firma` = `"sha256=" + hex(HMAC_SHA256(secreto, cuerpo_crudo))`.

**Verifica sobre los bytes crudos del cuerpo**, tal como llegaron. Si primero
parseas el JSON y lo vuelves a serializar, los bytes cambian y la firma no
coincide. Compara en tiempo constante.

## 4. Lo que tu endpoint debe hacer

1. Verificar la firma. Si no coincide → `401` y no procesar.
2. Idempotencia: si `evento_id` ya lo procesaste → responder `200` igual,
   sin aplicarlo de nuevo.
3. Responder `2xx` rápido (menos de 10 s). El trabajo pesado, en segundo plano.

Cualquier respuesta que no sea `2xx` (o un timeout) cuenta como fallo.

## 5. Reintentos

| Intento | Cuándo |
|---|---|
| 1 | Inmediato (≈1 s después del pago) |
| 2 | 1 s después del fallo |
| 3 | 5 s después |
| 4 | 30 s después |
| 5 | 2 min después |
| 6 | 10 min después |

Tras 6 fallos la entrega queda `fallido` definitivo. Todo queda en el log
`entrega_evento` (base del futuro "reenviar eventos").

Por los reintentos, **puedes recibir el mismo evento más de una vez**: por eso
la idempotencia por `evento_id` es obligatoria.

## 6. Receptor de ejemplo

### Python (FastAPI)

```python
import hashlib, hmac, json, os
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()
SECRETO = os.environ["PAGOOK_WEBHOOK_SECRETO"]

@app.post("/webhooks/pagook")
async def recibir(request: Request):
    cuerpo = await request.body()                       # bytes crudos
    esperada = "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperada, request.headers.get("X-PagoOK-Firma", "")):
        raise HTTPException(401, "Firma inválida")

    sobre = json.loads(cuerpo)
    # Idempotencia: INSERT INTO eventos_recibidos(evento_id) ... ON CONFLICT DO NOTHING
    # si no insertó nada → ya se procesó: return {"status": "duplicado"}

    if sobre["tipo"] == "pago.confirmado":
        d = sobre["datos"]
        # buscar pedido pendiente por d["referencia_pedido"] o por monto + hora,
        # marcarlo pagado, descontar inventario, etc.
    return {"status": "ok"}
```

### PHP

```php
$cuerpo   = file_get_contents('php://input');
$esperada = 'sha256=' . hash_hmac('sha256', $cuerpo, getenv('PAGOOK_WEBHOOK_SECRETO'));
if (!hash_equals($esperada, $_SERVER['HTTP_X_PAGOOK_FIRMA'] ?? '')) {
    http_response_code(401); exit('Firma inválida');
}
$sobre = json_decode($cuerpo, true);
// idempotencia por $sobre['evento_id'] (índice UNIQUE en tu BD)
http_response_code(200); echo '{"status":"ok"}';
```

### Node (Express)

```js
app.post('/webhooks/pagook', express.raw({ type: 'application/json' }), (req, res) => {
  const esperada = 'sha256=' + crypto.createHmac('sha256', process.env.PAGOOK_WEBHOOK_SECRETO)
    .update(req.body).digest('hex');
  const recibida = req.get('X-PagoOK-Firma') || '';
  if (recibida.length !== esperada.length ||
      !crypto.timingSafeEqual(Buffer.from(esperada), Buffer.from(recibida))) {
    return res.status(401).send('Firma inválida');
  }
  const sobre = JSON.parse(req.body);
  // idempotencia por sobre.evento_id
  res.json({ status: 'ok' });
});
```

### Receptor de referencia dentro de PagoOK

`POST /api/v1/webhooks/receptor-ejemplo` (código en
`app/api/router_webhook_ejemplo.py`). Verifica la firma con la variable
`PAGOOK_WEBHOOK_SECRETO_EJEMPLO` y descarta duplicados. Sirve para probar una
suscripción de punta a punta apuntándola al mismo servidor.

---

## Operación (equipo PagoOK)

1. Aplicar `sql/eventos_webhook.sql` en PGAdmin (una sola vez, idempotente).
2. Crear la suscripción:
   ```
   python scripts/gestion_webhooks.py crear --empresa-id 1 --nombre "QueVendi" \
       --url https://quevendi.pro/webhooks/pagook
   ```
   Imprime el secreto (una sola vez) y el `INSERT` para PGAdmin. Entrega el
   secreto al receptor por un canal seguro.
3. Probar sin esperar un pago real:
   ```
   python scripts/gestion_webhooks.py probar --url <url> --secreto <secreto>
   ```
4. Revisar entregas:
   ```sql
   SELECT id, tipo, ultimo_estado, intentos, ultimo_codigo_http, ultimo_error, proximo_reintento_en
   FROM entrega_evento ORDER BY id DESC LIMIT 50;
   ```

Variables de entorno:

| Variable | Default | Uso |
|---|---|---|
| `EVENTOS_WORKER_ACTIVO` | `1` | `0` apaga el worker de envíos (las entregas se siguen encolando). |
| `PAGOOK_WEBHOOK_SECRETO_EJEMPLO` | — | Secreto del receptor de ejemplo. |

Agregar un evento nuevo (`pago.recibido`, `pago.no_conciliado`,
`egreso.registrado`, `dispositivo.alerta`): escribir su `armar_datos_*` en
`app/services/eventos.py` y llamar a `emitir_evento(tipo, empresa_id, datos)`.
El emisor, la firma, los reintentos y las tablas no cambian; el cliente solo
agrega el tipo a `tipos_suscritos`.
