"""Heartbeat de dispositivos Android + dashboard admin de estado.

Endpoints:
  POST /api/v1/device/heartbeat          (auth X-Device-Token)  -> upsert estado
  GET  /api/v1/admin/dispositivos        (auth X-Admin-Token)   -> JSON estado + resumen
  GET  /admin/dispositivos/estado        (auth ?token=)         -> dashboard HTML

NOTA: el dashboard va en /admin/dispositivos/ESTADO (no /admin/dispositivos)
porque esa ruta ya la usa el panel de gestión de tokens (admin_dispositivos.py).

Español neutro peruano en textos visibles.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.models_device import DeviceEstadoActual
from app.api.models_webhook import Dispositivo

logger = logging.getLogger("pagook")
router = APIRouter(tags=["device-heartbeat"])

ESTADOS_VALIDOS = {"verde", "amarillo", "rojo"}
UMBRAL_SIN_HEARTBEAT_MIN = 5

# Campos obligatorios del body del heartbeat.
CAMPOS_REQUERIDOS = (
    "estado",
    "ultimo_ping_guardian_ok",
    "ultimo_pago_bancario",
    "pings_fallidos_consecutivos",
    "veces_zombie_total",
    "manufacturer",
    "modelo",
    "android_version",
    "app_version",
)


def _epoch_ms_a_dt(valor) -> Optional[datetime]:
    """Convierte epoch milliseconds (long) a datetime UTC tz-aware."""
    if valor is None:
        return None
    try:
        return datetime.fromtimestamp(int(valor) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================
# ENDPOINT 1: POST /api/v1/device/heartbeat
# =============================================================

@router.post("/api/v1/device/heartbeat")
async def heartbeat(
    request: Request,
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    # 1) Auth por token de dispositivo (mismo mecanismo que /api/v1/pagos/buscar).
    if not x_device_token:
        raise HTTPException(401, "Falta header X-Device-Token")
    dispositivo = (
        db.query(Dispositivo)
        .filter(Dispositivo.token == x_device_token, Dispositivo.activo == True)  # noqa: E712
        .first()
    )
    if not dispositivo:
        raise HTTPException(401, "Token inválido o desactivado")

    # 2) Validar body y campos requeridos.
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(data, dict):
        raise HTTPException(400, "El body debe ser un objeto JSON")

    faltantes = [c for c in CAMPOS_REQUERIDOS if data.get(c) is None]
    if faltantes:
        raise HTTPException(400, f"Faltan campos requeridos: {', '.join(faltantes)}")

    estado = str(data.get("estado")).lower()
    if estado not in ESTADOS_VALIDOS:
        raise HTTPException(400, "estado debe ser 'verde', 'amarillo' o 'rojo'")

    # 3) UPSERT portable (get-or-update): mismo efecto que INSERT ... ON CONFLICT.
    #    Se usa ORM en vez de ON CONFLICT crudo para que corra igual en los tests
    #    (SQLite) y en producción (Postgres); el heartbeat es serial por
    #    dispositivo, así que no hay carrera relevante.
    fila = (
        db.query(DeviceEstadoActual)
        .filter(DeviceEstadoActual.dispositivo_id == dispositivo.id)
        .first()
    )
    estado_anterior = fila.estado if fila else None

    def _campos_comunes(f: DeviceEstadoActual) -> None:
        f.estado = estado
        f.ultimo_ping_guardian_ok = _epoch_ms_a_dt(data.get("ultimo_ping_guardian_ok"))
        f.ultimo_pago_bancario = _epoch_ms_a_dt(data.get("ultimo_pago_bancario"))
        f.pings_fallidos_consecutivos = int(data.get("pings_fallidos_consecutivos") or 0)
        f.veces_zombie_total = int(data.get("veces_zombie_total") or 0)
        f.manufacturer = str(data.get("manufacturer"))[:50]
        f.modelo = str(data.get("modelo"))[:80]
        f.android_version = str(data.get("android_version"))[:20]
        f.app_version = str(data.get("app_version"))[:30]
        f.ultimo_heartbeat = _ahora_utc()

    if fila is None:
        fila = DeviceEstadoActual(dispositivo_id=dispositivo.id, veces_alarma_disparada=0)
        _campos_comunes(fila)
        db.add(fila)
    else:
        # Alarma: incrementar SOLO en la transición a 'rojo' desde un estado != 'rojo'.
        if estado == "rojo" and estado_anterior != "rojo":
            fila.veces_alarma_disparada = (fila.veces_alarma_disparada or 0) + 1
        _campos_comunes(fila)

    db.commit()

    # 4) Log según estado.
    if estado == "verde":
        logger.info(f"Heartbeat verde dispositivo={dispositivo.id} empresa={dispositivo.empresa_id}")
    else:
        logger.warning(
            f"Heartbeat {estado.upper()} dispositivo={dispositivo.id} "
            f"empresa={dispositivo.empresa_id} "
            f"pings_fallidos={data.get('pings_fallidos_consecutivos')}"
        )

    # 5) Response.
    return {"ok": True, "next_check_seconds": 60}


# =============================================================
# Datos compartidos para el dashboard (JSON y HTML)
# =============================================================

def _minutos_desde(uh: Optional[datetime]) -> Optional[float]:
    if uh is None:
        return None
    if uh.tzinfo is None:
        uh = uh.replace(tzinfo=timezone.utc)
    return (_ahora_utc() - uh).total_seconds() / 60.0


_ORDEN_ESTADO = {"rojo": 1, "amarillo": 2, "verde": 3}


def _obtener_dispositivos(db: Session) -> dict:
    """Arma la lista de dispositivos con su estado + el resumen agregado."""
    filas = (
        db.query(Dispositivo, Empresa, DeviceEstadoActual)
        .join(Empresa, Empresa.id == Dispositivo.empresa_id)
        .outerjoin(DeviceEstadoActual, DeviceEstadoActual.dispositivo_id == Dispositivo.id)
        .all()
    )

    dispositivos = []
    resumen = {"total": 0, "verde": 0, "amarillo": 0, "rojo": 0, "sin_heartbeat_reciente": 0}

    for disp, emp, dea in filas:
        estado = dea.estado if dea else None
        uh = dea.ultimo_heartbeat if dea else None
        minutos = _minutos_desde(uh)
        sin_reciente = minutos is None or minutos > UMBRAL_SIN_HEARTBEAT_MIN

        dispositivos.append({
            "dispositivo_id": disp.id,
            "empresa": emp.nombre_comercial or emp.razon_social,
            "estado": estado,
            "manufacturer": dea.manufacturer if dea else None,
            "modelo": dea.modelo if dea else None,
            "android_version": dea.android_version if dea else None,
            "app_version": dea.app_version if dea else None,
            "ultimo_heartbeat": uh.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if uh else None,
            "minutos_desde_ultimo_heartbeat": int(minutos) if minutos is not None else None,
            "veces_zombie_total": (dea.veces_zombie_total if dea else 0) or 0,
            "veces_alarma_disparada": (dea.veces_alarma_disparada if dea else 0) or 0,
            "pings_fallidos_consecutivos": (dea.pings_fallidos_consecutivos if dea else 0) or 0,
            "sin_heartbeat_reciente": sin_reciente,
        })

        resumen["total"] += 1
        if estado in ("verde", "amarillo", "rojo"):
            resumen[estado] += 1
        if sin_reciente:
            resumen["sin_heartbeat_reciente"] += 1

    dispositivos.sort(
        key=lambda d: (
            _ORDEN_ESTADO.get(d["estado"], 4),
            -(1e18 if d["ultimo_heartbeat"] is None else _clave_fecha(d["ultimo_heartbeat"])),
        )
    )
    return {"dispositivos": dispositivos, "resumen": resumen}


def _clave_fecha(iso: str) -> float:
    """Convierte 'YYYY-MM-DDTHH:MM:SSZ' a epoch para ordenar (desc)."""
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


# =============================================================
# ENDPOINT 2: GET /api/v1/admin/dispositivos (JSON)
# =============================================================

@router.get("/api/v1/admin/dispositivos")
def admin_dispositivos_json(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(401, "No autorizado")
    return _obtener_dispositivos(db)


# =============================================================
# ENDPOINT 3: GET /admin/dispositivos/estado (HTML)
# =============================================================

@router.get("/admin/dispositivos/estado", response_class=HTMLResponse)
def admin_dispositivos_html(
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    # MVP: token por query string. TODO(seguridad): migrar a cookie firmada.
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token or token != admin_token:
        return HTMLResponse(_html_login())
    datos = _obtener_dispositivos(db)
    return HTMLResponse(_html_dashboard(datos, token))


# =============================================================
# HTML (autocontenido, paleta "Amazonía Sereno")
# =============================================================

def _html_login() -> str:
    return """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estado de dispositivos · pagoOK</title>
