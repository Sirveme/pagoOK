"""Propuestas comerciales publicadas como página web.

Públicas (no aparecen en buscadores: noindex + X-Robots-Tag):
  GET  /propuestas/{codigo}     la propuesta (docs/propuestas/<archivo>.md según indice.yml)

Administrador (ADMIN_TOKEN):
  GET  /admin/propuestas?token=...   aperturas por propuesta y últimas visitas

El texto de la propuesta NO se modifica: se renderiza tal cual con la misma
librería que /privacidad. El encabezado YAML (titulo, cliente, fecha) va a la
cabecera de la página.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.api.models_propuestas import PropuestaVisita
from app.api.router_privacidad import _ip, _md_a_html
from app.services.secretos import hash_codigo

logger = logging.getLogger("pagook")
router = APIRouter(tags=["propuestas"])
templates = Jinja2Templates(directory="templates")

RAIZ_REPO = Path(__file__).resolve().parents[2]
RUTA_PROPUESTAS = RAIZ_REPO / "docs" / "propuestas"
URL_PUBLICA = os.getenv("PAGOOK_URL_PUBLICA", "https://pagook.pro").rstrip("/")
TZ_LIMA = ZoneInfo("America/Lima")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

# Robots que piden la página para armar la tarjeta del enlace (no es una persona leyendo).
_VISTA_PREVIA = [("whatsapp", "WhatsApp"), ("facebookexternalhit", "Facebook"), ("facebot", "Facebook"),
                 ("telegrambot", "Telegram"), ("slackbot", "Slack"), ("linkedinbot", "LinkedIn"),
                 ("twitterbot", "X"), ("skypeuripreview", "Teams/Skype"), ("discordbot", "Discord")]


# =============================================================
# ÍNDICE Y ARCHIVOS
# =============================================================

_cache: dict = {}


def _leer_indice() -> dict[str, str]:
    """{código: archivo}. Un índice ausente o mal escrito = ninguna propuesta publicada."""
    ruta = RUTA_PROPUESTAS / "indice.yml"
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    except OSError:
        logger.error(f"Propuestas: no existe {ruta}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Propuestas: indice.yml mal escrito: {e}")
        return {}
    if not isinstance(datos, dict):
        logger.error("Propuestas: indice.yml debe ser 'código: archivo' por línea")
        return {}
    return {str(k).strip().lower(): str(v).strip() for k, v in datos.items() if k and v}


def _separar_encabezado(fuente: str) -> tuple[dict, str]:
    """(encabezado YAML, cuerpo). Sin encabezado: ({}, fuente)."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", fuente, re.DOTALL)
    if not m:
        return {}, fuente
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        logger.error(f"Propuestas: encabezado YAML inválido: {e}")
        meta = {}
    return (meta if isinstance(meta, dict) else {}), fuente[m.end():]


def _fecha_larga(valor) -> str:
    if isinstance(valor, datetime):
        valor = valor.date()
    if isinstance(valor, date):
        return f"{valor.day} de {MESES[valor.month - 1]} de {valor.year}"
    return str(valor or "")


def _md_a_html_con_saltos(fuente: str) -> str:
    """Igual que /privacidad, pero cada salto del archivo es un <br>: así el membrete
    (Para/De/Asunto) y la firma salen línea por línea, como están escritos."""
    try:
        import markdown
    except ImportError:
        return _md_a_html(fuente)[0]   # texto plano en párrafos, con su propio log
    return markdown.markdown(fuente, extensions=["tables", "sane_lists", "nl2br"])


def _leer_propuesta(archivo: str) -> dict | None:
    """Lee y renderiza la propuesta (se recarga si el archivo cambia). None si no existe."""
    ruta = (RUTA_PROPUESTAS / archivo).resolve()
    if RUTA_PROPUESTAS.resolve() not in ruta.parents:
        logger.error(f"Propuestas: archivo fuera de docs/propuestas: {archivo}")
        return None
    try:
        mtime = ruta.stat().st_mtime
    except OSError:
        logger.error(f"Propuestas: no existe {ruta} (¿se subió el archivo?)")
        return None
    previo = _cache.get(ruta)
    if previo and previo["mtime"] == mtime:
        return previo
    meta, cuerpo = _separar_encabezado(ruta.read_text(encoding="utf-8"))
    html = _md_a_html_con_saltos(cuerpo)
    h1 = re.search(r"^#\s+(.+)$", cuerpo, re.MULTILINE)
    titulo = str(meta.get("titulo") or (h1.group(1).strip() if h1 else "Propuesta comercial"))
    cliente = str(meta.get("cliente") or "")
    datos = {
        "mtime": mtime, "html": html, "titulo": titulo, "cliente": cliente,
        "fecha": _fecha_larga(meta.get("fecha")),
        "descripcion": f"Propuesta de Perú Sistemas Pro para {cliente.rstrip('.')}." if cliente
                       else "Propuesta comercial de Perú Sistemas Pro.",
    }
    _cache[ruta] = datos
    return datos


# =============================================================
# REGISTRO DE VISITAS
# =============================================================

