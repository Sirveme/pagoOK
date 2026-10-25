"""Canal de correo bancario + administración de bancos (zWeb-06).

Entrada (Email Worker de Cloudflare):
  POST /api/v1/correo/entrante        X-Correo-Secreto + X-Destinatario, cuerpo = MIME crudo

Panel del dueño:
  GET  /panel/correo                  "Correo del banco"
  GET  /api/v1/panel/correo           dirección, código de Gmail, últimos 10 correos
  POST /api/v1/panel/correo/regenerar

Público:
  POST /api/v1/bancos/solicitud       "Pídelo" (5 por IP al día)

Administrador (ADMIN_TOKEN):
  GET  /admin/bancos?token=...
  GET/POST /api/v1/admin/bancos, PUT /api/v1/admin/bancos/{id}      (X-Admin-Token)
  GET  /api/v1/admin/bancos/solicitudes
  GET  /api/v1/admin/correos?estado=sin_formato|rechazado
  POST /api/v1/admin/correos/{id}/reprocesar
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.deps_empresa import SesionCtx, requiere, resolver_dispositivo, sesion_opcional
from app.api.models_correo import CorreoRaw, SolicitudBanco
from app.api.models_multiempresa import BancoConfig
from app.services import correo_entrante
from app.services.eventos import a_iso_lima
from app.services.secretos import hash_codigo

logger = logging.getLogger("pagook")
router = APIRouter(tags=["correo-bancos"])
templates = Jinja2Templates(directory="templates")

TAMANO_MAXIMO_CORREO = 5 * 1024 * 1024
MAX_SOLICITUDES_BANCO_IP_DIA = 5
TIPOS_BANCO = ("billetera", "banco", "caja")
_RE_PAQUETE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
_RE_DOMINIO = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_RE_CLAVE = re.compile(r"^[a-z0-9_]{2,30}$")
_RE_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


# =============================================================
# ENTRADA DESDE EL WORKER
# =============================================================

@router.post("/api/v1/correo/entrante")
async def correo_entrante_endpoint(
    request: Request,
    x_correo_secreto: str | None = Header(default=None, alias="X-Correo-Secreto"),
    x_destinatario: str | None = Header(default=None, alias="X-Destinatario"),
    db: Session = Depends(get_db),
):
    secreto = os.getenv("CORREO_ENTRANTE_SECRETO", "").strip()
    if not secreto:
        raise HTTPException(503, "Canal de correo desactivado (falta CORREO_ENTRANTE_SECRETO)")
    if not x_correo_secreto or not hmac.compare_digest(x_correo_secreto.encode(), secreto.encode()):
        raise HTTPException(401, "No autorizado")
    if not x_destinatario:
        raise HTTPException(400, "Falta X-Destinatario")
    mime = await request.body()
    if not mime:
        raise HTTPException(400, "Correo vacío")
    if len(mime) > TAMANO_MAXIMO_CORREO:
        raise HTTPException(413, "Correo demasiado grande")
    correo = correo_entrante.procesar_correo(db, x_destinatario, mime)
    return {"ok": True, "id": correo.id, "estado": correo.estado}


# =============================================================
# PANEL DEL DUEÑO: "Correo del banco"
# =============================================================

def _dominios_conocidos(db: Session, pais: str) -> list[dict]:
    return [{"nombre": b.nombre, "dominios": b.dominios_correo or []}
            for b in db.query(BancoConfig).filter(BancoConfig.pais == pais, BancoConfig.activo == True)  # noqa: E712
            .order_by(BancoConfig.nombre) if b.dominios_correo]


def _estado_correo(db: Session, ctx: SesionCtx) -> dict:
    ce = correo_entrante.correo_de_empresa(db, ctx.empresa.id, crear=True)
    gmail = correo_entrante.ultima_confirmacion_gmail(db, ctx.empresa.id)
    recientes = (
        db.query(CorreoRaw).filter(CorreoRaw.empresa_id == ctx.empresa.id)
        .order_by(CorreoRaw.id.desc()).limit(10).all()
    )
    return {
        "direccion": correo_entrante.direccion(ce.codigo),
        "codigo_gmail": gmail.codigo_confirmacion if gmail else None,
        "bancos_con_correo": _dominios_conocidos(db, ctx.empresa.pais_codigo),
        "correos": [{
            "fecha": a_iso_lima(c.recibido_en), "remitente": c.remitente, "asunto": c.asunto,
            "estado": c.estado, "motivo": c.motivo,
        } for c in recientes],
    }


@router.get("/api/v1/panel/correo")
def api_panel_correo(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    return _estado_correo(db, ctx)


@router.post("/api/v1/panel/correo/regenerar")
def api_regenerar_correo(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    ce = correo_entrante.regenerar_correo(db, ctx.empresa.id)
    return {"ok": True, "direccion": correo_entrante.direccion(ce.codigo)}


@router.get("/panel/correo", response_class=HTMLResponse)
def pagina_panel_correo(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is None:
        return RedirectResponse("/panel/login?siguiente=/panel/correo", status_code=303)
    if not ctx.puede("administrar"):
        return RedirectResponse("/panel", status_code=303)
    from app.api.router_panel import _yo
    return templates.TemplateResponse("panel/correo.html", {
        "request": request, "yo": _yo(ctx, db), "estado": _estado_correo(db, ctx),
    })


# =============================================================
# PÚBLICO: pedir un banco que no está ("Pídelo")
# =============================================================

def _normalizar_nombre(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()[:80]


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip() if xff else (request.client.host if request.client else "?")


@router.post("/api/v1/bancos/solicitud")
def solicitar_banco(
    request: Request,
    payload: dict = Body(...),
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    pais = str(payload.get("pais") or "").strip().upper()[:2]
    nombre = str(payload.get("nombre_banco") or payload.get("nombre") or "").strip()[:80]
    if len(pais) != 2 or len(nombre) < 2:
        raise HTTPException(400, "Indica el país y el nombre del banco o billetera.")
    ip_hash = hash_codigo(_ip(request), "solicitud_banco")
    hace_un_dia = datetime.now(timezone.utc) - timedelta(days=1)
    if db.query(SolicitudBanco).filter(SolicitudBanco.ip_hash == ip_hash,
                                       SolicitudBanco.creado_en >= hace_un_dia).count() >= MAX_SOLICITUDES_BANCO_IP_DIA:
        raise HTTPException(429, "Ya recibimos varios pedidos desde tu conexión hoy. Inténtalo mañana.")
    dispositivo_id = empresa_id = None
    if x_device_token:  # la empresa sale de la credencial, nunca del cuerpo
        try:
            disp = resolver_dispositivo(db, x_device_token)
            dispositivo_id, empresa_id = disp.id, disp.empresa_id
        except HTTPException:
            pass
    db.add(SolicitudBanco(pais=pais, nombre_banco=nombre, nombre_normalizado=_normalizar_nombre(nombre),
                          dispositivo_id=dispositivo_id, empresa_id=empresa_id, ip_hash=ip_hash,
                          creado_en=datetime.now(timezone.utc)))
    db.commit()
    return {"ok": True, "mensaje": "¡Gracias! Anotamos tu pedido. Te avisaremos cuando esté disponible."}


# =============================================================
# ADMINISTRADOR: bancos, solicitudes y correos por revisar
# =============================================================

def _exigir_admin(token: str | None) -> None:
    from app.admin.router import _token_admin_valido
    if not _token_admin_valido(token):
        raise HTTPException(401, "No autorizado")


def _banco_dict(b: BancoConfig) -> dict:
    return {"id": b.id, "pais": b.pais, "clave": b.clave, "nombre": b.nombre, "tipo": b.tipo,
            "activo": bool(b.activo), "orden": b.orden, "paquetes_android": b.paquetes_android or [],
            "dominios_correo": b.dominios_correo or [], "guia_alertas": b.guia_alertas, "color": b.color}


def _lista(valor) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, str):
        valor = re.split(r"[\s,;]+", valor)
    return [str(v).strip() for v in valor if str(v).strip()]


def _validar_banco(datos: dict, parcial: bool) -> dict:
    limpio = {}
    if not parcial or "pais" in datos:
        pais = str(datos.get("pais") or "").strip().upper()
        if len(pais) != 2:
            raise HTTPException(400, "País inválido (código de 2 letras, ej. PE)")
        limpio["pais"] = pais
    if not parcial or "clave" in datos:
        clave = str(datos.get("clave") or "").strip().lower()
        if not _RE_CLAVE.match(clave):
            raise HTTPException(400, "Clave inválida: minúsculas, números y _ (ej. caja_maynas)")
        limpio["clave"] = clave
    if not parcial or "nombre" in datos:
        nombre = str(datos.get("nombre") or "").strip()
        if not 2 <= len(nombre) <= 80:
            raise HTTPException(400, "Nombre inválido")
        limpio["nombre"] = nombre
    if not parcial or "tipo" in datos:
        tipo = str(datos.get("tipo") or "").strip().lower()
        if tipo not in TIPOS_BANCO:
            raise HTTPException(400, f"Tipo inválido: {', '.join(TIPOS_BANCO)}")
        limpio["tipo"] = tipo
    if "paquetes_android" in datos:
        paquetes = _lista(datos["paquetes_android"])
        malos = [p for p in paquetes if not _RE_PAQUETE.match(p)]
        if malos:
            raise HTTPException(400, f"Nombre técnico de app inválido (formato xx.yy.zz): {', '.join(malos)}")
        limpio["paquetes_android"] = paquetes
    if "dominios_correo" in datos:
        dominios = [d.lower().lstrip("@") for d in _lista(datos["dominios_correo"])]
        malos = [d for d in dominios if not _RE_DOMINIO.match(d)]
        if malos:
            raise HTTPException(400, f"Dominio inválido: {', '.join(malos)}")
        limpio["dominios_correo"] = dominios
    if "guia_alertas" in datos:
        limpio["guia_alertas"] = (str(datos["guia_alertas"] or "").strip() or None)
    if "color" in datos:
        color = (str(datos["color"] or "").strip() or None)
        if color and not _RE_COLOR.match(color):
            raise HTTPException(400, "Color inválido (ej. #6d2230)")
        limpio["color"] = color
    if "orden" in datos:
        try:
            limpio["orden"] = int(datos["orden"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Orden inválido")
    if "activo" in datos:
        limpio["activo"] = bool(datos["activo"])
    return limpio


def _invalidar_cache() -> None:
    from app.api.router_vinculacion import limpiar_cache_bancos
    limpiar_cache_bancos()


@router.get("/api/v1/admin/bancos")
def admin_listar_bancos(pais: str = "PE", tipo: str | None = None, estado: str | None = None,
                        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
                        db: Session = Depends(get_db)):
    _exigir_admin(x_admin_token)
    q = db.query(BancoConfig).filter(BancoConfig.pais == pais.upper())
    if tipo:
        q = q.filter(BancoConfig.tipo == tipo)
    if estado in ("activo", "inactivo"):
        q = q.filter(BancoConfig.activo == (estado == "activo"))
    orden = {"billetera": 0, "banco": 1, "caja": 2}
    bancos = sorted(q.all(), key=lambda b: (orden.get(b.tipo, 3), b.orden or 0, b.nombre))
    return {"bancos": [_banco_dict(b) for b in bancos]}


@router.post("/api/v1/admin/bancos")
def admin_crear_banco(payload: dict = Body(...),
                      x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
                      db: Session = Depends(get_db)):
    _exigir_admin(x_admin_token)
    datos = _validar_banco(payload, parcial=False)
    if db.query(BancoConfig).filter(BancoConfig.pais == datos["pais"], BancoConfig.clave == datos["clave"]).first():
        raise HTTPException(409, f"Ya existe {datos['clave']} en {datos['pais']}")
    b = BancoConfig(**{"activo": True, "orden": 0, "paquetes_android": [], "dominios_correo": [], **datos})
    db.add(b)
    db.commit()
    _invalidar_cache()
    return _banco_dict(b)


@router.put("/api/v1/admin/bancos/{banco_id}")
def admin_editar_banco(banco_id: int, payload: dict = Body(...),
                       x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
                       db: Session = Depends(get_db)):
    """Editar o desactivar ({"activo": false}). Nunca se borra."""
    _exigir_admin(x_admin_token)
    b = db.get(BancoConfig, banco_id)
    if b is None:
        raise HTTPException(404, "Banco no encontrado")
    for k, v in _validar_banco(payload, parcial=True).items():
        setattr(b, k, v)
    db.commit()
    _invalidar_cache()
    return _banco_dict(b)


@router.get("/api/v1/admin/bancos/solicitudes")
def admin_solicitudes_banco(pais: str | None = None,
                            x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
                            db: Session = Depends(get_db)):
    _exigir_admin(x_admin_token)
    q = db.query(SolicitudBanco.pais, SolicitudBanco.nombre_normalizado,
                 func.min(SolicitudBanco.nombre_banco), func.count(SolicitudBanco.id),
                 func.max(SolicitudBanco.creado_en))
    if pais:
        q = q.filter(SolicitudBanco.pais == pais.upper())
    filas = q.group_by(SolicitudBanco.pais, SolicitudBanco.nombre_normalizado).all()
    filas.sort(key=lambda f: -f[3])
    return {"solicitudes": [{"pais": p, "nombre": n, "pedidos": c, "ultimo": a_iso_lima(u)}
                            for p, _, n, c, u in filas]}


@router.get("/api/v1/admin/correos")
def admin_correos(estado: str = "sin_formato",
                  x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
                  db: Session = Depends(get_db)):
    """Correos que piden atención: sin formato (agregar formato) o rechazados
    (p. ej. el banco firma con un dominio que aún no está en la lista)."""
    _exigir_admin(x_admin_token)
    if estado not in ("sin_formato", "rechazado", "descartado"):
        raise HTTPException(400, "estado: sin_formato | rechazado | descartado")
    filas = (
        db.query(CorreoRaw, Empresa.slug).outerjoin(Empresa, Empresa.id == CorreoRaw.empresa_id)
        .filter(CorreoRaw.estado == estado).order_by(CorreoRaw.id.desc()).limit(100).all()
    )
    return {"correos": [{
        "id": c.id, "empresa": slug, "fecha": a_iso_lima(c.recibido_en), "remitente": c.remitente,
        "asunto": c.asunto, "dkim_dominios": c.dkim_dominios, "banco": c.banco_clave, "motivo": c.motivo,
        "texto": (c.cuerpo_texto or "")[:1500], "se_puede_reprocesar": bool(c.mime) and c.empresa_id is not None,
    } for c, slug in filas]}


@router.post("/api/v1/admin/correos/{correo_id}/reprocesar")
def admin_reprocesar_correo(correo_id: int,
                            x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
                            db: Session = Depends(get_db)):
    _exigir_admin(x_admin_token)
    c = db.get(CorreoRaw, correo_id)
    if c is None:
        raise HTTPException(404, "Correo no encontrado")
    try:
        correo_entrante.reprocesar_correo(db, c)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"id": c.id, "estado": c.estado, "motivo": c.motivo, "pago_id": c.pago_id}


@router.get("/admin/bancos", response_class=HTMLResponse)
def admin_bancos_html(request: Request, token: str | None = None):
    from app.admin.router import _html_login_admin_empresas, _token_admin_valido
    if not _token_admin_valido(token):
        cuerpo = _html_login_admin_empresas().body.decode("utf-8") \
            .replace("/admin/empresas/nuevas", "/admin/bancos").replace("Empresas nuevas", "Bancos y billeteras")
        return HTMLResponse(cuerpo, status_code=401)
    return templates.TemplateResponse("admin/bancos.html", {"request": request, "token": token,
                                                             "tipos": TIPOS_BANCO})
