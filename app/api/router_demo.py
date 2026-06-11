"""Router público para demostración a testers.

Endpoint:
  GET /{slug}                 -> pantalla pública con pagos recientes
  GET /api/v1/demo/{slug}     -> JSON con pagos recientes (para polling)
  POST /api/v1/demo/{slug}/buscar -> busca pago por monto

NO requiere autenticación. Los slugs son públicos (security through obscurity).
Para v2 se agregará PIN guardado en app Android.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from fastapi import APIRouter, Request, HTTPException, Body, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc, and_, func, or_
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.admin.db import SessionLocal, get_db
from app.admin.models import Empresa
from app.api.models_webhook import PagoDetectado, Dispositivo

templates = Jinja2Templates(directory="templates")

# Zona horaria de Lima/Perú
TZ_LIMA = ZoneInfo("America/Lima")

# Límite de pagos a mostrar
LIMITE_PAGOS = 10

# Cuántos minutos atrás considerar para "pagos recientes"
VENTANA_MINUTOS = 60


router = APIRouter(tags=["demo"])


def _hora_local_str(dt: datetime | None) -> str:
    """Convierte UTC -> hora local Lima formateada como HH:MM:SS."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        # Asumimos UTC si viene naive
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(TZ_LIMA)
    return local.strftime("%H:%M:%S")


