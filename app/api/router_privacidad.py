"""Privacidad y eliminación de datos (exigidas por Google Play).

Públicas:
  GET  /privacidad              política (docs/legal/privacidad.md) con índice
  GET  /privacidad.txt          la misma, en texto plano
  GET  /api/v1/privacidad/version  {"version": "2026-10-v1"} (= consentimiento_version de la app)
  GET  /eliminar-cuenta         qué se borra + entrar al panel + formulario sin acceso
  POST /eliminar-cuenta/solicitud   formulario (5 por IP al día)

Dueño con sesión:
  GET  /panel/eliminar
  GET  /api/v1/panel/exportar
  POST /api/v1/panel/eliminar   {frase, descarga: "descargado" | "no_necesito"}

Administrador (ADMIN_TOKEN):
  GET  /admin/eliminaciones?token=...
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.deps_empresa import SesionCtx, borrar_cookie, requiere, sesion_opcional
from app.api.models_privacidad import SolicitudEliminacion
from app.services import eliminacion
from app.services.secretos import hash_codigo

logger = logging.getLogger("pagook")
router = APIRouter(tags=["privacidad"])
templates = Jinja2Templates(directory="templates")

RUTA_POLITICA = Path("docs/legal/privacidad.md")
MAX_SOLICITUDES_IP_DIA = 5
MENSAJE_SOLICITUD = ("Recibimos tu solicitud. Te contactaremos en un máximo de 15 días para confirmar "
                     "tu identidad y eliminar tus datos.")
PAISES_FORM = [("PE", "Perú"), ("BO", "Bolivia"), ("CO", "Colombia"), ("EC", "Ecuador"), ("MX", "México"),
               ("CL", "Chile"), ("AR", "Argentina"), ("ES", "España"), ("VE", "Venezuela")]


# =============================================================
# POLÍTICA DE PRIVACIDAD
# =============================================================

_cache_politica: dict = {}


def _leer_politica() -> dict:
    """Lee y renderiza la política una vez (se recarga si el archivo cambia).
    El texto NO se modifica: es el de docs/legal/privacidad.md tal cual."""
    import markdown

    mtime = RUTA_POLITICA.stat().st_mtime
    if _cache_politica.get("mtime") == mtime:
        return _cache_politica
    fuente = RUTA_POLITICA.read_text(encoding="utf-8")
    m = re.search(r"Versi[oó]n\s+(\d{4}-\d{2}-v\d+)", fuente)
    md = markdown.Markdown(extensions=["tables", "sane_lists", "toc"],
                           extension_configs={"toc": {"toc_depth": "2-3", "permalink": False}})
    html = md.convert(fuente)
    titulo = re.search(r"^#\s+(.+)$", fuente, re.MULTILINE)
    _cache_politica.update({
        "mtime": mtime, "fuente": fuente, "html": html, "toc": md.toc,
        "version": m.group(1) if m else None,
        "titulo": titulo.group(1).strip() if titulo else "Política de Privacidad",
    })
    return _cache_politica


def version_politica() -> str | None:
    return _leer_politica()["version"]


def _a_texto_plano(fuente: str) -> str:
    """Markdown -> texto legible: sin **, sin #, tablas como 'a | b'."""
    lineas = []
    for linea in fuente.splitlines():
        if re.fullmatch(r"\s*\|?\s*:?-{3,}.*", linea):
            continue  # separador de tabla
        if linea.startswith("|"):
            linea = " | ".join(c.strip() for c in linea.strip().strip("|").split("|"))
        m = re.match(r"^(#+)\s+(.*)$", linea)
        if m:
            linea = m.group(2).upper() if len(m.group(1)) <= 2 else m.group(2)
        linea = re.sub(r"\*\*(.+?)\*\*", r"\1", linea)
        linea = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", linea)
        linea = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", linea)
        lineas.append(linea)
    return "\n".join(lineas).strip() + "\n"


@router.get("/privacidad", response_class=HTMLResponse)
def pagina_privacidad(request: Request):
    p = _leer_politica()
    return templates.TemplateResponse("legal/privacidad.html", {
        "request": request, "contenido": p["html"], "indice": p["toc"], "version": p["version"],
        "titulo": p["titulo"],
    })


@router.get("/privacidad.txt", response_class=PlainTextResponse)
def privacidad_txt():
    return PlainTextResponse(_a_texto_plano(_leer_politica()["fuente"]))


@router.get("/api/v1/privacidad/version")
def api_version_privacidad():
    return {"version": version_politica()}


# =============================================================
# ELIMINAR CUENTA: página pública + formulario sin acceso
# =============================================================

@router.get("/eliminar-cuenta", response_class=HTMLResponse)
def pagina_eliminar_cuenta(request: Request):
    return templates.TemplateResponse("legal/eliminar_cuenta.html", {
        "request": request, "paises": PAISES_FORM, "version": version_politica(),
    })


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip() if xff else (request.client.host if request.client else "?")


