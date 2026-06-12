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
from app.api import webhook_router, admin_dispositivos_router
from app.api.router_demo import router as router_demo
from app.api.router_push import router as router_push

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

# router_push ANTES de router_demo: /recibir y /{slug}/receptores deben
# resolverse antes que el catch-all /{slug} de router_demo
app.include_router(router_push)
app.include_router(router_demo)