<style>
  body { font-family:'Inter',system-ui,sans-serif; background:#faf6ef; color:#1c1f26; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
  .caja { background:#fff; border-radius:16px; padding:28px; box-shadow:0 2px 14px rgba(28,31,38,.1); max-width:360px; width:90%; }
  h1 { font-size:18px; color:#0f766e; margin:0 0 6px; }
  p { font-size:13px; color:#4b5563; margin:0 0 16px; }
  input { width:100%; border:1px solid #ddd; border-radius:8px; padding:10px 12px; font-size:14px; }
  button { width:100%; margin-top:12px; background:#0f766e; color:#fff; border:none; border-radius:8px; padding:11px; font-weight:700; cursor:pointer; }
</style></head>
<body><div class="caja">
  <h1>Estado de dispositivos</h1>
  <p>Ingresa el token de administrador para continuar.</p>
  <form method="get" action="/admin/dispositivos/estado">
    <input type="password" name="token" placeholder="Token de administrador" autofocus>
    <button type="submit">Entrar</button>
  </form>
</div></body></html>"""


def _html_dashboard(datos: dict, token: str) -> str:
    import json as _json
    plantilla = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estado de dispositivos · pagoOK</title>
<style>
  :root { --jade:#0f766e; --coral:#ea6d40; --fondo:#faf6ef; --grafito:#1c1f26; --suave:#4b5563; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Inter',system-ui,sans-serif; background:var(--fondo); color:var(--grafito); padding:16px; }
  .cont { max-width:1000px; margin:0 auto; }
  h1 { font-size:20px; color:var(--jade); margin-bottom:8px; }
  .resumen { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
  .chip { border-radius:999px; padding:6px 14px; font-size:13px; font-weight:600; }
  .c-verde { background:#dcfce7; color:#166534; }
  .c-amarillo { background:#fef3c7; color:#854d0e; }
  .c-rojo { background:#fee2e2; color:#991b1b; }
  .c-total { background:#e2e8f0; color:#334155; }
  .c-sin { background:#e5e7eb; color:#4b5563; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 1px 8px rgba(28,31,38,.06); }
  th, td { text-align:left; padding:9px 12px; font-size:13px; border-bottom:1px solid #f0ece3; }
  th { background:#f7f3ea; color:var(--suave); font-weight:600; }
  tr.rojo { background:#fee2e2; }
  tr.amarillo { background:#fef3c7; }
  tr.sin { background:#f3f4f6; color:#6b7280; }
  .badge-estado { font-weight:700; text-transform:capitalize; }
  .badge-sin { background:#9ca3af; color:#fff; border-radius:999px; padding:2px 8px; font-size:11px; }
  .actualizado { font-size:12px; color:var(--suave); margin-top:12px; text-align:center; }
</style></head>
<body><div class="cont">
  <h1>Estado de dispositivos</h1>
  <div class="resumen" id="resumen"></div>
  <table>
    <thead><tr>
      <th>Empresa</th><th>Equipo</th><th>Android</th><th>App</th>
      <th>Estado</th><th>Último heartbeat</th><th>Zombies</th><th>Alarmas</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <div class="actualizado" id="actualizado"></div>
</div>
<script>
const TOKEN = __TOKEN_JSON__;
let datos = __DATOS_JSON__;

function esc(s){ if(s==null) return "—"; return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function relativo(min) {
  if (min == null) return "sin datos";
  if (min < 1) return "hace segundos";
  if (min < 60) return "hace " + min + " min";
  const h = Math.floor(min/60);
  return "hace " + h + " h";
}
function pintar() {
  const r = datos.resumen;
  document.getElementById("resumen").innerHTML =
    '<span class="chip c-total">'+r.total+' equipos</span>' +
    '<span class="chip c-verde">'+r.verde+' verdes</span>' +
    '<span class="chip c-amarillo">'+r.amarillo+' amarillos</span>' +
    '<span class="chip c-rojo">'+r.rojo+' rojos</span>' +
    '<span class="chip c-sin">'+r.sin_heartbeat_reciente+' sin conexión</span>';
  document.getElementById("tbody").innerHTML = datos.dispositivos.map(d => {
    let clase = "";
    if (d.sin_heartbeat_reciente) clase = "sin";
    else if (d.estado === "rojo") clase = "rojo";
    else if (d.estado === "amarillo") clase = "amarillo";
    const equipo = esc(d.manufacturer) + " " + esc(d.modelo);
    const estadoTxt = d.sin_heartbeat_reciente
      ? '<span class="badge-sin">Sin conexión ' + relativo(d.minutos_desde_ultimo_heartbeat) + '</span>'
      : '<span class="badge-estado">' + esc(d.estado || "—") + '</span>';
    return "<tr class='"+clase+"'>" +
      "<td>"+esc(d.empresa)+"</td>" +
      "<td>"+equipo+"</td>" +
      "<td>"+esc(d.android_version)+"</td>" +
      "<td>"+esc(d.app_version)+"</td>" +
      "<td>"+estadoTxt+"</td>" +
      "<td>"+relativo(d.minutos_desde_ultimo_heartbeat)+"</td>" +
      "<td>"+d.veces_zombie_total+"</td>" +
      "<td>"+d.veces_alarma_disparada+"</td>" +
    "</tr>";
  }).join("");
  document.getElementById("actualizado").textContent = "Actualizado " + new Date().toLocaleTimeString("es-PE");
}
async function refrescar() {
  try {
    const r = await fetch("/api/v1/admin/dispositivos", { headers: { "X-Admin-Token": TOKEN } });
    if (r.ok) { datos = await r.json(); pintar(); }
  } catch (e) {}
}
pintar();
setInterval(refrescar, 30000);
</script>
</body></html>"""
    return (plantilla
            .replace("__TOKEN_JSON__", _json.dumps(token))
            .replace("__DATOS_JSON__", _json.dumps(datos)))