@router.post("/eliminar-cuenta/solicitud")
def crear_solicitud(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    pais = str(payload.get("pais") or "").strip().upper()[:2]
    id_fiscal = re.sub(r"\s", "", str(payload.get("id_fiscal") or ""))[:20]
    nombre = str(payload.get("nombre") or "").strip()[:150]
    contacto = str(payload.get("contacto") or "").strip()[:150]
    motivo = str(payload.get("motivo") or "").strip()[:2000] or None
    if pais not in {p for p, _ in PAISES_FORM} or not id_fiscal or not nombre or not contacto:
        raise HTTPException(400, "Completa país, identificador fiscal, nombre y un celular o correo de contacto.")

    ip_hash = hash_codigo(_ip(request), "solicitud_eliminacion")
    hace_un_dia = datetime.now(timezone.utc) - timedelta(days=1)
    recientes = (
        db.query(SolicitudEliminacion)
        .filter(SolicitudEliminacion.ip_hash == ip_hash, SolicitudEliminacion.creado_en >= hace_un_dia)
        .count()
    )
    if recientes >= MAX_SOLICITUDES_IP_DIA:
        raise HTTPException(429, "Ya recibimos varias solicitudes desde tu conexión hoy. Inténtalo mañana.")
    db.add(SolicitudEliminacion(pais=pais, id_fiscal=id_fiscal, nombre=nombre, contacto=contacto,
                                motivo=motivo, ip_hash=ip_hash, estado="pendiente",
                                creado_en=datetime.now(timezone.utc)))
    db.commit()
    logger.info("Solicitud de eliminación recibida (formulario público)")
    return {"ok": True, "mensaje": MENSAJE_SOLICITUD}


# =============================================================
# AUTOSERVICIO DEL DUEÑO
# =============================================================

@router.get("/panel/eliminar", response_class=HTMLResponse)
def pagina_panel_eliminar(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is None:
        return RedirectResponse("/panel/login?siguiente=/panel/eliminar", status_code=303)
    if not ctx.puede("administrar"):
        return RedirectResponse("/panel", status_code=303)
    from app.api.router_panel import _yo
    return templates.TemplateResponse("panel/eliminar.html", {
        "request": request, "yo": _yo(ctx, db), "frase": eliminacion.FRASE_CONFIRMACION,
        "permitida": eliminacion.eliminacion_permitida(ctx.empresa),
    })


@router.get("/api/v1/panel/exportar")
def exportar_datos(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    contenido = eliminacion.exportar_zip(db, ctx.empresa)
    eliminacion.marcar_descarga(ctx.empresa.id)
    nombre = f"pagook_{ctx.empresa.slug}_{datetime.now(timezone.utc):%Y%m%d}.zip"
    return Response(contenido, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.post("/api/v1/panel/eliminar")
def eliminar_mis_datos(
    payload: dict = Body(...),
    ctx: SesionCtx = Depends(requiere("administrar")),
    db: Session = Depends(get_db),
):
    if not eliminacion.eliminacion_permitida(ctx.empresa):
        raise HTTPException(403, "Esta empresa no se puede eliminar.")
    if payload.get("frase") != eliminacion.FRASE_CONFIRMACION:
        raise HTTPException(400, "Escribe la frase exactamente como aparece para confirmar.")
    descarga = payload.get("descarga")
    if descarga == "descargado":
        if not eliminacion.descargo_recientemente(ctx.empresa.id):
            raise HTTPException(400, "Primero descarga tus datos, o marca «No necesito descargar mis datos».")
    elif descarga != "no_necesito":
        raise HTTPException(400, "Descarga tus datos o marca «No necesito descargar mis datos».")

    empresa = ctx.empresa
    try:
        conteos = eliminacion.eliminar_empresa(db, empresa, "autoservicio")
        db.commit()
    except eliminacion.EliminacionProhibida:
        db.rollback()
        raise HTTPException(403, "Esta empresa no se puede eliminar.")
    except Exception:
        db.rollback()
        logger.exception("Error eliminando una empresa (autoservicio); no se borró nada")
        raise HTTPException(500, "No se pudo completar la eliminación. No se borró nada; inténtalo de nuevo.")
    logger.info(f"Empresa eliminada por autoservicio: {sum(conteos.values())} filas")
    resp = JSONResponse({"ok": True, "mensaje": "Tus datos fueron eliminados.", "redirigir": "/eliminar-cuenta?ok=1"})
    borrar_cookie(resp)
    return resp


# =============================================================
# ADMINISTRADOR: solicitudes recibidas
# =============================================================

@router.get("/admin/eliminaciones", response_class=HTMLResponse)
def admin_eliminaciones(request: Request, token: str | None = None, db: Session = Depends(get_db)):
    from app.admin.router import _html_login_admin_empresas, _token_admin_valido

    if not _token_admin_valido(token):
        respuesta = _html_login_admin_empresas()
        cuerpo = respuesta.body.decode("utf-8").replace("/admin/empresas/nuevas", "/admin/eliminaciones") \
                                               .replace("Empresas nuevas", "Solicitudes de eliminación")
        return HTMLResponse(cuerpo, status_code=401)
    solicitudes = db.query(SolicitudEliminacion).order_by(SolicitudEliminacion.id.desc()).limit(200).all()
    filas = []
    for s in solicitudes:
        emp = (
            db.query(Empresa).filter(Empresa.pais_codigo == s.pais, Empresa.id_fiscal == s.id_fiscal).first()
            if s.estado == "pendiente" else None
        )
        filas.append({"s": s, "empresa": emp,
                      "protegida": bool(emp) and not eliminacion.eliminacion_permitida(emp)})
    return templates.TemplateResponse("admin/eliminaciones.html", {"request": request, "filas": filas})
