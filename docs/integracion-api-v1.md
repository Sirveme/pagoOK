# PagoOK — API pública v1

PagoOK detecta en tiempo real los pagos Yape, Plin y transferencias bancarias
que llegan al celular de un negocio (vía las notificaciones del teléfono) y los
deja disponibles por API. Esta API v1 permite que **cualquier sistema externo**
(en cualquier lenguaje) consulte esos pagos y los **reclame** para conciliar una
venta. Es REST + JSON + API Key, al estilo de `api/v1` de facturalo.pro.

- **Base URL (producción):** `https://<tu-dominio-railway>/api/v1`
- **Formato:** JSON en request y response.
- **Errores:** siempre `{"detail": "..."}` con el código HTTP correspondiente.

---

## 1. Obtener una API Key

Las credenciales se emiten por cuenta/cliente. Hoy se generan con un script
interno (sin UI todavía):

```bash
python scripts/gestion_cuentas.py crear --empresa-id <ID> --nombre "Mi sistema"
```

Esto imprime la **api_key UNA sola vez** (formato `pk_live_...`). Guárdala
cifrada: en la base solo queda su hash SHA-256, no se puede recuperar. Si la
pierdes, se rota con `gestion_cuentas.py rotar --cuenta-id <ID>`.

Cada api_key está atada a **una empresa**: solo verás y podrás reclamar los
pagos de esa empresa. Un cliente jamás ve pagos de otro.

**Autenticación:** envía la key en el header `X-API-Key` en cada request.

```
X-API-Key: pk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 2. Endpoints

### GET /api/v1/pagos

Lista los pagos de tu cuenta que están **sin reclamar** o **reclamados por ti
mismo** (los reclamados por otra cuenta no aparecen).

**Query params** (todos opcionales):

| Param   | Tipo            | Descripción                                  |
|---------|-----------------|----------------------------------------------|
| `monto` | decimal exacto  | Filtra por monto exacto (ej. `15.00`)        |
| `desde` | ISO-8601        | Pagos con hora `>=` (ej. `2026-06-07T10:00:00`) |
| `hasta` | ISO-8601        | Pagos con hora `<=`                          |

**Respuesta 200:**

```json
{
  "pagos": [
    {
      "id": "1287",
      "nombre": "Juan Carlos Perez Lopez",
      "monto": 15.0,
      "hora": "2026-06-07T14:32:11.120480",
      "banco": "yape"
    }
  ]
}
```

- `id`: identificador del pago (string), úsalo para reclamar.
- `nombre`: nombre del remitente detectado (puede ser `null` en transferencias).
- `monto`: número. Para comparar, usa decimal exacto, nunca float redondeado.
- `hora`: ISO-8601 (hora en que el servidor registró el pago) o `null`.
- `banco`: `"yape"`, `"plin"`, el nombre del banco, o `null`.

**Errores:** `401` (falta o api_key inválida), `400` (param `monto`/`desde`/`hasta` mal formado).

---

### POST /api/v1/pagos/{id}/reclamar

Reclama un pago para tu cuenta. **Idempotente** para el mismo reclamante:
repetir el reclamo del mismo pago por la misma cuenta devuelve `200`.

**Respuesta 200:**

```json
{ "id": "1287", "reclamado": true, "reclamado_por": 3 }
```

| Código | Significado                                             |
|--------|--------------------------------------------------------|
| `200`  | Reclamado OK (o ya reclamado por ti — idempotente)     |
| `409`  | Ya reclamado por **otra** cuenta                        |
| `404`  | No existe o no pertenece a tu cuenta                    |
| `401`  | Falta o api_key inválida                                |

El reclamo es **atómico**: si dos consumidores reclaman el mismo pago a la vez,
exactamente uno recibe `200` y el otro `409`.

---

## 3. Flujo recomendado de conciliación

1. El cliente dice que pagó `S/ 15.00` por Yape hace un minuto.
2. **Consulta** por monto + ventana de tiempo:
   `GET /api/v1/pagos?monto=15.00&desde=2026-06-07T14:31:00`
3. **Presenta** los candidatos al cajero (nombre del remitente + hora).
4. Al confirmar, **reclama** ese pago: `POST /api/v1/pagos/{id}/reclamar`.
5. Si recibes `409`, ese pago ya lo tomó otra venta/sistema: vuelve al paso 2
   y elige otro candidato (o pide al cliente el código de operación).

Reclamar **antes** de cerrar la venta evita que dos cajas usen el mismo pago.

---

## 4. Ejemplos copy-paste

### Python (httpx)

```python
import httpx

BASE = "https://tu-dominio/api/v1"
KEY = "pk_live_xxxxx"
headers = {"X-API-Key": KEY}

# 1) Buscar pagos de S/ 15.00 desde cierta hora
r = httpx.get(f"{BASE}/pagos",
              params={"monto": "15.00", "desde": "2026-06-07T14:31:00"},
              headers=headers)
r.raise_for_status()
pagos = r.json()["pagos"]

# 2) Reclamar el primero
if pagos:
    pid = pagos[0]["id"]
    rr = httpx.post(f"{BASE}/pagos/{pid}/reclamar", headers=headers)
    if rr.status_code == 200:
        print("Reclamado:", rr.json())
    elif rr.status_code == 409:
        print("Ya lo reclamó otro, busca otro candidato")
```

### PHP (curl nativo)

```php
<?php
$base = "https://tu-dominio/api/v1";
$key  = "pk_live_xxxxx";

// 1) Buscar
$qs = http_build_query(["monto" => "15.00", "desde" => "2026-06-07T14:31:00"]);
$ch = curl_init("$base/pagos?$qs");
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => ["X-API-Key: $key"],
]);
$resp = json_decode(curl_exec($ch), true);
curl_close($ch);
$pagos = $resp["pagos"] ?? [];

// 2) Reclamar el primero
if (!empty($pagos)) {
    $pid = $pagos[0]["id"];
    $ch = curl_init("$base/pagos/$pid/reclamar");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ["X-API-Key: $key"],
    ]);
    curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    echo $code === 200 ? "Reclamado\n" : "No disponible ($code)\n";
}
```

### JavaScript (fetch)

```javascript
const BASE = "https://tu-dominio/api/v1";
const KEY = "pk_live_xxxxx";
const headers = { "X-API-Key": KEY };

// 1) Buscar
const qs = new URLSearchParams({ monto: "15.00", desde: "2026-06-07T14:31:00" });
const r = await fetch(`${BASE}/pagos?${qs}`, { headers });
const { pagos } = await r.json();

// 2) Reclamar el primero
if (pagos.length) {
  const pid = pagos[0].id;
  const rr = await fetch(`${BASE}/pagos/${pid}/reclamar`, { method: "POST", headers });
  if (rr.status === 200) console.log("Reclamado", await rr.json());
  else if (rr.status === 409) console.log("Ya lo reclamó otro");
}
```

---

## 5. Notas

- Compara montos como **decimal exacto** (`"15.00"`), no como float.
- Las fechas son ISO-8601; se acepta el sufijo `Z` (UTC).
- `GET /api/v1/pagos` devuelve hasta 200 pagos, más recientes primero.
- Esta API es **aditiva**: la ingesta de notificaciones desde el celular y sus
  endpoints internos no forman parte del contrato público y pueden cambiar.
