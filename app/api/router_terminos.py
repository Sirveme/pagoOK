"""Condiciones del Servicio (página pública, exigida junto con la privacidad).

Públicas:
  GET  /terminos        Condiciones del Servicio (docs/legal/terminos.md) con índice
  GET  /terminos.txt    las mismas, en texto plano

Mismo diseño y librería que /privacidad. El texto NO se modifica aquí: es el de
docs/legal/terminos.md tal cual; la versión se lee del propio archivo. Si el
archivo no existe, la página responde de forma controlada (503), nunca 500.

La página es pública, con SSL, accesible desde cualquier país y SÍ debe
indexarse: no lleva noindex.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

# Reusamos el renderizador y el paso a texto plano de la política: mismo diseño.
from app.api.router_privacidad import _a_texto_plano, _md_a_html

logger = logging.getLogger("pagook")
router = APIRouter(tags=["terminos"])
templates = Jinja2Templates(directory="templates")

# Relativa al repo, no al directorio de trabajo del proceso (Railway puede arrancar en otro).
RAIZ_REPO = Path(__file__).resolve().parents[2]
RUTA_TERMINOS = RAIZ_REPO / "docs" / "legal" / "terminos.md"

_cache_terminos: dict = {}


class TerminosNoDisponibles(Exception):
    pass


def _separar_front_matter(texto: str) -> tuple[dict, str]:
    """Separa un bloque YAML inicial (--- ... ---) de los metadatos del cuerpo.

    El cuerpo se devuelve SIN el front matter para que el renderizador no lo
    convierta en un encabezado basura ni lo arrastre al índice/texto plano. No
    se modifica el texto del documento: solo se aparta su cabecera de metadatos.
    """
    meta: dict = {}
    m = re.match(r"^﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", texto, re.DOTALL)
    if not m:
        return meta, texto
    for linea in m.group(1).splitlines():
        clave, sep, valor = linea.partition(":")
        if sep and clave.strip():
            meta[clave.strip().lower()] = valor.strip()
    return meta, texto[m.end():]


def _leer_terminos() -> dict:
    """Lee y renderiza las condiciones una vez (se recarga si el archivo cambia).
    El texto NO se modifica: es el de docs/legal/terminos.md tal cual (apartando
    solo el front matter YAML de la cabecera, si lo tiene)."""
    try:
        mtime = RUTA_TERMINOS.stat().st_mtime
    except OSError:
        logger.error(f"Términos: no existe {RUTA_TERMINOS} (¿se subió docs/legal/terminos.md?)")
        raise TerminosNoDisponibles()
    if _cache_terminos.get("mtime") == mtime:
        return _cache_terminos
    raw = RUTA_TERMINOS.read_text(encoding="utf-8")
    meta, cuerpo = _separar_front_matter(raw)
    # Versión: primero del front matter (version:), si no, de la línea *Versión ...* del cuerpo.
    m = re.search(r"Versi[oó]n\s+(\d{4}-\d{2}-v\d+)", cuerpo)
    version = meta.get("version") or (m.group(1) if m else None)
    # Título: del front matter (titulo:) o del primer encabezado H1 del cuerpo.
    h1 = re.search(r"^#\s+(.+)$", cuerpo, re.MULTILINE)
    titulo = meta.get("titulo") or (h1.group(1).strip() if h1 else "Condiciones del Servicio")
    html, toc = _md_a_html(cuerpo)
    _cache_terminos.update({
        "mtime": mtime, "fuente": cuerpo, "html": html, "toc": toc,
        "version": version, "titulo": titulo,
    })
    return _cache_terminos


def version_terminos() -> str | None:
    try:
        return _leer_terminos()["version"]
    except TerminosNoDisponibles:
        return None


def _terminos_o_503() -> dict:
    try:
        return _leer_terminos()
    except TerminosNoDisponibles:
        raise HTTPException(503, "Las Condiciones del Servicio no están disponibles en este momento.")


@router.get("/terminos", response_class=HTMLResponse)
def pagina_terminos(request: Request):
    t = _terminos_o_503()
    return templates.TemplateResponse("legal/terminos.html", {
        "request": request, "contenido": t["html"], "indice": t["toc"], "version": t["version"],
        "titulo": t["titulo"],
    })


@router.get("/terminos.txt", response_class=PlainTextResponse)
def terminos_txt():
    return PlainTextResponse(_a_texto_plano(_terminos_o_503()["fuente"]))
