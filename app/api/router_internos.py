"""Router del módulo EGRESOS + CUENTAS PROPIAS.

Incluye:
  - Vistas cronológicas: GET /{slug}/ingresos, GET /{slug}/egresos (HTML)
  - JSON paginado: GET /api/v1/{slug}/ingresos, GET /api/v1/{slug}/egresos
  - Confirmación de posible interno: POST /api/v1/{slug}/pagos/{id}/confirmacion
  - "Mis cuentas propias": GET /{slug}/config/cuentas + CRUD
  - Admin: POST /api/v1/admin/recalcular-interno/{empresa_id}

Español neutro peruano en todos los textos de cara al usuario.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Body, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.models_internos import PersonaCuenta, TitularConfirmadoExterno
from app.api.models_webhook import Dispositivo, PagoDetectado
from app.api.deps_empresa import acceso_html_o_redirigir, acceso_por_sesion_o_legacy
from app.api.router_demo import _obtener_empresa_por_slug, _serializar_pago
from app.services.deteccion_interno import es_posible_interno, normalizar

router = APIRouter(tags=["egresos-internos"])

TAM_PAGINA = 50
TZ_LIMA = ZoneInfo("America/Lima")
LOTE_RECALCULO = 500


# =============================================================
# AUTH (mínima, reutiliza mecanismos existentes)
# =============================================================

def _vista_html(slug: str, request: Request, db: Session, permiso: str, tipo: str):
    empresa = acceso_html_o_redirigir(slug, request, db, permiso)
    if not isinstance(empresa, Empresa):
        return empresa  # redirección al login
    nombre = empresa.nombre_comercial or empresa.razon_social or slug
    return HTMLResponse(_html_lista_movimientos(slug, nombre, tipo))


def _verificar_acceso_dueno(slug: str, request: Request, db: Session) -> Empresa:
    """Resuelve la empresa por slug para las secciones del dueño.

    Credenciales aceptadas (el slug solo se VALIDA contra ellas):
      - X-Device-Token o X-API-Key de esa misma empresa (como antes), o
      - sesión del panel de esa empresa con rol de dueño, o
      - sin credencial: solo perusistemas mientras LEGACY_PUBLICO_PERUSISTEMAS=true.
    """
    token = request.headers.get("x-device-token")
    api_key = request.headers.get("x-api-key")
    if not token and not api_key:
        return acceso_por_sesion_o_legacy(slug, request, db, "administrar")

    empresa = _obtener_empresa_por_slug(db, slug)
    if empresa is None:
        raise HTTPException(404, "Empresa no encontrada")

    token = request.headers.get("x-device-token")
    api_key = request.headers.get("x-api-key")

    if token:
        disp = (
            db.query(Dispositivo)
            .filter(Dispositivo.token == token, Dispositivo.empresa_id == empresa.id)
            .first()
        )
        if not disp:
            raise HTTPException(403, "Token no autorizado para esta empresa")
    elif api_key:
        # Validación por API Key de la empresa (import local para evitar ciclos).
        from app.api.models_cuenta import CuentaApi, hash_api_key
        cuenta = (
            db.query(CuentaApi)
            .filter(
                CuentaApi.api_key_hash == hash_api_key(api_key),
                CuentaApi.empresa_id == empresa.id,
                CuentaApi.activa == True,  # noqa: E712
            )
            .first()
        )
        if not cuenta:
            raise HTTPException(403, "API key no autorizada para esta empresa")

    return empresa


# =============================================================
# JSON PAGINADO: ingresos / egresos
# =============================================================

def _parse_iso_opcional(valor: Optional[str], campo: str) -> Optional[datetime]:
    """Fecha del filtro -> UTC naive (así se guarda recibido_en).

    Acepta 'YYYY-MM-DD' o ISO completo (con 'Z' o con offset, ej. -05:00).
    Sin zona horaria se interpreta como hora de Lima. Antes se descartaba la
    zona sin convertir: "hoy desde las 00:00" de Lima se comparaba como 00:00
    UTC y se perdían 5 horas en cada borde del rango.
    """
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.strip().replace(" ", "+").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(400, f"Parámetro '{campo}' no es una fecha válida")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_LIMA)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


ETIQUETA_TIPO_EGRESO = {
    "envio_billetera": "Envío por billetera",
    "transferencia_enviada": "Transferencia enviada",
    "consumo_tarjeta": "Consumo con tarjeta",
    "retiro_cajero": "Retiro en cajero",
    "pago_servicio": "Pago de servicio",
    "cargo_recurrente": "Cargo recurrente",
    "otro_egreso": "Otro egreso",
}


def _query_movimientos(
    db: Session, empresa_id: int, tipo: str,
    desde: Optional[str], hasta: Optional[str],
    metodo: Optional[str], solo_negocio: bool, tipo_egreso: Optional[str] = None,
):
    q = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == empresa_id,
        PagoDetectado.tipo == tipo,
    )
    d = _parse_iso_opcional(desde, "desde")
    h = _parse_iso_opcional(hasta, "hasta")
    if d is not None:
        q = q.filter(PagoDetectado.recibido_en >= d)
    if h is not None:
        q = q.filter(PagoDetectado.recibido_en <= h)
    if metodo:
        q = q.filter(PagoDetectado.metodo == metodo)
    if solo_negocio:
        # Excluye los internos confirmados (transferencias entre cuentas propias).
        q = q.filter(
            ~(
                (PagoDetectado.posible_interno == True)  # noqa: E712
                & (PagoDetectado.confirmacion_usuario == "confirmado_interno")
            )
        )
    if tipo == "egreso" and tipo_egreso:
        q = q.filter(PagoDetectado.tipo_egreso == tipo_egreso)
    return q


def _totales_por_moneda(q) -> dict:
    """Suma del rango COMPLETO filtrado (no solo la página), por moneda, sin convertir."""
    from sqlalchemy import func as _f
    filas = (
        q.with_entities(PagoDetectado.moneda, _f.sum(PagoDetectado.monto), _f.count(PagoDetectado.id))
        .group_by(PagoDetectado.moneda)
        .all()
    )
    return {(m or "PEN"): {"total": f"{Decimal(str(t or 0)):.2f}", "cantidad": n} for m, t, n in filas}


def _listar_movimientos(
    db: Session, empresa_id: int, tipo: str,
    desde: Optional[str], hasta: Optional[str],
    metodo: Optional[str], solo_negocio: bool, pagina: int,
    tipo_egreso: Optional[str] = None,
) -> dict:
    q = _query_movimientos(db, empresa_id, tipo, desde, hasta, metodo, solo_negocio, tipo_egreso)
    total = q.count()
    pagina = max(1, pagina)
    offset = (pagina - 1) * TAM_PAGINA
    filas = (
        q.order_by(PagoDetectado.recibido_en.desc())
        .offset(offset)
        .limit(TAM_PAGINA)
        .all()
    )
    return {
        "ok": True,
        "tipo": tipo,
        "pagina": pagina,
        "tam_pagina": TAM_PAGINA,
        "total": total,
        "hay_siguiente": offset + len(filas) < total,
        "movimientos": [_serializar_pago(p) for p in filas],
        "totales_por_moneda": _totales_por_moneda(q.order_by(None)),
    }


@router.get("/api/v1/{slug}/ingresos")
def api_ingresos(
    slug: str,
    request: Request,
    desde: Optional[str] = None, hasta: Optional[str] = None,
    metodo: Optional[str] = None, solo_negocio: bool = False, pagina: int = 1,
    db: Session = Depends(get_db),
):
    empresa = acceso_por_sesion_o_legacy(slug, request, db, "ver_pagos")
    return _listar_movimientos(db, empresa.id, "ingreso", desde, hasta, metodo, solo_negocio, pagina)


@router.get("/api/v1/{slug}/egresos")
def api_egresos(
    slug: str,
    request: Request,
    desde: Optional[str] = None, hasta: Optional[str] = None,
    metodo: Optional[str] = None, solo_negocio: bool = False, pagina: int = 1,
    tipo_egreso: Optional[str] = None,
    db: Session = Depends(get_db),
):
    empresa = acceso_por_sesion_o_legacy(slug, request, db, "reportes")
    return _listar_movimientos(db, empresa.id, "egreso", desde, hasta, metodo, solo_negocio, pagina,
                               tipo_egreso=tipo_egreso)


@router.get("/api/v1/{slug}/egresos.csv")
def api_egresos_csv(
    slug: str,
    request: Request,
    desde: Optional[str] = None, hasta: Optional[str] = None,
    metodo: Optional[str] = None, solo_negocio: bool = False,
    tipo_egreso: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """CSV completo de egresos del rango. Solo dueño y contador CON SESIÓN
    (no aplica el modo legacy público)."""
    import csv
    import io
    from fastapi.responses import Response
    from app.api.deps_empresa import sesion_opcional
    from app.services.eventos import a_iso_lima

    ctx = sesion_opcional(request, db)
    if ctx is None:
        raise HTTPException(401, "sesion_requerida")
    empresa = acceso_por_sesion_o_legacy(slug, request, db, "reportes")
    q = _query_movimientos(db, empresa.id, "egreso", desde, hasta, metodo, solo_negocio, tipo_egreso)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha_hora", "tipo_egreso", "metodo", "banco", "contraparte", "monto", "moneda",
                "tarjeta_ultimos4", "cuenta_propia"])
    for p in q.order_by(PagoDetectado.recibido_en).all():
        w.writerow([a_iso_lima(p.recibido_en), p.tipo_egreso or "", p.metodo, p.banco or "", p.titular or "",
                    f"{Decimal(str(p.monto)):.2f}", p.moneda or "PEN", p.tarjeta_ultimos4 or "",
                    "si" if p.confirmacion_usuario == "confirmado_interno" else "no"])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="egresos_{empresa.slug}.csv"'})


# =============================================================
# CONFIRMACIÓN DE POSIBLE INTERNO (desde la vista pública)
# =============================================================

@router.post("/api/v1/{slug}/pagos/{pago_id}/confirmacion")
def confirmar_interno(
    slug: str, pago_id: int, request: Request, payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """El usuario confirma si un posible interno es cuenta propia o no.

    Body: {"es_propia": true|false}

    - es_propia=true  -> confirmacion_usuario='confirmado_interno'.
    - es_propia=false -> 'confirmado_externo' + registra el nombre en
      titular_confirmado_externo para no volver a sugerirlo.

    NOTA: NO se sobrescribe `tipo` (se preserva ingreso/egreso). La exclusión del
    negocio se hace por confirmacion_usuario en los cálculos de resumen.

    Acceso: sesión de la empresa con permiso de reportes (dueño o contador),
    o perusistemas en modo legacy público.
    """
    empresa = acceso_por_sesion_o_legacy(slug, request, db, "reportes")

    pago = (
        db.query(PagoDetectado)
        .filter(PagoDetectado.id == pago_id, PagoDetectado.empresa_id == empresa.id)
        .first()
    )
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")

    es_propia = bool(payload.get("es_propia"))
    if es_propia:
        pago.confirmacion_usuario = "confirmado_interno"
        pago.posible_interno = True
    else:
        pago.confirmacion_usuario = "confirmado_externo"
        norm = normalizar(pago.titular or "")
        if norm:
            existente = (
                db.query(TitularConfirmadoExterno)
                .filter(
                    TitularConfirmadoExterno.empresa_id == empresa.id,
                    TitularConfirmadoExterno.nombre_normalizado == norm,
                )
                .first()
            )
            if existente:
                existente.veces_confirmado = (existente.veces_confirmado or 0) + 1
                existente.ultima_confirmacion = datetime.utcnow()
            else:
                db.add(TitularConfirmadoExterno(
                    empresa_id=empresa.id, nombre_normalizado=norm,
                ))
    db.commit()
    return {"ok": True, "id": pago.id, "confirmacion_usuario": pago.confirmacion_usuario}


# =============================================================
# CONFIG: "Mis cuentas propias" (CRUD)
# =============================================================

BILLETERAS_VALIDAS = [
    "yape", "plin", "bcp", "bbva", "interbank", "scotiabank",
    "pichincha", "banbif", "tunki", "banco_nacion", "otro",
]


def _cuenta_dict(c: PersonaCuenta) -> dict:
    return {
        "id": c.id,
        "nombre_en_push": c.nombre_en_push,
        "billetera": c.billetera,
        "banco": c.banco or "",
        "telefono": c.telefono or "",
        "activa": bool(c.activa),
    }


@router.get("/{slug}/config/cuentas", response_class=HTMLResponse)
def vista_config_cuentas(slug: str, request: Request, db: Session = Depends(get_db)):
    empresa = _verificar_acceso_dueno(slug, request, db)
    cuentas = (
        db.query(PersonaCuenta)
        .filter(PersonaCuenta.empresa_id == empresa.id, PersonaCuenta.activa == True)  # noqa: E712
        .order_by(PersonaCuenta.creada_en.desc())
        .all()
    )
    nombre = empresa.nombre_comercial or empresa.razon_social or slug
    return HTMLResponse(_html_config_cuentas(slug, nombre, [_cuenta_dict(c) for c in cuentas]))


@router.post("/{slug}/config/cuentas")
def crear_cuenta(
    slug: str, request: Request,
    nombre_en_push: str = Form(...),
    billetera: str = Form(...),
    banco: str = Form(""),
    telefono: str = Form(""),
    db: Session = Depends(get_db),
):
    empresa = _verificar_acceso_dueno(slug, request, db)
    if not empresa.duenio_id:
        raise HTTPException(400, "La empresa no tiene un dueño asignado")

    nombre = (nombre_en_push or "").strip()[:200]
    if not nombre:
        raise HTTPException(400, "El nombre no puede estar vacío")
    bille = (billetera or "").strip().lower()
    if bille not in BILLETERAS_VALIDAS:
        bille = "otro"

    db.add(PersonaCuenta(
        persona_id=empresa.duenio_id,
        empresa_id=empresa.id,
        nombre_en_push=nombre,
        nombre_normalizado=normalizar(nombre),
        billetera=bille,
        banco=(banco or "").strip()[:50] or None,
        telefono=(telefono or "").strip()[:20] or None,
    ))
    db.commit()
    return RedirectResponse(url=f"/{slug}/config/cuentas", status_code=303)


@router.put("/{slug}/config/cuentas/{cuenta_id}")
def editar_cuenta(
    slug: str, cuenta_id: int, request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    empresa = _verificar_acceso_dueno(slug, request, db)
    cuenta = (
        db.query(PersonaCuenta)
        .filter(PersonaCuenta.id == cuenta_id, PersonaCuenta.empresa_id == empresa.id)
        .first()
    )
    if cuenta is None:
        raise HTTPException(404, "Cuenta no encontrada")

    if "nombre_en_push" in payload:
        nombre = (payload.get("nombre_en_push") or "").strip()[:200]
        if not nombre:
            raise HTTPException(400, "El nombre no puede estar vacío")
        cuenta.nombre_en_push = nombre
        cuenta.nombre_normalizado = normalizar(nombre)
    if "billetera" in payload:
        b = (payload.get("billetera") or "").strip().lower()
        cuenta.billetera = b if b in BILLETERAS_VALIDAS else "otro"
    if "banco" in payload:
        cuenta.banco = (payload.get("banco") or "").strip()[:50] or None
    if "telefono" in payload:
        cuenta.telefono = (payload.get("telefono") or "").strip()[:20] or None
    cuenta.actualizada_en = datetime.utcnow()
    db.commit()
    return {"ok": True, "cuenta": _cuenta_dict(cuenta)}


@router.delete("/{slug}/config/cuentas/{cuenta_id}")
def eliminar_cuenta(slug: str, cuenta_id: int, request: Request, db: Session = Depends(get_db)):
    """Desactiva (no borra) la cuenta propia."""
    empresa = _verificar_acceso_dueno(slug, request, db)
    cuenta = (
        db.query(PersonaCuenta)
        .filter(PersonaCuenta.id == cuenta_id, PersonaCuenta.empresa_id == empresa.id)
        .first()
    )
    if cuenta is None:
        raise HTTPException(404, "Cuenta no encontrada")
    cuenta.activa = False
    cuenta.actualizada_en = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": cuenta_id, "activa": False}


# =============================================================
# ADMIN: recalcular posible_interno sobre pagos existentes
# =============================================================

@router.post("/api/v1/admin/recalcular-interno/{empresa_id}")
def recalcular_interno(
    empresa_id: int,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    """Re-evalúa posible_interno sobre los pagos existentes de una empresa.

    Protegido por X-Admin-Token (variable de entorno ADMIN_TOKEN). Corre por
    lotes de 500. No toca confirmacion_usuario ni el tipo.
    """
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(403, "No autorizado")

    actualizados = 0
    ultimo_id = 0
    while True:
        lote = (
            db.query(PagoDetectado)
            .filter(PagoDetectado.empresa_id == empresa_id, PagoDetectado.id > ultimo_id)
            .order_by(PagoDetectado.id.asc())
            .limit(LOTE_RECALCULO)
            .all()
        )
        if not lote:
            break
        for pago in lote:
            ultimo_id = pago.id
            nuevo = es_posible_interno(empresa_id, pago.titular or "", db)
            if bool(pago.posible_interno) != bool(nuevo):
                pago.posible_interno = nuevo
                actualizados += 1
        db.commit()

    return {"ok": True, "empresa_id": empresa_id, "actualizados": actualizados}


# =============================================================
# HTML (autocontenido) — vistas cronológicas y config
# =============================================================

def _html_lista_movimientos(slug: str, nombre_empresa: str, tipo: str) -> str:
    es_egreso = tipo == "egreso"
    titulo = "Egresos" if es_egreso else "Ingresos"
    color = "#ea6d40" if es_egreso else "#0f766e"
    plantilla = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__ · __NOMBRE__ · pagoOK</title>
<style>
  :root { --acento: __COLOR__; --jade:#0f766e; --coral:#ea6d40; --fondo:#faf6ef; --grafito:#1c1f26; --suave:#4b5563; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Inter',system-ui,sans-serif; background:var(--fondo); color:var(--grafito); padding:16px; }
  .cont { max-width:900px; margin:0 auto; }
  h1 { font-size:20px; color:var(--acento); margin-bottom:4px; }
  .sub { font-size:13px; color:var(--suave); margin-bottom:16px; }
  a.volver { color:var(--jade); font-size:13px; text-decoration:none; }
  .filtros { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:14px 0; }
  .filtros button, .filtros select { border:1px solid #ddd; background:#fff; border-radius:8px; padding:7px 11px; font-size:13px; cursor:pointer; font-family:inherit; }
  .filtros button.activo { background:var(--acento); color:#fff; border-color:var(--acento); }
  .toggle { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--suave); }
  .rango-pers { display:flex; gap:6px; align-items:center; font-size:13px; color:var(--suave); }
  .rango-pers input { border:1px solid #ddd; border-radius:8px; padding:6px 8px; font-size:13px; font-family:inherit; }
  .btn-csv { margin-left:auto; background:var(--jade); color:#fff; border:none; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 1px 8px rgba(28,31,38,.06); }
  th, td { text-align:left; padding:10px 12px; font-size:13px; border-bottom:1px solid #f0ece3; }
  th { background:#f7f3ea; color:var(--suave); font-weight:600; }
  td.monto { font-variant-numeric:tabular-nums; font-weight:700; color:var(--acento); }
  .badge { background:#fef3c7; border:1px solid #fbbf24; border-radius:999px; padding:2px 8px; font-size:11px; }
  .pag { display:flex; gap:10px; justify-content:center; align-items:center; margin:16px 0; }
  .pag button { border:1px solid #ddd; background:#fff; border-radius:8px; padding:8px 14px; cursor:pointer; }
  .pag button:disabled { opacity:.4; cursor:not-allowed; }
  .vacio { text-align:center; padding:30px; color:var(--suave); }
</style></head>
<body><div class="cont">
  <a class="volver" href="/__SLUG__">← Volver al inicio</a>
  <h1>__TITULO__ de __NOMBRE__</h1>
  <div class="sub" id="sub">Cargando...</div>

  <div class="filtros">
    <button data-rango="hoy" class="activo">Hoy</button>
    <button data-rango="7">7 días</button>
    <button data-rango="30">30 días</button>
    <button data-rango="mes">Este mes</button>
    <button data-rango="todo">Todo</button>
    <span class="rango-pers">
      <label>Desde <input type="date" id="f-desde" autocomplete="off"></label>
      <label>Hasta <input type="date" id="f-hasta" autocomplete="off"></label>
    </span>
    <select id="metodo" autocomplete="off">
      <option value="" selected>Todos</option>
      <option value="yape" class="solo-ingreso">Yape</option>
      <option value="plin">Plin</option>
      <option value="transferencia">Transferencia</option>
      <option value="tarjeta">Tarjeta</option>
    </select>
    <select id="tipo-egreso" style="display:none" autocomplete="off">
      <option value="">Todos los tipos</option>
      <option value="envio_billetera">Envío por billetera</option>
      <option value="transferencia_enviada">Transferencia enviada</option>
      <option value="consumo_tarjeta">Consumo con tarjeta</option>
      <option value="retiro_cajero">Retiro en cajero</option>
      <option value="pago_servicio">Pago de servicio</option>
      <option value="cargo_recurrente">Cargo recurrente</option>
      <option value="otro_egreso">Otro egreso</option>
    </select>
    <label class="toggle" title="Oculta las transferencias que confirmaste como movimientos entre tus propias cuentas">
      <input type="checkbox" id="solo-negocio" autocomplete="off"> Solo del negocio</label>
    <button class="btn-csv" id="csv">Descargar CSV</button>
  </div>
  <div class="sub" id="totales"></div>

  <table>
    <thead><tr><th>Fecha</th><th>Hora</th><th>Método</th><th>__COL_NOMBRE__</th><th>Monto</th><th>Estado</th></tr></thead>
    <tbody id="tbody"><tr><td colspan="6" class="vacio">Cargando...</td></tr></tbody>
  </table>

  <div class="pag">
    <button id="ant" disabled>← Anterior</button>
    <span id="pag-info">Página 1</span>
    <button id="sig" disabled>Siguiente →</button>
  </div>
</div>
<script>
const SLUG = "__SLUG__";
const TIPO = "__TIPO__";
const ES_EGRESO = __ES_EGRESO__;
let pagina = 1, rango = "hoy", ultimaData = null;

// ISO con la zona del navegador (ej. 2026-10-23T00:00:00-05:00): el servidor
// la convierte a UTC. Sin zona, el servidor asume hora de Lima.
function isoConZona(d) {
  const p = n => String(n).padStart(2, "0");
  const off = -d.getTimezoneOffset(), s = off >= 0 ? "+" : "-", a = Math.abs(off);
  return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+"T"+p(d.getHours())+":"+p(d.getMinutes())+":"+p(d.getSeconds())
         + s + p(Math.floor(a/60)) + ":" + p(a%60);
}
function rangoFechas(clave) {
  if (clave === "todo") return { desde: "", hasta: "" };
  if (clave === "personalizado") {
    const d = document.getElementById("f-desde").value, h = document.getElementById("f-hasta").value;
    const [dy, dm, dd] = (d || "").split("-").map(Number), [hy, hm, hd] = (h || "").split("-").map(Number);
    return { desde: d ? isoConZona(new Date(dy, dm-1, dd, 0, 0, 0)) : "",
             hasta: h ? isoConZona(new Date(hy, hm-1, hd, 23, 59, 59)) : "" };
  }
  const ahora = new Date();
  const fin = new Date(ahora.getFullYear(), ahora.getMonth(), ahora.getDate(), 23, 59, 59);
  let ini;
  if (clave === "hoy") ini = new Date(ahora.getFullYear(), ahora.getMonth(), ahora.getDate());
  else if (clave === "7") ini = new Date(ahora.getTime() - 7*864e5);
  else if (clave === "30") ini = new Date(ahora.getTime() - 30*864e5);
  else if (clave === "mes") ini = new Date(ahora.getFullYear(), ahora.getMonth(), 1);
  else ini = new Date(ahora.getFullYear(), ahora.getMonth(), ahora.getDate());
  return { desde: isoConZona(ini), hasta: isoConZona(fin) };
}
function esc(s){ if(s==null) return ""; return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function filtrosQS() {
  const { desde, hasta } = rangoFechas(rango);
  const tipoEgreso = ES_EGRESO ? document.getElementById("tipo-egreso").value : "";
  return (desde ? "desde="+encodeURIComponent(desde) : "desde=") + (hasta ? "&hasta="+encodeURIComponent(hasta) : "&hasta=") +
         "&metodo="+document.getElementById("metodo").value+
         "&solo_negocio="+document.getElementById("solo-negocio").checked+
         (tipoEgreso ? "&tipo_egreso="+encodeURIComponent(tipoEgreso) : "");
}
if (ES_EGRESO) document.getElementById("tipo-egreso").style.display = "";
// Yape no notifica a quien paga: en egresos ese filtro siempre daría vacío.
if (ES_EGRESO) document.querySelectorAll("#metodo .solo-ingreso").forEach(o => o.remove());
// Al cargar, filtros en su valor por defecto (el navegador puede reponer uno viejo).
document.getElementById("metodo").value = "";
document.getElementById("tipo-egreso").value = "";
document.getElementById("solo-negocio").checked = false;
document.getElementById("f-desde").value = ""; document.getElementById("f-hasta").value = "";

async function cargar() {
  const { desde, hasta } = rangoFechas(rango);
  const metodo = document.getElementById("metodo").value;
  const soloNeg = document.getElementById("solo-negocio").checked;
  const tipoEgreso = ES_EGRESO ? document.getElementById("tipo-egreso").value : "";
  // El endpoint es plural (/ingresos, /egresos); TIPO viene en singular.
  const url = "/api/v1/" + SLUG + "/" + TIPO + "s?" + filtrosQS() + "&pagina="+pagina;
  const r = await fetch(url); const data = await r.json();
  if (!data.ok) { document.getElementById("tbody").innerHTML = '<tr><td colspan=6 class=vacio>No se pudo cargar.</td></tr>'; return; }
  ultimaData = data;
  const tb = document.getElementById("tbody");
  if (data.movimientos.length === 0) { tb.innerHTML = '<tr><td colspan=6 class=vacio>No hay '+(ES_EGRESO?"egresos":"ingresos")+' en este rango.</td></tr>'; }
  else {
    tb.innerHTML = data.movimientos.map(p => {
      const simbolo = p.moneda === "USD" ? "$" : "S/";
      const flecha = ES_EGRESO ? "→ " : "";
      const estado = p.necesita_revision ? '<span class="badge">🏠 ¿Tuyo?</span>' : (p.confirmacion_usuario==="confirmado_interno" ? "Cuenta propia" : "");
      const medio = ES_EGRESO && p.tipo_egreso_label ? p.tipo_egreso_label + " · " + p.metodo_label : p.metodo_label;
      const tarjeta = p.tarjeta_ultimos4 ? " (•••• " + esc(p.tarjeta_ultimos4) + ")" : "";
      return "<tr><td>"+esc(p.fecha)+"</td><td>"+esc(p.hora)+"</td><td>"+esc(medio)+tarjeta+"</td><td>"+flecha+esc(p.titular_corto)+"</td><td class=monto>"+simbolo+" "+esc(p.monto)+"</td><td>"+estado+"</td></tr>";
    }).join("");
  }
  document.getElementById("sub").textContent = "Mostrando " + data.movimientos.length + " de " + data.total + " movimientos";
  const tot = data.totales_por_moneda || {};
  document.getElementById("totales").textContent = Object.keys(tot).length
    ? "Total del rango: " + Object.keys(tot).map(m => (m === "USD" ? "$ " : "S/ ") + tot[m].total + " (" + tot[m].cantidad + ")").join("  ·  ")
    : "";
  document.getElementById("pag-info").textContent = "Página " + data.pagina;
  document.getElementById("ant").disabled = data.pagina <= 1;
  document.getElementById("sig").disabled = !data.hay_siguiente;
}

document.querySelectorAll(".filtros [data-rango]").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".filtros [data-rango]").forEach(x => x.classList.remove("activo"));
  b.classList.add("activo"); rango = b.dataset.rango; pagina = 1;
  document.getElementById("f-desde").value = ""; document.getElementById("f-hasta").value = "";
  cargar();
}));
["f-desde", "f-hasta"].forEach(id => document.getElementById(id).addEventListener("change", () => {
  document.querySelectorAll(".filtros [data-rango]").forEach(x => x.classList.remove("activo"));
  rango = "personalizado"; pagina = 1; cargar();
}));
document.getElementById("metodo").addEventListener("change", () => { pagina = 1; cargar(); });
document.getElementById("solo-negocio").addEventListener("change", () => { pagina = 1; cargar(); });
document.getElementById("ant").addEventListener("click", () => { if (pagina>1){ pagina--; cargar(); } });
document.getElementById("sig").addEventListener("click", () => { if (ultimaData && ultimaData.hay_siguiente){ pagina++; cargar(); } });
document.getElementById("tipo-egreso").addEventListener("change", () => { pagina = 1; cargar(); });
document.getElementById("csv").addEventListener("click", () => {
  if (ES_EGRESO) {  // CSV completo del rango desde el servidor (dueño y contador con sesión)
    location.href = "/api/v1/" + SLUG + "/egresos.csv?" + filtrosQS();
    return;
  }
  if (!ultimaData || !ultimaData.movimientos.length) return;
  const filas = [["Fecha","Hora","Metodo","Nombre","Monto","Moneda"]];
  ultimaData.movimientos.forEach(p => filas.push([p.fecha,p.hora,p.metodo_label,(p.titular||""),p.monto,p.moneda]));
  const csv = filas.map(f => f.map(c => '"'+String(c).replace(/"/g,'""')+'"').join(",")).join("\\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type:"text/csv"}));
  a.download = TIPO + "_" + SLUG + ".csv"; a.click();
});
cargar();
</script>
</body></html>"""
    return (plantilla
            .replace("__TITULO__", titulo)
            .replace("__COLOR__", color)
            .replace("__COL_NOMBRE__", "Destinatario" if es_egreso else "Titular")
            .replace("__ES_EGRESO__", "true" if es_egreso else "false")
            .replace("__TIPO__", tipo)
            .replace("__SLUG__", slug)
            .replace("__NOMBRE__", nombre_empresa))


