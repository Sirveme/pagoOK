from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from pathlib import Path
import logging

from app.config import get_settings
from app.routes import router as public_router
from app.admin import router as admin_router
from app.admin.deps import NoAutenticado
from app.api import webhook_router, admin_dispositivos_router, api_v1_publica_router
from app.api.router_demo import router as router_demo
from app.api.router_push import router as router_push
from app.api.router_internos import router as router_internos
from app.api.router_device import router as router_device
from app.middleware.security import SecurityScanBlockMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pagook")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"pagoOK arrancando en modo {settings.env}")
    logger.info(f"Backend Gestix: {settings.gestix_api_url}")
    if settings.database_url:
        logger.info(f"BD pagoOK conectada")
    else:
        logger.warning("DATABASE_URL no configurado")
    # Thread de consolidación horaria de estado de dispositivos (best-effort).
    try:
        from app.services.consolidacion_heartbeat import iniciar_consolidacion_background
        iniciar_consolidacion_background()
    except Exception as exc:
        logger.warning(f"No se pudo iniciar la consolidación de heartbeats: {exc}")
    yield
    logger.info("pagoOK apagando")


app = FastAPI(
    title="pagoOK",
    description="Validacion de pagos Yape y Plin en tiempo real",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
)

# Middleware de seguridad: DEBE ir ANTES de cualquier otro middleware para
# cortar los escaneos de rutas maliciosas (backups, wp-admin, .env, dumps...)
# como el punto más externo de la cadena.
app.add_middleware(SecurityScanBlockMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(NoAutenticado)
async def no_autenticado_handler(request: Request, exc: NoAutenticado):
    return RedirectResponse(url="/admin/login", status_code=303)


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse("static/robots.txt", media_type="text/plain")


@app.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    return FileResponse("static/llms.txt", media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")


@app.get("/sw.js", include_in_schema=False)
def serve_service_worker():
    """Sirve el Service Worker desde la raíz con scope amplio."""
    return FileResponse(
        Path("static/js/sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


app.include_router(public_router)
app.include_router(admin_router)
app.include_router(admin_dispositivos_router)
app.include_router(webhook_router)

# Heartbeat de dispositivos + dashboard admin de estado.
# Después de notificaciones y antes del catch-all /{slug} de router_demo.
app.include_router(router_device)

# API publica v1 (consumidores externos del ecosistema, ej. alerta.pe).
# Auth por X-API-Key. Rutas /api/v1/pagos y /api/v1/pagos/{id}/reclamar:
# multi-segmento, NO colisionan con el catch-all /{slug} de router_demo ni
# con los /api/v1/pagos/{buscar,recientes,{id}/consumir} del webhook (X-Device-Token).
app.include_router(api_v1_publica_router)

# router_push y router_internos ANTES de router_demo: sus rutas /{slug}/...
# (receptores, ingresos, egresos, config) deben resolverse antes que el
# catch-all /{slug} de router_demo.
app.include_router(router_push)
app.include_router(router_internos)
app.include_router(router_demo)