def resumir_user_agent(ua: str | None) -> tuple[str, bool]:
    """(resumen corto, es_vista_previa). Nunca guarda el user-agent completo."""
    ua_l = (ua or "").lower()
    for marca, nombre in _VISTA_PREVIA:
        if marca in ua_l:
            return f"{nombre} (vista previa)", True
    if re.search(r"bot|crawler|spider|preview", ua_l):
        return "Robot", True
    if "android" in ua_l:
        so = "Android"
    elif "iphone" in ua_l or "ipad" in ua_l:
        so = "iPhone" if "iphone" in ua_l else "iPad"
    elif "windows" in ua_l:
        so = "Windows"
    elif "mac os" in ua_l or "macintosh" in ua_l:
        so = "Mac"
    elif "linux" in ua_l:
        so = "Linux"
    else:
        so = "Otro"
    for marca, nombre in (("edg/", "Edge"), ("opr/", "Opera"), ("samsungbrowser", "Samsung"),
                          ("fban", "Facebook"), ("instagram", "Instagram"), ("chrome", "Chrome"),
                          ("crios", "Chrome"), ("firefox", "Firefox"), ("fxios", "Firefox"), ("safari", "Safari")):
        if marca in ua_l:
            return f"{so} · {nombre}", False
    return so, False


def _registrar_visita(db: Session, codigo: str, request: Request) -> None:
    """Best-effort: si la BD falla, la propuesta se muestra igual."""
    resumen, vista_previa = resumir_user_agent(request.headers.get("user-agent"))
    try:
        db.add(PropuestaVisita(codigo=codigo, visto_en=datetime.now(timezone.utc), user_agent=resumen,
                               ip_hash=hash_codigo(_ip(request), "propuesta_visita"),
                               vista_previa=vista_previa))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Propuestas: no se pudo registrar la visita")


# =============================================================
# PÁGINA PÚBLICA
# =============================================================

@router.get("/propuestas/{codigo}", response_class=HTMLResponse)
def ver_propuesta(codigo: str, request: Request, db: Session = Depends(get_db)):
    codigo = codigo.strip().lower()
    archivo = _leer_indice().get(codigo)
    p = _leer_propuesta(archivo) if archivo else None
    if p is None:
        raise HTTPException(404, "Propuesta no encontrada.")
    _registrar_visita(db, codigo, request)
    url = f"{URL_PUBLICA}/propuestas/{codigo}"
    resp = templates.TemplateResponse("propuestas/propuesta.html", {
        "request": request, "p": p, "url": url, "imagen": f"{URL_PUBLICA}/static/img/pagook-icon-512.png",
        "whatsapp": "https://wa.me/?text=" + quote(f"{p['titulo']}\n{url}"),
    })
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# =============================================================
# ADMINISTRADOR
# =============================================================

def _hora_lima(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_LIMA).strftime("%d/%m/%Y %H:%M")


@router.get("/admin/propuestas", response_class=HTMLResponse)
def admin_propuestas(request: Request, token: str | None = None, db: Session = Depends(get_db)):
    from app.admin.router import _html_login_admin_empresas, _token_admin_valido

    if not _token_admin_valido(token):
        respuesta = _html_login_admin_empresas()
        cuerpo = respuesta.body.decode("utf-8").replace("/admin/empresas/nuevas", "/admin/propuestas") \
                                               .replace("Empresas nuevas", "Propuestas")
        return HTMLResponse(cuerpo, status_code=401)

    personas = PropuestaVisita.vista_previa.is_(False)
    previas = PropuestaVisita.vista_previa.is_(True)
    agregados = {
        r.codigo: r for r in db.query(
            PropuestaVisita.codigo,
            func.count().filter(personas).label("aperturas"),
            func.count().filter(previas).label("vistas_previas"),
            func.count(func.distinct(PropuestaVisita.ip_hash)).filter(personas).label("conexiones"),
            func.max(PropuestaVisita.visto_en).filter(personas).label("ultima"),
        ).group_by(PropuestaVisita.codigo).all()
    }
    indice = _leer_indice()
    filas = []
    for codigo in list(indice) + sorted(set(agregados) - set(indice)):
        a = agregados.get(codigo)
        p = _leer_propuesta(indice[codigo]) if codigo in indice else None
        filas.append({
            "codigo": codigo, "publicada": codigo in indice, "titulo": p["titulo"] if p else None,
            "cliente": p["cliente"] if p else None, "archivo": indice.get(codigo),
            "aperturas": a.aperturas if a else 0, "vistas_previas": a.vistas_previas if a else 0,
            "conexiones": a.conexiones if a else 0, "ultima": _hora_lima(a.ultima if a else None),
        })
    recientes = [
        {"codigo": v.codigo, "hora": _hora_lima(v.visto_en), "ua": v.user_agent or "—",
         "conexion": v.ip_hash[:6], "vista_previa": v.vista_previa}
        for v in db.query(PropuestaVisita).order_by(PropuestaVisita.id.desc()).limit(50)
    ]
    return templates.TemplateResponse("admin/propuestas.html", {
        "request": request, "filas": filas, "recientes": recientes, "url_publica": URL_PUBLICA,
    })