@router.get("/{slug}/ingresos", response_class=HTMLResponse)
def vista_ingresos(slug: str, request: Request, db: Session = Depends(get_db)):
    return _vista_html(slug, request, db, "ver_pagos", "ingreso")


@router.get("/{slug}/egresos", response_class=HTMLResponse)
def vista_egresos(slug: str, request: Request, db: Session = Depends(get_db)):
    return _vista_html(slug, request, db, "reportes", "egreso")


def _html_config_cuentas(slug: str, nombre_empresa: str, cuentas: list[dict]) -> str:
    import json as _json
    plantilla = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mis cuentas propias · __NOMBRE__ · pagoOK</title>
<style>
  :root { --jade:#0f766e; --coral:#ea6d40; --fondo:#faf6ef; --grafito:#1c1f26; --suave:#4b5563; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Inter',system-ui,sans-serif; background:var(--fondo); color:var(--grafito); padding:16px; }
  .cont { max-width:760px; margin:0 auto; }
  a.volver { color:var(--jade); font-size:13px; text-decoration:none; }
  h1 { font-size:20px; color:var(--jade); margin:8px 0 4px; }
  .expl { font-size:13px; color:var(--suave); margin-bottom:18px; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 1px 8px rgba(28,31,38,.06); }
  th, td { text-align:left; padding:10px 12px; font-size:13px; border-bottom:1px solid #f0ece3; }
  th { background:#f7f3ea; color:var(--suave); font-weight:600; }
  .btn { border:none; border-radius:8px; padding:8px 12px; font-size:13px; font-weight:600; cursor:pointer; font-family:inherit; }
  .btn-jade { background:var(--jade); color:#fff; }
  .btn-mini { background:#fff; border:1px solid #ddd; padding:4px 9px; font-size:12px; }
  .btn-del { color:var(--coral); border-color:var(--coral); }
  .agregar { margin:16px 0; }
  .modal-fondo { display:none; position:fixed; inset:0; background:rgba(28,31,38,.55); align-items:center; justify-content:center; padding:20px; z-index:20; }
  .modal-fondo.abierto { display:flex; }
  .modal { background:#fff; border-radius:16px; max-width:440px; width:100%; padding:22px; }
  .modal h3 { font-size:17px; margin-bottom:14px; }
  .campo { margin-bottom:12px; }
  .campo label { display:block; font-size:12px; color:var(--suave); margin-bottom:4px; }
  .campo small { color:#9ca3af; }
  .campo input, .campo select { width:100%; border:1px solid #ddd; border-radius:8px; padding:9px 11px; font-size:14px; font-family:inherit; }
  .modal .acciones { display:flex; gap:10px; margin-top:16px; }
  .vacio { text-align:center; padding:24px; color:var(--suave); }
</style></head>
<body><div class="cont">
  <a class="volver" href="/__SLUG__">← Volver al inicio</a>
  <h1>Mis cuentas propias</h1>
  <div class="expl">Cuando aparezca uno de estos nombres en tus notificaciones, pagoOK sabrá que es tu propia cuenta y lo mostrará aparte del flujo del negocio.</div>

  <table>
    <thead><tr><th>Nombre en la notificación</th><th>Billetera</th><th>Banco</th><th>Teléfono</th><th></th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>

  <div class="agregar"><button class="btn btn-jade" onclick="abrir()">+ Agregar cuenta</button></div>
</div>

<div class="modal-fondo" id="modal">
  <div class="modal">
    <h3 id="modal-titulo">Agregar cuenta</h3>
    <form id="form">
      <input type="hidden" id="f-id">
      <div class="campo">
        <label>Nombre como aparece en la notificación <small>(ej. "DUILIO RESTUCCIA" o "D. C. Restuccia E.")</small></label>
        <input id="f-nombre" required>
      </div>
      <div class="campo">
        <label>Billetera</label>
        <select id="f-billetera">
          <option value="yape">Yape</option><option value="plin">Plin</option>
          <option value="bcp">BCP</option><option value="bbva">BBVA</option>
          <option value="interbank">Interbank</option><option value="scotiabank">Scotiabank</option>
          <option value="pichincha">Pichincha</option><option value="banbif">BanBif</option>
          <option value="tunki">Tunki</option><option value="banco_nacion">Banco de la Nación</option>
          <option value="otro">Otro</option>
        </select>
      </div>
      <div class="campo"><label>Banco (opcional)</label><input id="f-banco"></div>
      <div class="campo"><label>Teléfono (opcional)</label><input id="f-telefono"></div>
      <div class="acciones">
        <button type="submit" class="btn btn-jade">Guardar</button>
        <button type="button" class="btn btn-mini" onclick="cerrar()">Cancelar</button>
      </div>
    </form>
  </div>
</div>

<script>
const SLUG = "__SLUG__";
let cuentas = __CUENTAS_JSON__;

function esc(s){ if(s==null) return ""; return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function pintar() {
  const tb = document.getElementById("tbody");
  if (!cuentas.length) { tb.innerHTML = '<tr><td colspan=5 class=vacio>Aún no tienes cuentas propias registradas.</td></tr>'; return; }
  tb.innerHTML = cuentas.map(c =>
    "<tr><td>"+esc(c.nombre_en_push)+"</td><td>"+esc(c.billetera)+"</td><td>"+esc(c.banco)+"</td><td>"+esc(c.telefono)+"</td>"+
    "<td><button class='btn btn-mini' data-edit='"+c.id+"'>Editar</button> <button class='btn btn-mini btn-del' data-del='"+c.id+"'>Eliminar</button></td></tr>"
  ).join("");
}
function abrir(c) {
  document.getElementById("modal-titulo").textContent = c ? "Editar cuenta" : "Agregar cuenta";
  document.getElementById("f-id").value = c ? c.id : "";
  document.getElementById("f-nombre").value = c ? c.nombre_en_push : "";
  document.getElementById("f-billetera").value = c ? c.billetera : "yape";
  document.getElementById("f-banco").value = c ? c.banco : "";
  document.getElementById("f-telefono").value = c ? c.telefono : "";
  document.getElementById("modal").classList.add("abierto");
}
function cerrar(){ document.getElementById("modal").classList.remove("abierto"); }

document.getElementById("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const id = document.getElementById("f-id").value;
  const datos = {
    nombre_en_push: document.getElementById("f-nombre").value.trim(),
    billetera: document.getElementById("f-billetera").value,
    banco: document.getElementById("f-banco").value.trim(),
    telefono: document.getElementById("f-telefono").value.trim(),
  };
  if (!datos.nombre_en_push) return;
  if (id) {
    await fetch("/"+SLUG+"/config/cuentas/"+id, { method:"PUT", headers:{"Content-Type":"application/json"}, body: JSON.stringify(datos) });
  } else {
    const fd = new URLSearchParams(datos);
    await fetch("/"+SLUG+"/config/cuentas", { method:"POST", headers:{"Content-Type":"application/x-www-form-urlencoded"}, body: fd.toString() });
  }
  location.reload();
});
document.addEventListener("click", async (ev) => {
  const e = ev.target.closest("[data-edit]");
  const d = ev.target.closest("[data-del]");
  if (e) { const c = cuentas.find(x => String(x.id) === e.dataset.edit); if (c) abrir(c); }
  if (d) {
    await fetch("/"+SLUG+"/config/cuentas/"+d.dataset.del, { method:"DELETE" });
    location.reload();
  }
});
pintar();
</script>
</body></html>"""
    return (plantilla
            .replace("__CUENTAS_JSON__", _json.dumps(cuentas))
            .replace("__SLUG__", slug)
            .replace("__NOMBRE__", nombre_empresa))