def _fecha_local_str(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(TZ_LIMA)
    return local.strftime("%d/%m/%Y")


def _metodo_color(metodo: str) -> str:
    return {
        "yape": "#8a05be",
        "plin": "#00d1c1",
        "transferencia": "#f5a623",
    }.get((metodo or "").lower(), "#888888")


def _metodo_label(metodo: str) -> str:
    return {
        "yape": "YAPE",
        "plin": "PLIN",
        "transferencia": "TRANSF.",
    }.get((metodo or "").lower(), (metodo or "?").upper())


def _serializar_pago(pago: PagoDetectado) -> dict:
    """Convierte PagoDetectado a dict para JSON."""
    monto = pago.monto or Decimal("0")
    return {
        "id": pago.id,
        "metodo": (pago.metodo or "").lower(),
        "metodo_label": _metodo_label(pago.metodo or ""),
        "metodo_color": _metodo_color(pago.metodo or ""),
        "monto": f"{monto:.2f}",
        "monto_num": float(monto),
        "moneda": pago.moneda or "PEN",
        "titular": pago.titular or "(sin titular)",
        "titular_corto": pago.titular_corto or pago.titular or "—",
        "banco": pago.banco or "",
        "codigo_operacion": pago.codigo_operacion or "",
        "fecha": _fecha_local_str(pago.recibido_en),
        "hora": _hora_local_str(pago.recibido_en),
    }


def _obtener_empresa_por_slug(db, slug: str) -> Empresa | None:
    """Busca empresa por slug (case-insensitive) o por id_fiscal (RUC) exacto."""
    if not slug:
        return None
    slug_norm = slug.strip().lower()
    # 1) Buscar por slug exacto (case-insensitive)
    empresa = db.execute(
        select(Empresa).where(func.lower(Empresa.slug) == slug_norm)
    ).scalar_one_or_none()
    if empresa is not None:
        return empresa
    # 2) Si no se encontró por slug y parece RUC (todo dígitos), buscar por id_fiscal
    slug_raw = slug.strip()
    if slug_raw.isdigit():
        empresa = db.execute(
            select(Empresa).where(Empresa.id_fiscal == slug_raw)
        ).scalar_one_or_none()
    return empresa


def _obtener_pagos_recientes(db, empresa_id: int, limite: int = LIMITE_PAGOS) -> list[PagoDetectado]:
    """Trae los últimos N pagos de la empresa, descendente."""
    return list(db.execute(
        select(PagoDetectado)
        .where(PagoDetectado.empresa_id == empresa_id)
        .order_by(desc(PagoDetectado.recibido_en))
        .limit(limite)
    ).scalars().all())


# ============================================================
# JSON ENDPOINTS (para polling desde la pantalla)
# ============================================================

@router.get("/api/v1/demo/{slug}")
def api_demo_pagos(slug: str):
    """Devuelve JSON con los últimos pagos de la empresa.

    Polling cada 5 segundos desde la pantalla pública.
    """
    db = SessionLocal()
    try:
        empresa = _obtener_empresa_por_slug(db, slug)
        if empresa is None:
            return JSONResponse({"ok": False, "error": "empresa_no_encontrada"}, status_code=404)

        pagos = _obtener_pagos_recientes(db, empresa.id)
        return {
            "ok": True,
            "empresa": empresa.nombre_comercial or empresa.razon_social or slug,
            "slug": slug,
            "pagos": [_serializar_pago(p) for p in pagos],
            "timestamp_server": datetime.now(TZ_LIMA).strftime("%H:%M:%S"),
        }
    finally:
        db.close()


@router.post("/api/v1/demo/{slug}/buscar")
def api_demo_buscar(slug: str, payload: dict = Body(...)):
    """Busca un pago por monto exacto en la última hora.

    Body: {"monto": "1.50"}
    """
    db = SessionLocal()
    try:
        empresa = _obtener_empresa_por_slug(db, slug)
        if empresa is None:
            return JSONResponse({"ok": False, "error": "empresa_no_encontrada"}, status_code=404)

        try:
            monto_str = str(payload.get("monto", "")).strip().replace(",", ".")
            monto_buscado = Decimal(monto_str)
        except Exception:
            return {"ok": False, "error": "monto_invalido"}

        if monto_buscado <= 0:
            return {"ok": False, "error": "monto_invalido"}

        # Buscar en los últimos 60 minutos
        desde = datetime.now(timezone.utc) - timedelta(minutes=VENTANA_MINUTOS)

        pago = db.execute(
            select(PagoDetectado)
            .where(and_(
                PagoDetectado.empresa_id == empresa.id,
                PagoDetectado.monto == monto_buscado,
                PagoDetectado.recibido_en >= desde,
            ))
            .order_by(desc(PagoDetectado.recibido_en))
            .limit(1)
        ).scalar_one_or_none()

        if pago is None:
            return {"ok": True, "encontrado": False}

        return {
            "ok": True,
            "encontrado": True,
            "pago": _serializar_pago(pago),
        }
    finally:
        db.close()


# ============================================================
# PÁGINA PÚBLICA /testers (grilla de empresas)
# ============================================================

@router.get("/testers", response_class=HTMLResponse)
def vista_testers(request: Request, db: Session = Depends(get_db)):
    """Página pública con grilla de empresas que usan pagoOK."""
    empresas = (
        db.query(Empresa)
        .filter(Empresa.visible_en_testers == True)
        .filter(Empresa.activa == True)
        .order_by(Empresa.creada_en.asc())
        .all()
    )

    resumen = []
    ahora = datetime.utcnow()
    for emp in empresas:
        total_pagos = (
            db.query(func.count(PagoDetectado.id))
            .filter(PagoDetectado.empresa_id == emp.id)
            .scalar() or 0
        )
        ultimo_ping = (
            db.query(func.max(Dispositivo.ultimo_ping))
            .filter(Dispositivo.empresa_id == emp.id)
            .scalar()
        )
        estado = "sin_dispositivo"
        if ultimo_ping is not None:
            if ahora - ultimo_ping < timedelta(minutes=30):
                estado = "activo"
            else:
                estado = "inactivo"

        # URL preferida: slug alias si difiere del RUC, sino el RUC
        url_slug = emp.slug if emp.slug != emp.id_fiscal else emp.id_fiscal

        resumen.append({
            "id": emp.id,
            "nombre_comercial": emp.nombre_comercial or emp.razon_social,
            "razon_social": emp.razon_social,
            "id_fiscal": emp.id_fiscal,
            "slug": emp.slug,
            "url_slug": url_slug,
            "logo_url": emp.logo_url,
            "total_pagos": total_pagos,
            "estado": estado,
        })

    context = {
        "request": request,
        "empresas": resumen,
        # CONFIG: ajustar manualmente conforme entren testers
        "cupos_total": 50,
        "cupos_disponibles": 47,
        "wa_numero": "51967317946",
        "wa_mensaje_invitacion": (
            "Hola, quiero ser tester de pagoOK.\n\n"
            "Mi info:\n"
            "RUC:\n"
            "Nombre Comercial:\n"
            "Email:\n"
            "Ciudad:"
        ),
        # Videos: URLs vacías por default. Cuando grabes, pega aquí la URL embed
        "video_demo_url": "",   # ej: "https://www.youtube.com/embed/abc123"
        "video_casos_url": "",
    }
    return templates.TemplateResponse("testers/grilla.html", context)


# ============================================================
# FLUJO DE CAJA SEMANAL (7 días x método, ingresos + egresos)
# ============================================================

METODOS_ORDEN = ["yape", "plin", "bim", "p51", "pix", "transferencia"]


def _calcular_flujo_caja(db, slug: str) -> dict | None:
    """Construye la matriz de flujo de caja de los últimos 7 días corridos.

    Devuelve un dict (o None si la empresa no existe). Agrupa por fecha local
    de Lima usando `recibido_en` (UTC en BD) y por `tipo` ('ingreso'/'egreso').
    """
    empresa = _obtener_empresa_por_slug(db, slug)
    if empresa is None:
        return None

    # Rango: hoy (Lima) + 6 días anteriores
    hoy = datetime.now(TZ_LIMA).date()
    desde = hoy - timedelta(days=6)
    dias = [desde + timedelta(days=i) for i in range(7)]
    dias_str = [d.isoformat() for d in dias]

    # Ventana en UTC naive (como se guarda recibido_en) cubriendo el rango Lima
    inicio_utc = (
        datetime.combine(desde, time.min, tzinfo=TZ_LIMA)
        .astimezone(timezone.utc).replace(tzinfo=None)
    )
    fin_utc = (
        datetime.combine(hoy, time.max, tzinfo=TZ_LIMA)
        .astimezone(timezone.utc).replace(tzinfo=None)
    )

    pagos = list(db.execute(
        select(PagoDetectado)
        .where(and_(
            PagoDetectado.empresa_id == empresa.id,
            PagoDetectado.recibido_en >= inicio_utc,
            PagoDetectado.recibido_en <= fin_utc,
        ))
    ).scalars().all())

    matriz = {
        "ingreso": defaultdict(lambda: [0.0] * 7),
        "egreso": defaultdict(lambda: [0.0] * 7),
    }

    for p in pagos:
        if not p.recibido_en:
            continue
        dt = p.recibido_en
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = dt.astimezone(TZ_LIMA).date()
        idx = (d - desde).days
        if idx < 0 or idx > 6:
            continue
        tipo = (getattr(p, "tipo", None) or "ingreso").lower()
        if tipo not in ("ingreso", "egreso"):
            tipo = "ingreso"
        metodo = (p.metodo or "otro").lower()
        if metodo not in METODOS_ORDEN:
            metodo = "transferencia"  # cae en transferencia/otros
        try:
            matriz[tipo][metodo][idx] += float(p.monto or 0)
        except (TypeError, ValueError):
            continue

    ingresos = {m: matriz["ingreso"].get(m, [0.0] * 7) for m in METODOS_ORDEN}
    egresos = {m: matriz["egreso"].get(m, [0.0] * 7) for m in METODOS_ORDEN}

    total_ingresos = [sum(ingresos[m][i] for m in METODOS_ORDEN) for i in range(7)]
    total_egresos = [sum(egresos[m][i] for m in METODOS_ORDEN) for i in range(7)]
    neto_dia = [total_ingresos[i] - total_egresos[i] for i in range(7)]

    return {
        "empresa": {
            "slug": empresa.slug,
            "nombre": empresa.nombre_comercial or empresa.razon_social,
            "ruc": empresa.id_fiscal,
        },
        "rango": {"desde": desde.isoformat(), "hasta": hoy.isoformat()},
        "dias": dias_str,
        "metodos": METODOS_ORDEN,
        "ingresos": ingresos,
        "egresos": egresos,
        "total_ingresos": total_ingresos,
        "total_egresos": total_egresos,
        "neto_dia": neto_dia,
    }


@router.get("/api/v1/flujo-caja/{slug}")
def api_flujo_caja(slug: str, db: Session = Depends(get_db)):
    """Flujo de caja semanal de la empresa (JSON). Últimos 7 días corridos."""
    data = _calcular_flujo_caja(db, slug)
    if data is None:
        return JSONResponse({"ok": False, "error": "empresa_no_encontrada"}, status_code=404)
    return data


@router.get("/flujo-caja/{slug}", response_class=HTMLResponse)
def vista_flujo_caja(request: Request, slug: str, db: Session = Depends(get_db)):
    """Vista visual (HTML) del Flujo de Caja semanal."""
    data = _calcular_flujo_caja(db, slug)
    if data is None:
        return HTMLResponse(_html_no_encontrado(slug), status_code=404)
    return templates.TemplateResponse(
        "flujo_caja/semanal.html",
        {"request": request, "data": data},
    )


# ============================================================
# HTML ENDPOINT (la pantalla pública)
# ============================================================

@router.get("/{slug}", response_class=HTMLResponse)
def demo_pantalla_publica(slug: str, request: Request):
    """Pantalla pública que muestra los pagos en tiempo real.

    URL: pagook.pro/{slug}  (ej: pagook.pro/perusistemas)

    Lista whitelist de slugs reservados que NO deben capturarse aquí
    para no chocar con admin, api, etc.
    """
    # Slugs reservados del sistema (no son empresas)
    RESERVADOS = {
        "admin", "api", "static", "favicon.ico", "robots.txt",
        "demo", "docs", "openapi.json", "redoc", "health",
        "login", "logout", "register", "signup", "auth",
        "assets", "public", "templates", "testers",
        "videos", "qr", "caja", "sitemap.xml", "llms.txt",
    }
    if slug.lower() in RESERVADOS:
        raise HTTPException(status_code=404)

    db = SessionLocal()
    try:
        empresa = _obtener_empresa_por_slug(db, slug)
        if empresa is None:
            return HTMLResponse(_html_no_encontrado(slug), status_code=404)

        nombre = empresa.nombre_comercial or empresa.razon_social or slug
        return HTMLResponse(_html_pantalla(slug, nombre))
    finally:
        db.close()


def _html_no_encontrado(slug: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pagoOK — Empresa no encontrada</title>
<style>
  body {{ background: #1A0303; color: #FFF6E0; font-family: system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; min-height: 100vh;
         margin: 0; text-align: center; padding: 20px; }}
  .box {{ max-width: 500px; }}
  h1 {{ color: #F5A623; font-size: 32px; }}
  code {{ background: #2A0404; padding: 4px 8px; border-radius: 4px; color: #F5A623; }}
  a {{ color: #F5A623; }}
</style></head>
<body><div class="box">
<h1>Empresa no encontrada</h1>
<p>No existe el negocio <code>{slug}</code> en pagoOK.</p>
<p>Verifica el enlace, o visita <a href="https://pagook.pro">pagook.pro</a></p>
</div></body></html>"""


def _html_pantalla(slug: str, nombre_empresa: str) -> str:
    """Pantalla pública sin candado, con polling cada 5 segundos."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>pagoOK · {nombre_empresa}</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='15' fill='%234E0808'/><text x='50' y='65' font-family='Georgia,serif' font-size='40' font-weight='bold' text-anchor='middle' fill='%23F5A623'>OK</text></svg>">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: linear-gradient(180deg, #1A0303 0%, #2A0404 100%);
      color: #FFF6E0;
      font-family: -apple-system, system-ui, sans-serif;
      min-height: 100vh;
      padding: 16px;
    }}
    .container {{ max-width: 720px; margin: 0 auto; }}
    .header {{
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 0 20px; border-bottom: 1px solid #4E0808;
    }}
    .logo {{ display: flex; align-items: center; gap: 6px; font-family: Georgia, serif; font-size: 22px; font-weight: 700; }}
    .logo .pago {{ color: #FFFFFF; }}
    .logo .ok {{
      color: #F5A623; border: 2px solid #F5A623; padding: 0 8px;
      border-radius: 4px; line-height: 1.2;
    }}
    .empresa {{ color: #F5A623; font-size: 14px; font-weight: 600; text-align: right; }}
    .empresa small {{ display: block; color: #BBBBBB; font-weight: 400; font-size: 11px; }}

    .seccion-titulo {{
      color: #F5A623; font-size: 12px; font-weight: 700;
      letter-spacing: 1.5px; margin: 28px 0 12px;
    }}

    .seccion-subtitulo {{
      color: #BBBBBB; font-size: 12px; font-style: italic;
      margin: -8px 0 14px;
    }}

    .indicador-live {{
      display: inline-flex; align-items: center; gap: 6px;
      background: rgba(245,166,35,0.1); border: 1px solid #F5A623;
      border-radius: 20px; padding: 4px 12px; font-size: 11px;
      color: #F5A623; font-weight: 600;
    }}
    .punto {{
      display: inline-block; width: 8px; height: 8px; border-radius: 50%;
      background: #10B981; animation: pulse 1.5s infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.5; transform: scale(1.3); }}
    }}

    .pagos {{ display: flex; flex-direction: column; gap: 8px; }}

    .pago {{
      background: rgba(0,0,0,0.3);
      border: 1px solid #4E0808;
      border-radius: 10px;
      padding: 14px 16px;
      display: flex; align-items: center; gap: 14px;
      transition: transform 0.3s ease, border-color 0.3s ease;
    }}
    .pago.nuevo {{
      animation: aparecer 0.6s ease-out;
      border-color: #F5A623;
    }}
    @keyframes aparecer {{
      from {{ transform: translateY(-20px); opacity: 0; background: rgba(245,166,35,0.2); }}
      to {{ transform: translateY(0); opacity: 1; background: rgba(0,0,0,0.3); }}
    }}

    .badge {{
      flex-shrink: 0; padding: 4px 10px; border-radius: 6px;
      font-size: 10px; font-weight: 800; letter-spacing: 0.8px;
      color: #FFFFFF; min-width: 70px; text-align: center;
    }}
    .monto {{
      font-size: 22px; font-weight: 700; color: #F5A623;
      font-family: Georgia, serif; min-width: 120px;
    }}
    .info {{ flex: 1; min-width: 0; }}
    .titular {{
      font-size: 14px; font-weight: 600; color: #FFF6E0;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    .meta {{ font-size: 11px; color: #BBBBBB; margin-top: 2px; }}
    .hora {{ font-size: 12px; color: #888888; flex-shrink: 0; }}

    .vacio {{
      text-align: center; padding: 40px 20px; color: #888888;
      border: 1px dashed #4E0808; border-radius: 10px;
    }}
    .vacio strong {{ display: block; color: #F5A623; font-size: 16px; margin-bottom: 6px; }}

    .buscar-box {{
      background: rgba(0,0,0,0.3); border: 1px solid #4E0808;
      border-radius: 10px; padding: 16px; margin-top: 8px;
    }}
    .buscar-row {{ display: flex; gap: 8px; align-items: center; }}
    .buscar-row .moneda {{ color: #F5A623; font-weight: 700; }}
    .buscar-row input {{
      flex: 1; background: rgba(0,0,0,0.4); border: 1.5px solid #4E0808;
      border-radius: 8px; padding: 10px 12px; color: #F5A623;
      font-family: Georgia, serif; font-size: 18px; font-weight: 700;
      letter-spacing: 1px; text-align: center;
      outline: none; transition: border-color 0.2s;
    }}
    .buscar-row input:focus {{ border-color: #F5A623; }}
    .buscar-row input::placeholder {{ color: #555; font-family: -apple-system, system-ui, sans-serif; font-size: 13px; font-weight: 400; letter-spacing: normal; }}
    .buscar-row button {{
      background: #F5A623; color: #2A0404; border: none;
      padding: 10px 18px; border-radius: 8px; font-weight: 700;
      cursor: pointer; font-size: 13px; letter-spacing: 0.5px;
      transition: background 0.2s;
    }}
    .buscar-row button:hover {{ background: #FFC14F; }}
    .buscar-row button:disabled {{ background: #888; cursor: not-allowed; }}

    .resultado-buscar {{
      margin-top: 12px; padding: 14px; border-radius: 8px;
      font-size: 13px; line-height: 1.5;
    }}
    .resultado-ok {{ background: rgba(16,185,129,0.15); border: 1px solid #10B981; color: #10B981; }}
    .resultado-ok strong {{ display: block; color: #FFFFFF; font-size: 18px; margin-bottom: 4px; }}
    .resultado-no {{ background: rgba(239,68,68,0.1); border: 1px solid #EF4444; color: #FCA5A5; }}

    .footer {{
      margin-top: 32px; padding-top: 16px;
      border-top: 1px solid #4E0808;
      text-align: center; font-size: 11px; color: #888888;
    }}
    .footer a {{ color: #F5A623; text-decoration: none; }}

    @media (max-width: 600px) {{
      .pago {{ flex-wrap: wrap; gap: 8px; }}
      .monto {{ min-width: auto; font-size: 20px; }}
      .info {{ flex-basis: 100%; }}
      .empresa {{ font-size: 12px; }}
    }}
  </style>
</head>
<body>
<div class="container">

  <div class="header">
    <div class="logo"><span class="pago">pago</span><span class="ok">OK</span></div>
    <div class="empresa">
      {nombre_empresa}
      <small>{slug}</small>
    </div>
  </div>

  <div class="seccion-titulo">
    Últimos pagos recibidos &nbsp;
    <span class="indicador-live"><span class="punto"></span> EN VIVO</span>
  </div>

  <div id="pagos" class="pagos">
    <div class="vacio">
      <strong>Esperando pagos...</strong>
      Apenas alguien envíe un Yape, Plin o transferencia, aparecerá aquí en segundos.
    </div>
  </div>

  <div class="seccion-titulo">¿Quieres verificar un Yape, Plin o Transferencia?</div>
  <div class="seccion-subtitulo">En la práctica el vendedor o cajero buscará por un nombre del cliente/pagador.</div>

  <div class="buscar-box">
    <div class="buscar-row">
      <span class="moneda">S/</span>
      <input type="text" id="buscar-monto" placeholder="Digita monto exacto que esperas recibir (ej: 1.00)" inputmode="decimal">
      <button onclick="buscarPago()">Buscar</button>
    </div>
    <div id="resultado-buscar"></div>
  </div>

  <div class="footer">
    pagoOK detecta pagos Yape · Plin · Transferencias en tiempo real<br>
    <a href="https://pagook.pro">pagook.pro</a> · por Perú Sistemas Pro
  </div>

</div>

<script>
const SLUG = "{slug}";
let pagosVistos = new Set();
let primeraCarga = true;

async function cargarPagos() {{
  try {{
    const r = await fetch(`/api/v1/demo/${{SLUG}}`);
    const data = await r.json();
    if (!data.ok) return;

    const cont = document.getElementById('pagos');

    if (data.pagos.length === 0) {{
      cont.innerHTML = `<div class="vacio"><strong>Esperando pagos...</strong>Apenas alguien envíe un Yape, Plin o transferencia, aparecerá aquí en segundos.</div>`;
      return;
    }}

    const idsActuales = new Set(data.pagos.map(p => p.id));
    const nuevos = data.pagos.filter(p => !pagosVistos.has(p.id));

    cont.innerHTML = data.pagos.map(p => {{
      const esNuevo = !primeraCarga && nuevos.some(n => n.id === p.id);
      return `<div class="pago ${{esNuevo ? 'nuevo' : ''}}">
        <div class="badge" style="background:${{p.metodo_color}}">${{p.metodo_label}}</div>
        <div class="monto">S/ ${{p.monto}}</div>
        <div class="info">
          <div class="titular">${{escapeHtml(p.titular_corto)}}</div>
          <div class="meta">${{escapeHtml(p.banco || p.titular || '')}}</div>
        </div>
        <div class="hora">${{p.hora}}</div>
      </div>`;
    }}).join('');

    pagosVistos = idsActuales;
    primeraCarga = false;
  }} catch (e) {{
    console.error('Error cargando pagos:', e);
  }}
}}

async function buscarPago() {{
  const input = document.getElementById('buscar-monto');
  const cont = document.getElementById('resultado-buscar');
  const monto = input.value.trim().replace(',', '.');

  if (!monto || isNaN(parseFloat(monto)) || parseFloat(monto) <= 0) {{
    cont.innerHTML = '<div class="resultado-buscar resultado-no">Digita un monto válido (ej: 1.50)</div>';
    return;
  }}

  cont.innerHTML = '<div class="resultado-buscar" style="color:#888">Buscando...</div>';

  try {{
    const r = await fetch(`/api/v1/demo/${{SLUG}}/buscar`, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{monto: monto}})
    }});
    const data = await r.json();

    if (!data.ok) {{
      cont.innerHTML = '<div class="resultado-buscar resultado-no">Error: ' + (data.error || 'desconocido') + '</div>';
      return;
    }}

    if (!data.encontrado) {{
      cont.innerHTML = `<div class="resultado-buscar resultado-no">
        No encontramos un pago de <strong style="color:#F5A623">S/ ${{monto}}</strong> en los últimos 60 minutos.<br>
        Verifica el monto exacto, o espera unos segundos a que llegue al sistema.
      </div>`;
      return;
    }}

    const p = data.pago;
    cont.innerHTML = `<div class="resultado-buscar resultado-ok">
      <strong>✓ ¡Es tu pago!</strong>
      <div style="margin-top:8px; color:#FFF6E0">
        <div><b>${{escapeHtml(p.titular)}}</b></div>
        <div style="font-size:22px; font-family:Georgia,serif; color:#F5A623; margin:6px 0">S/ ${{p.monto}}</div>
        <div style="font-size:12px; color:#BBBBBB">${{p.metodo_label}} · ${{p.fecha}} ${{p.hora}}</div>
      </div>
      <div style="margin-top:10px; font-size:11px; color:#10B981">
        Detectado en tiempo real por pagoOK
      </div>
    </div>`;
    input.value = '';
  }} catch (e) {{
    cont.innerHTML = '<div class="resultado-buscar resultado-no">Error de conexión. Intenta de nuevo.</div>';
  }}
}}

function escapeHtml(s) {{
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[ch]);
}}

document.getElementById('buscar-monto').addEventListener('keypress', e => {{
  if (e.key === 'Enter') buscarPago();
}});

cargarPagos();
setInterval(cargarPagos, 5000);
</script>
</body>
</html>"""