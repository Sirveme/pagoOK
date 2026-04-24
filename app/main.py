from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import logging

from app.config import get_settings
from app.routes import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pagook")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"pagoOK arrancando en modo {settings.env}")
    logger.info(f"Backend Gestix: {settings.gestix_api_url}")
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


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse("static/robots.txt", media_type="text/plain")


@app.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    return FileResponse("static/llms.txt", media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")


app.include_router(router)