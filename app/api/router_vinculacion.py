"""Vinculación de celulares a una empresa, catálogo de bancos y ampliación de
la prueba.

  POST /api/v1/dispositivos/vincular   (pública, límite por IP)
  GET  /api/v1/bancos-config?pais=PE   (pública, cache 1 h)
  POST /api/v1/cuenta/ampliar-prueba   (token de dispositivo o sesión de dueño)

El registro antiguo (POST /api/v1/dispositivos/registrar, sin código) sigue
igual en router_device.py y asigna la empresa como hoy (perusistemas).
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.deps_empresa import resolver_dispositivo, sesion_opcional
from app.api.models_multiempresa import BancoConfig, CodigoVinculacion
from app.api.models_webhook import Dispositivo
from app.api.router_device import _ip_cliente, guardar_campos_opcionales
from app.services import cuenta_empresa
from app.services.secretos import hash_codigo, normalizar_codigo_vinculacion

logger = logging.getLogger("pagook")
router = APIRouter(tags=["vinculacion"])

# ---- Límite de intentos FALLIDOS de vinculación: 5 por IP cada 10 min ----
MAX_FALLOS_VINCULAR = 5
VENTANA_FALLOS_SEG = 600
_lock_fallos = threading.Lock()
_fallos: dict[str, list[float]] = {}

MENSAJE_CODIGO_INVALIDO = "Código inválido o vencido. Pide uno nuevo en el panel."


def _fallos_recientes(ip: str) -> int:
    limite = time.time() - VENTANA_FALLOS_SEG
    with _lock_fallos:
        lista = [t for t in _fallos.get(ip, []) if t > limite]
        _fallos[ip] = lista
        return len(lista)


def _registrar_fallo(ip: str) -> None:
    with _lock_fallos:
        _fallos.setdefault(ip, []).append(time.time())


def reiniciar_limite_vinculacion() -> None:
    """Limpia el estado del límite (tests)."""
    with _lock_fallos:
        _fallos.clear()


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def datos_empresa_publicos(empresa: Empresa) -> dict:
    return {
        "razon_social": empresa.razon_social,
        "id_fiscal": empresa.id_fiscal,
        "id_fiscal_tipo": empresa.id_fiscal_tipo or ("RUC" if empresa.pais_codigo == "PE" else None),
        "pais": empresa.pais_codigo,
    }


@router.post("/api/v1/dispositivos/vincular")
async def vincular_dispositivo(request: Request, db: Session = Depends(get_db)):
    ip = _ip_cliente(request)
    if _fallos_recientes(ip) >= MAX_FALLOS_VINCULAR:
        raise HTTPException(429, "Demasiados intentos. Espera 10 minutos.")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(data, dict):
        raise HTTPException(400, "El body debe ser un objeto JSON")

    # Sin consentimiento explícito no se vincula (y no cuenta como fallo).
    if not data.get("consentimiento_version") or not data.get("consentimiento_fecha"):
        raise HTTPException(400, "Falta el consentimiento (consentimiento_version y consentimiento_fecha)")

    codigo = normalizar_codigo_vinculacion(data.get("codigo"))
    ahora = _ahora()
    cv = None
    if len(codigo) == 6:
        cv = (
            db.query(CodigoVinculacion)
            .filter(CodigoVinculacion.codigo_hash == hash_codigo(codigo, "vinculacion"))
            .first()
        )
    if cv is not None:
        expira = cv.expira_en if cv.expira_en.tzinfo else cv.expira_en.replace(tzinfo=timezone.utc)
    if cv is None or cv.usado_en is not None or expira < ahora:
        # 400 genérico: no se revela si era inválido, vencido o ya usado.
        _registrar_fallo(ip)
        raise HTTPException(400, MENSAJE_CODIGO_INVALIDO)

    # Consumo atómico del código: dos celulares con el mismo código -> solo uno gana.
    res = db.execute(
        update(CodigoVinculacion)
        .where(CodigoVinculacion.id == cv.id, CodigoVinculacion.usado_en.is_(None))
        .values(usado_en=ahora)
    )
    if res.rowcount != 1:
        db.rollback()
        _registrar_fallo(ip)
        raise HTTPException(400, MENSAJE_CODIGO_INVALIDO)

    empresa = db.get(Empresa, cv.empresa_id)
    instalacion_id = (str(data.get("instalacion_id") or "")).strip()[:64] or None

    # Misma instalación ya vinculada a esta empresa: se reutiliza el dispositivo.
    disp = None
    if instalacion_id:
        disp = (
            db.query(Dispositivo)
            .filter(Dispositivo.empresa_id == empresa.id, Dispositivo.instalacion_id == instalacion_id,
                    Dispositivo.activo == True)  # noqa: E712
            .first()
        )
    if disp is None:
        manufacturer = str(data.get("manufacturer") or "")[:50] or None
        modelo = str(data.get("modelo") or "")[:80] or None
        disp = Dispositivo(
            empresa_id=empresa.id,
            token="tok_" + secrets.token_urlsafe(24),
            nombre=(f"{manufacturer or ''} {modelo or ''}").strip()[:100] or "Dispositivo",
            modelo=modelo,
            instalacion_id=instalacion_id,
            manufacturer=manufacturer,
            android_version=str(data.get("android_version") or "")[:20] or None,
            app_version=str(data.get("app_version") or "")[:30] or None,
            activo=True,
        )
        db.add(disp)
    guardar_campos_opcionales(disp, data)
    db.flush()
    cv.dispositivo_id = disp.id
    db.commit()
    logger.info(f"Dispositivo vinculado id={disp.id} empresa={empresa.id} por código")
    return {
        "token": disp.token,
        "dispositivo_id": disp.id,
        "empresa": datos_empresa_publicos(empresa),
    }


# =============================================================
# CATÁLOGO DE BANCOS
# =============================================================

_cache_bancos: dict[str, tuple[float, list]] = {}
_lock_cache = threading.Lock()
TTL_CACHE_BANCOS = 3600


def limpiar_cache_bancos() -> None:
    with _lock_cache:
        _cache_bancos.clear()


@router.get("/api/v1/bancos-config")
def bancos_config(pais: str = "PE", db: Session = Depends(get_db)):
    clave = (pais or "").strip().upper()[:2]
    with _lock_cache:
        en_cache = _cache_bancos.get(clave)
    if en_cache and en_cache[0] > time.time():
        bancos = en_cache[1]
    else:
        filas = (
            db.query(BancoConfig)
            .filter(BancoConfig.pais == clave, BancoConfig.activo == True)  # noqa: E712
            .all()
        )
        filas.sort(key=lambda b: (0 if b.tipo == "billetera" else 1, b.orden or 0, b.nombre))
        bancos = [{"clave": b.clave, "nombre": b.nombre, "tipo": b.tipo} for b in filas]
        with _lock_cache:
            _cache_bancos[clave] = (time.time() + TTL_CACHE_BANCOS, bancos)
    # País sin bancos -> lista vacía con 200 (nunca 404).
    return JSONResponse({"bancos": bancos}, headers={"Cache-Control": "public, max-age=3600"})


# =============================================================
# AMPLIAR PRUEBA (+15 días, una vez)
# =============================================================

@router.post("/api/v1/cuenta/ampliar-prueba")
def ampliar_prueba(
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    if x_device_token:
        empresa = db.get(Empresa, resolver_dispositivo(db, x_device_token).empresa_id)
    else:
        ctx = sesion_opcional(request, db)
        if ctx is None:
            raise HTTPException(401, "Se requiere el token del dispositivo o una sesión de dueño")
        if ctx.rol != "dueno":
            raise HTTPException(403, "sin_permiso")
        empresa = ctx.empresa
    try:
        cuenta = cuenta_empresa.ampliar_prueba(db, empresa)
    except cuenta_empresa.CuentaError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "cuenta": cuenta}
