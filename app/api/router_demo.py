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
    tipo = (getattr(pago, "tipo", None) or "ingreso").lower()
    posible_interno = bool(getattr(pago, "posible_interno", False))
    confirmacion = getattr(pago, "confirmacion_usuario", None)
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
        # Egresos + detección de cuentas propias
        "tipo": tipo,
        "es_egreso": tipo == "egreso",
        "posible_interno": posible_interno,
        "confirmacion_usuario": confirmacion,
        # Badge "¿Es tuyo?" solo si es sugerencia sin revisar
        "necesita_revision": posible_interno and confirmacion is None,
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

        pagos = _obtener_pagos_recientes(db, empresa.id, limite=40)
        serializados = [_serializar_pago(p) for p in pagos]
        ingresos = [p for p in serializados if not p["es_egreso"]]
        egresos = [p for p in serializados if p["es_egreso"]]

        from app.services.resumen_empresa import calcular_resumen_dia, resumen_serializable
        hoy_lima = datetime.now(TZ_LIMA).date()
        resumen = resumen_serializable(calcular_resumen_dia(empresa.id, hoy_lima, db))

        return {
            "ok": True,
            "empresa": empresa.nombre_comercial or empresa.razon_social or slug,
            "slug": slug,
            "pagos": serializados,               # compat: lista completa como antes
            "ingresos": ingresos[:LIMITE_PAGOS],
            "egresos": egresos[:LIMITE_PAGOS],
            "resumen_hoy": resumen,
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
    """Pantalla pública rediseñada (paleta "Amazonía Sereno").

    HTML autocontenido, polling cada 5 s contra /api/v1/demo/{slug}. Muestra
    resumen del día (total y "del negocio"), últimos ingresos y egresos, y un
    badge "¿Es tuyo?" con modal HTML (sin confirm nativo) para confirmar si un
    posible interno es cuenta propia o de otra persona.
    """
    plantilla = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>pagoOK · __NOMBRE__</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='18' fill='%230f766e'/><text x='50' y='66' font-family='Georgia,serif' font-size='42' font-weight='bold' text-anchor='middle' fill='%23faf6ef'>OK</text></svg>">
  <style>
    :root {
      --jade: #0f766e; --jade-claro: #14b8a6;
      --coral: #ea6d40; --coral-hover: #f97316;
      --crema-fondo: #faf6ef; --crema-tarjeta: #ffffff;
      --grafito: #1c1f26; --grafito-suave: #4b5563;
      --terracota: #f5cec0; --indigo: #4c5578; --amarillo: #fbbf24;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--crema-fondo); color: var(--grafito);
      font-family: 'Inter', -apple-system, system-ui, sans-serif;
      min-height: 100vh; padding: 16px; line-height: 1.4;
    }
    .container { max-width: 760px; margin: 0 auto; }
    h1, h2, .monto { font-family: 'Space Grotesk', 'Inter', sans-serif; }
    .num { font-variant-numeric: tabular-nums; }

    .header {
      display: flex; align-items: center; justify-content: space-between;
      gap: 12px; padding: 6px 0 18px; flex-wrap: wrap;
    }
    .marca { display: flex; align-items: baseline; gap: 8px; }
    .marca .logo { font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 22px; color: var(--jade); }
    .marca .empresa { font-size: 15px; font-weight: 600; color: var(--grafito); }
    .marca .fecha { display: block; font-size: 12px; color: var(--grafito-suave); font-weight: 400; }
    .pill-live {
      display: inline-flex; align-items: center; gap: 6px;
      background: rgba(15,118,110,0.09); border: 1px solid var(--jade);
      color: var(--jade); border-radius: 999px; padding: 5px 12px;
      font-size: 11px; font-weight: 700; letter-spacing: 0.6px;
    }
    .punto { width: 8px; height: 8px; border-radius: 50%; background: var(--jade-claro); animation: latido 1.5s infinite; }
    @keyframes latido { 0%,100% { opacity: 1; transform: scale(1);} 50% { opacity: .4; transform: scale(1.4);} }

    .seccion-titulo { font-size: 13px; font-weight: 700; color: var(--grafito); letter-spacing: .3px; margin: 26px 0 12px; display: flex; align-items: center; justify-content: space-between; }
    .ver-todos { font-size: 12px; font-weight: 600; color: var(--jade); text-decoration: none; border: 1px solid var(--jade); border-radius: 8px; padding: 5px 10px; }
    .ver-todos:hover { background: rgba(15,118,110,0.08); }

    .fila-tarjetas { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
    .tarjeta {
      background: var(--crema-tarjeta); border-radius: 14px; padding: 16px;
      box-shadow: 0 2px 10px rgba(28,31,38,0.06); border: 1px solid rgba(28,31,38,0.05);
    }
    .tarjeta .rotulo { font-size: 12px; color: var(--grafito-suave); font-weight: 500; }
    .tarjeta .monto { font-size: 24px; font-weight: 700; margin-top: 6px; }
    .tarjeta .conteo { font-size: 11px; color: var(--grafito-suave); margin-top: 4px; }
    .tarjeta.chica .monto { font-size: 19px; }
    .t-ingreso .monto { color: var(--jade); }
    .t-egreso .monto { color: var(--coral); }
    .t-dif .monto { color: var(--grafito); }
    .dif-pos { color: var(--jade) !important; }
    .dif-neg { color: var(--coral) !important; }

    .divisor { height: 1px; background: rgba(28,31,38,0.10); margin: 22px 0 14px; }
    .subtitulo { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: var(--indigo); margin-bottom: 12px; }
    .ayuda { position: relative; cursor: help; display: inline-flex; }
    .ayuda .ico { width: 16px; height: 16px; border-radius: 50%; background: var(--indigo); color: #fff; font-size: 11px; display: flex; align-items: center; justify-content: center; font-weight: 700; }
    .ayuda .tip { display: none; position: absolute; left: 22px; top: -4px; width: 240px; background: var(--grafito); color: #fff; font-size: 11px; font-weight: 400; padding: 8px 10px; border-radius: 8px; z-index: 5; }
    .ayuda:hover .tip { display: block; }

    .lista { display: flex; flex-direction: column; gap: 8px; }
    .op {
      background: var(--crema-tarjeta); border-radius: 12px; padding: 12px 14px;
      display: flex; align-items: center; gap: 12px;
      box-shadow: 0 1px 6px rgba(28,31,38,0.05); border: 1px solid rgba(28,31,38,0.05);
    }
    .op .hora { font-size: 12px; color: var(--grafito-suave); min-width: 46px; }
    .op .metodo { flex-shrink: 0; padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 800; color: #fff; min-width: 58px; text-align: center; }
    .op .quien { flex: 1; min-width: 0; }
    .op .quien .nombre { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .op .quien .flecha { color: var(--coral); font-weight: 700; }
    .op .monto { font-size: 17px; font-weight: 700; }
    .op.ingreso .monto { color: var(--jade); }
    .op.egreso .monto { color: var(--coral); }
    .badge-tuyo {
      background: #fef3c7; border: 1px solid var(--amarillo); color: var(--grafito);
      font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 999px; cursor: pointer;
    }
    .vacio { text-align: center; padding: 26px 16px; color: var(--grafito-suave); border: 1px dashed rgba(28,31,38,0.15); border-radius: 12px; font-size: 13px; }

    .footer { margin-top: 30px; padding-top: 16px; border-top: 1px solid rgba(28,31,38,0.10); text-align: center; font-size: 11px; color: var(--grafito-suave); }
    .footer a { color: var(--jade); text-decoration: none; }

    .modal-fondo { display: none; position: fixed; inset: 0; background: rgba(28,31,38,0.55); align-items: center; justify-content: center; padding: 20px; z-index: 20; }
    .modal-fondo.abierto { display: flex; }
    .modal { background: #fff; border-radius: 16px; max-width: 400px; width: 100%; padding: 22px; }
    .modal h3 { font-family: 'Space Grotesk', sans-serif; font-size: 18px; margin-bottom: 6px; }
    .modal p { font-size: 13px; color: var(--grafito-suave); margin-bottom: 16px; }
    .modal .dato { background: var(--crema-fondo); border-radius: 10px; padding: 10px 12px; margin-bottom: 16px; font-size: 14px; }
    .modal .botones { display: flex; flex-direction: column; gap: 10px; }
    .btn { border: none; border-radius: 10px; padding: 13px; font-size: 14px; font-weight: 700; cursor: pointer; font-family: inherit; }
    .btn-si { background: var(--jade); color: #fff; }
    .btn-si:hover { background: #0d6459; }
    .btn-no { background: #fff; color: var(--coral); border: 1.5px solid var(--coral); }
    .btn-no:hover { background: rgba(234,109,64,0.08); }
    .btn-cerrar { background: transparent; color: var(--grafito-suave); font-weight: 500; }

    @media (max-width: 560px) {
      .fila-tarjetas { grid-template-columns: 1fr 1fr; }
      .fila-tarjetas .tarjeta.t-dif { grid-column: 1 / -1; }
      .op .hora { min-width: 40px; }
    }
  </style>
</head>
<body>
<div class="container">

  <div class="header">
    <div class="marca">
      <span class="logo">pagoOK</span>
      <span>
        <span class="empresa">__NOMBRE__</span>
        <span class="fecha" id="fecha">&nbsp;</span>
      </span>
    </div>
    <span class="pill-live"><span class="punto"></span> EN VIVO</span>
  </div>

  <h2 class="seccion-titulo">Tu día en un vistazo</h2>
  <div class="fila-tarjetas">
    <div class="tarjeta t-ingreso">
      <div class="rotulo">Ingresos</div>
      <div class="monto num" id="tot-ing">S/ 0.00</div>
      <div class="conteo" id="cnt-ing">0 operaciones</div>
    </div>
    <div class="tarjeta t-egreso">
      <div class="rotulo">Egresos</div>
      <div class="monto num" id="tot-egr">S/ 0.00</div>
      <div class="conteo" id="cnt-egr">0 operaciones</div>
    </div>
    <div class="tarjeta t-dif">
      <div class="rotulo">Diferencia</div>
      <div class="monto num" id="tot-dif">S/ 0.00</div>
      <div class="conteo">del día</div>
    </div>
  </div>

  <div class="divisor"></div>

  <div class="subtitulo">
    Excluyendo tus cuentas propias
    <span class="ayuda"><span class="ico">?</span>
      <span class="tip">Las transferencias entre tus billeteras se excluyen del cálculo del negocio.</span>
    </span>
  </div>
  <div class="fila-tarjetas">
    <div class="tarjeta chica t-ingreso">
      <div class="rotulo">Ingresos del negocio</div>
      <div class="monto num" id="neg-ing">S/ 0.00</div>
    </div>
    <div class="tarjeta chica t-egreso">
      <div class="rotulo">Egresos del negocio</div>
      <div class="monto num" id="neg-egr">S/ 0.00</div>
    </div>
    <div class="tarjeta chica t-dif">
      <div class="rotulo">Del negocio hoy</div>
      <div class="monto num" id="neg-dif">S/ 0.00</div>
    </div>
  </div>

  <h2 class="seccion-titulo">Últimos ingresos
    <a class="ver-todos" href="/__SLUG__/ingresos">Ver todos</a>
  </h2>
  <div id="lista-ingresos" class="lista"></div>

  <h2 class="seccion-titulo">Últimos egresos
    <a class="ver-todos" href="/__SLUG__/egresos">Ver todos</a>
  </h2>
  <div id="lista-egresos" class="lista"></div>

  <div class="footer">
    <span id="actualizado">Conectando...</span> · Powered by <a href="https://pagook.pro">pagoOK</a>
  </div>
</div>

<div class="modal-fondo" id="modal">
  <div class="modal">
    <h3>¿Esta cuenta es tuya?</h3>
    <p>Si es una de tus propias billeteras, la separaremos del flujo del negocio.</p>
    <div class="dato" id="modal-dato">—</div>
    <div class="botones">
      <button class="btn btn-si" onclick="confirmar(true)">Sí, es mía</button>
      <button class="btn btn-no" onclick="confirmar(false)">No, es de otra persona</button>
      <button class="btn btn-cerrar" onclick="cerrarModal()">Cancelar</button>
    </div>
  </div>
</div>

<script>
const SLUG = "__SLUG__";
let pagoEnRevision = null;

function fmt(monto, moneda) {
  const simbolo = moneda === "USD" ? "$" : "S/";
  return simbolo + " " + monto;
}
function esc(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function fechaHoy() {
  try {
    const f = new Date().toLocaleDateString("es-PE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    document.getElementById("fecha").textContent = "Hoy · " + f;
  } catch (e) {}
}

function pintarResumen(r) {
  if (!r) return;
  const s = (n) => "S/ " + Number(n).toFixed(2);
  document.getElementById("tot-ing").textContent = s(r.total_ingresos);
  document.getElementById("tot-egr").textContent = s(r.total_egresos);
  document.getElementById("cnt-ing").textContent = r.cuenta_ingresos + " operaciones";
  document.getElementById("cnt-egr").textContent = r.cuenta_egresos + " operaciones";
  const dif = document.getElementById("tot-dif");
  dif.textContent = (r.diferencia >= 0 ? "+ " : "- ") + "S/ " + Math.abs(r.diferencia).toFixed(2);
  dif.className = "monto num " + (r.diferencia >= 0 ? "dif-pos" : "dif-neg");
  document.getElementById("neg-ing").textContent = s(r.ingresos_negocio);
  document.getElementById("neg-egr").textContent = s(r.egresos_negocio);
  const nd = document.getElementById("neg-dif");
  nd.textContent = (r.diferencia_negocio >= 0 ? "+ " : "- ") + "S/ " + Math.abs(r.diferencia_negocio).toFixed(2);
  nd.className = "monto num " + (r.diferencia_negocio >= 0 ? "dif-pos" : "dif-neg");
}

function filaOp(p, esEgreso) {
  const badge = p.necesita_revision
    ? '<span class="badge-tuyo" data-id="' + p.id + '" data-titular="' + esc(p.titular) + '" data-monto="' + esc(p.monto) + '" data-moneda="' + esc(p.moneda || "PEN") + '">¿Es tuyo?</span>'
    : "";
  const flecha = esEgreso ? '<span class="flecha">→ </span>' : "";
  return '<div class="op ' + (esEgreso ? "egreso" : "ingreso") + '">' +
      '<span class="hora num">' + esc(p.hora) + '</span>' +
      '<span class="metodo" style="background:' + p.metodo_color + '">' + esc(p.metodo_label) + '</span>' +
      '<span class="quien"><span class="nombre">' + flecha + esc(p.titular_corto) + '</span></span>' +
      badge +
      '<span class="monto num">' + fmt(p.monto, p.moneda) + '</span>' +
    '</div>';
}

// Delegación: un solo listener para todos los badges "¿Es tuyo?".
document.addEventListener("click", function (ev) {
  const b = ev.target.closest(".badge-tuyo");
  if (!b) return;
  abrirModal(b.dataset.id, b.dataset.titular, b.dataset.monto, b.dataset.moneda);
});

function pintarLista(id, items, esEgreso) {
  const cont = document.getElementById(id);
  if (!items || items.length === 0) {
    cont.innerHTML = '<div class="vacio">Aún no hay ' + (esEgreso ? "egresos" : "ingresos") + ' hoy.</div>';
    return;
  }
  cont.innerHTML = items.map(p => filaOp(p, esEgreso)).join("");
}

async function cargar() {
  try {
    const r = await fetch("/api/v1/demo/" + SLUG);
    const data = await r.json();
    if (!data.ok) return;
    pintarResumen(data.resumen_hoy);
    pintarLista("lista-ingresos", data.ingresos, false);
    pintarLista("lista-egresos", data.egresos, true);
    document.getElementById("actualizado").textContent = "Actualizado a las " + data.timestamp_server;
  } catch (e) {
    console.error("Error cargando:", e);
  }
}

function abrirModal(id, titular, monto, moneda) {
  pagoEnRevision = id;
  document.getElementById("modal-dato").textContent = (titular || "(sin titular)") + " · " + fmt(monto, moneda);
  document.getElementById("modal").classList.add("abierto");
}
function cerrarModal() {
  pagoEnRevision = null;
  document.getElementById("modal").classList.remove("abierto");
}
async function confirmar(esPropia) {
  if (!pagoEnRevision) return;
  try {
    await fetch("/api/v1/" + SLUG + "/pagos/" + pagoEnRevision + "/confirmacion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ es_propia: esPropia })
    });
  } catch (e) {}
  cerrarModal();
  cargar();
}

fechaHoy();
cargar();
setInterval(cargar, 5000);
</script>
</body>
</html>"""
    return plantilla.replace("__SLUG__", slug).replace("__NOMBRE__", nombre_empresa)
