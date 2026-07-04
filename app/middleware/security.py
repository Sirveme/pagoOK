"""Middleware de seguridad: bloqueo de escaneos de rutas maliciosas.

Los bots escanean el servidor buscando backups expuestos, paneles de WordPress,
credenciales (.env/.git) y utilidades de administración (phpmyadmin, adminer...).
Este middleware corta esos intentos con 403 Forbidden ANTES de que lleguen al
router, los registra en un logger dedicado y aplica un rate limit simple por IP.

Reglas de diseño:
  - Solo se inspecciona el PATH de la URL, NUNCA los query params. Así una ruta
    legítima con `?filename=backup.pdf` (ej. Facturalo) jamás se bloquea.
  - Sin dependencias externas: solo stdlib + Starlette (ya presente vía FastAPI).
  - Estado en memoria (por proceso). Al reiniciar el servicio se pierde; es
    aceptable para MVP.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("security.scan_attempts")


# =============================================================
# PATRONES DE BLOQUEO
# =============================================================
# Prefijos de path maliciosos. El emparejamiento respeta límites de segmento
# (ver `_coincide_prefijo`) para NO capturar rutas legítimas que apenas empiecen
# con el mismo texto (ej. "/pma" NO debe bloquear un hipotético "/pmarket").
PATRONES_PREFIJO: tuple[str, ...] = (
    # Backups expuestos
    "/backups/",
    # Paneles/archivos de WordPress (somos FastAPI, no WordPress)
    "/wp-admin",
    "/wp-login",
    "/wp-includes",
    "/wp-content",
    # Credenciales / metadatos de repos y nube
    "/.env",
    "/.git",
    "/.aws",
    "/.ssh",
    # Utilidades de administración de bases de datos / webshells
    "/phpmyadmin",
    "/pma",
    "/adminer",
    "/admin.php",
    "/config.php",
    "/shell.php",
)

# Extensiones de archivo típicas de dumps/backups comprimidos. Ninguna ruta
# legítima de PagoOK/ecosistema termina en estas extensiones, así que se
# bloquean por sufijo del path (los query params quedan fuera del análisis).
EXTENSIONES_SOSPECHOSAS: tuple[str, ...] = (
    ".sql",
    ".sql.gz",
    ".sql.bz2",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tar.xz",
    ".7z",
    ".rar",
    ".bz2",
    ".zst",
    ".zip",
)


# =============================================================
# RATE LIMIT POR IP (en memoria)
# =============================================================
VENTANA_SEGUNDOS = 60      # ventana para contar intentos
MAX_INTENTOS = 30          # más de esto en la ventana -> baneo
BAN_SEGUNDOS = 600         # 10 minutos de baneo

_lock = threading.Lock()
# ip -> lista de timestamps (epoch) de intentos bloqueados dentro de la ventana
_intentos: dict[str, list[float]] = {}
# ip -> timestamp (epoch) en que EXPIRA el baneo
_baneados: dict[str, float] = {}


# =============================================================
# HELPERS
# =============================================================

def _coincide_prefijo(path: str, patron: str) -> bool:
    """True si `path` coincide con `patron` respetando límites de segmento.

    - Si el patrón termina en '/', basta con `startswith` (es un directorio).
    - Si no, el carácter siguiente en el path debe ser un límite ('/', '.', '?')
      o el fin del string, para no capturar prefijos parciales de otra palabra.
    """
    if patron.endswith("/"):
        return path.startswith(patron)
    if path == patron:
        return True
    if path.startswith(patron):
        siguiente = path[len(patron):][0]
        return siguiente in "/.?"
    return False


def _es_ruta_maliciosa(path: str) -> bool:
    """Decide si el path (ya en minúsculas) corresponde a un escaneo conocido."""
    for patron in PATRONES_PREFIJO:
        if _coincide_prefijo(path, patron):
            return True
    for ext in EXTENSIONES_SOSPECHOSAS:
        if path.endswith(ext):
            return True
    return False


def _obtener_ip(request: Request) -> str:
    """IP del cliente. Detrás del proxy de Railway usa X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # El primer valor es el cliente original.
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def _esta_baneado(ip: str, ahora: float) -> bool:
    """True si la IP tiene un baneo vigente. Limpia el baneo si ya expiró."""
    with _lock:
        expira = _baneados.get(ip)
        if expira is None:
            return False
        if ahora >= expira:
            del _baneados[ip]
            _intentos.pop(ip, None)
            return False
        return True


def _registrar_intento(ip: str, ahora: float) -> bool:
    """Registra un intento bloqueado. Devuelve True si con esto la IP quedó baneada."""
    with _lock:
        marcas = [t for t in _intentos.get(ip, []) if ahora - t < VENTANA_SEGUNDOS]
        marcas.append(ahora)
        _intentos[ip] = marcas
        if len(marcas) > MAX_INTENTOS:
            _baneados[ip] = ahora + BAN_SEGUNDOS
            return True
        return False


def _log_intento(ip: str, path: str, user_agent: str, baneado: bool) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    logger.warning(
        "Escaneo bloqueado ip=%s path=%s user_agent=%r timestamp=%s baneado=%s",
        ip, path, user_agent or "-", ts, baneado,
    )


def _respuesta_403() -> JSONResponse:
    return JSONResponse({"detail": "Forbidden"}, status_code=403)


def _respuesta_429() -> JSONResponse:
    return JSONResponse(
        {"detail": "Too Many Requests"},
        status_code=429,
        headers={"Retry-After": str(BAN_SEGUNDOS)},
    )


# =============================================================
# MIDDLEWARE
# =============================================================

class SecurityScanBlockMiddleware(BaseHTTPMiddleware):
    """Bloquea escaneos de rutas maliciosas y limita por IP.

    Debe registrarse como el middleware más externo (primero en app/main.py)
    para cortar el tráfico de escaneo antes de cualquier otro procesamiento.
    """

    async def dispatch(self, request: Request, call_next):
        ahora = time.time()
        ip = _obtener_ip(request)

        # 1) IP con baneo vigente -> 429 para CUALQUIER petición (durante 10 min).
        if _esta_baneado(ip, ahora):
            return _respuesta_429()

        # 2) ¿El path es un escaneo conocido? (solo path, nunca query params)
        path = request.url.path.lower()
        if _es_ruta_maliciosa(path):
            baneado = _registrar_intento(ip, ahora)
            _log_intento(ip, request.url.path, request.headers.get("user-agent", ""), baneado)
            # Si este intento cruzó el umbral, ya devolvemos 429; si no, 403.
            return _respuesta_429() if baneado else _respuesta_403()

        # 3) Ruta legítima -> sigue el flujo normal.
        return await call_next(request)
