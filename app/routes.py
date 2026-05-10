from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
import httpx
import logging

from app.config import get_settings

logger = logging.getLogger("pagook")
settings = get_settings()
templates = Jinja2Templates(directory="templates")

router = APIRouter()


async def detectar_pais_por_ip(ip: str) -> str:
    """Devuelve codigo ISO de 2 letras. Default PE para IPs privadas/locales."""
    if not ip or ip.startswith(("127.", "192.168.", "10.", "172.")):
        return "PE"
    return "PE"


async def obtener_precio_pais(codigo_pais: str) -> dict:
    """Consulta Gestix para precio localizado. Fallback a PE si falla."""
    fallback = {
        "codigo": "PE",
        "pais": "PE",
        "pais_nombre": "Peru",
        "bandera": "PE",
        "moneda": "PEN",
        "simbolo": "S/",
        "precio": "5",
        "precio_minimo": "10",
        "minimo_celulares": 2,
        "billeteras": ["Yape", "Plin"],
        "activo": True,
    }
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.api_backend_server}/api/v1/precios/pais/{codigo_pais}")
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"No se pudo obtener precio {codigo_pais}: {e}")
    return fallback


async def obtener_tabla_precios() -> list:
    """Tabla comparativa de precios. Fallback a [] si falla."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.api_backend_server}/api/v1/precios/tabla")
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"No se pudo obtener tabla precios: {e}")
    return []


def contexto_base(request: Request) -> dict:
    return {
        "request": request,
        "whatsapp_link": settings.whatsapp_link,
        "whatsapp_mensaje": "Hola, quiero info sobre pagoOK",
        "email_contacto": settings.email_contacto,
        "api_base": settings.api_backend_browser,
    }


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    ip = request.client.host if request.client else ""
    pais_codigo = await detectar_pais_por_ip(ip)
    precio = await obtener_precio_pais(pais_codigo)
    tabla_precios = await obtener_tabla_precios()
    
    contexto = contexto_base(request)
    contexto.update({
        "pais_detectado": pais_codigo,
        "precio": precio,
        "tabla_precios": tabla_precios,
    })
    return templates.TemplateResponse("landing.html", contexto)


@router.get("/videos", response_class=HTMLResponse)
async def videos(request: Request):
    return templates.TemplateResponse("videos.html", contexto_base(request))


@router.get("/qr/{codigo}")
async def proxy_qr(codigo: str, request: Request):
    """Proxy al tracking de Gestix."""
    return RedirectResponse(
        url=f"{settings.api_backend_server}/qr/{codigo}",
        status_code=302
    )


@router.get("/caja/{codigo}")
async def caja_vendedor(codigo: str):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/static/caja/index.html?codigo={codigo}", status_code=302)


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "env": settings.env}