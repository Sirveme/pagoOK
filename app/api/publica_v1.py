"""API pública v1 de PagoOK — consulta y reclamo de pagos detectados.

Contrato (agnóstico al lenguaje del consumidor, estilo facturalo.pro):

  GET  /api/v1/pagos?monto=&desde=&hasta=     (auth: X-API-Key)
  POST /api/v1/pagos/{id}/reclamar            (auth: X-API-Key)

ZONA ADITIVA: NO toca la ingesta Android (/api/v1/notificaciones/inbound),
ni el parser, ni la auth por X-Device-Token. Reusa el modelo `pago_detectado`
(ya aislado por empresa_id) y la columna aditiva `reclamado_por`.

Aislamiento: una CuentaApi solo ve/reclama pagos de SU empresa.
Errores: JSON consistente {"detail": "..."} (formato nativo de HTTPException).
"""
import threading
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.admin.db import get_db
from app.api.models_cuenta import CuentaApi, hash_api_key
from app.api.models_webhook import PagoDetectado

router = APIRouter(prefix="/api/v1", tags=["api-v1-publica"])


# =============================================================
# RATE LIMIT — por API Key (ventana fija de 1 minuto, en memoria)
# =============================================================
# Limite simple y liviano para evitar abuso de terceros. Es por-proceso
# (in-memory): con varios workers el limite efectivo se multiplica por worker,
# suficiente como primera barrera. Si se necesita exacto/distribuido, migrar
# a Redis sin cambiar el contrato (mismo 429).
RATE_LIMIT_POR_MINUTO = 120

_rate_lock = threading.Lock()
# cuenta_id -> (minuto_epoch, conteo_en_ese_minuto)
_rate_buckets: dict[int, tuple[int, int]] = {}


def _check_rate_limit(cuenta_id: int) -> None:
    """429 si la cuenta supera RATE_LIMIT_POR_MINUTO solicitudes en el minuto."""
    minuto = int(time.time() // 60)
    with _rate_lock:
        ventana, conteo = _rate_buckets.get(cuenta_id, (minuto, 0))
        if ventana != minuto:
            ventana, conteo = minuto, 0
        conteo += 1
        _rate_buckets[cuenta_id] = (ventana, conteo)
    if conteo > RATE_LIMIT_POR_MINUTO:
        raise HTTPException(
            status_code=429,
            detail="Limite de solicitudes excedido. Reintenta en un minuto.",
        )


# =============================================================
# AUTH — X-API-Key  ->  CuentaApi
# =============================================================

def cuenta_actual(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> CuentaApi:
    """Resuelve la credencial. 401 si falta o es inválida/inactiva."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Falta header X-API-Key")
    cuenta = (
        db.query(CuentaApi)
        .filter(
            CuentaApi.api_key_hash == hash_api_key(x_api_key),
            CuentaApi.activa == True,  # noqa: E712
        )
        .first()
    )
    if not cuenta:
        raise HTTPException(status_code=401, detail="API key inválida")
    # Rate limit por credencial (despues de autenticar: no gastamos cupo en 401).
    _check_rate_limit(cuenta.id)
    return cuenta


# =============================================================
# Helpers de serialización / parseo
# =============================================================

def _banco_respuesta(p: PagoDetectado) -> str | None:
    """Mapea al campo `banco` del contrato: yape|plin|<banco>|null.

    En el modelo `metodo` guarda yape/plin/transferencia y la columna `banco`
    guarda el banco (BCP, BBVA...) solo en transferencias.
    """
    if p.metodo in ("yape", "plin"):
        return p.metodo
    return p.banco or p.metodo or None


def _serializar_pago(p: PagoDetectado) -> dict:
    return {
        "id": str(p.id),
        "nombre": p.titular or None,
        "monto": float(p.monto),
        "hora": p.recibido_en.isoformat() if p.recibido_en else None,
        "banco": _banco_respuesta(p),
    }


def _parse_iso(valor: str, campo: str) -> datetime:
    """Parsea ISO-8601 tolerando el sufijo 'Z'. 400 si es inválido."""
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Parámetro '{campo}' no es una fecha ISO-8601 válida",
        )


def _parse_monto(valor: str) -> Decimal:
    """Convierte el monto a Decimal exacto (nunca float). 400 si es inválido."""
    try:
        return Decimal(valor.strip())
    except (InvalidOperation, AttributeError):
        raise HTTPException(
            status_code=400, detail="Parámetro 'monto' no es un decimal válido"
        )


# =============================================================
# GET /api/v1/pagos
# =============================================================

@router.get("/pagos")
def listar_pagos(
    monto: str | None = Query(default=None, description="Monto decimal exacto"),
    desde: str | None = Query(default=None, description="ISO-8601 inicio (>=)"),
    hasta: str | None = Query(default=None, description="ISO-8601 fin (<=)"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximo de resultados (1-200)"),
    offset: int = Query(default=0, ge=0, description="Desplazamiento para paginar"),
    cuenta: CuentaApi = Depends(cuenta_actual),
    db: Session = Depends(get_db),
):
    """Lista los pagos de la cuenta: no reclamados, o reclamados por ella misma.

    Paginacion: `limit` (1-200, default 50) y `offset` (default 0), ordenado por
    `recibido_en` descendente. Para recorrer todo: subir `offset` hasta recibir
    menos de `limit` resultados.
    """
    q = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == cuenta.empresa_id,
        (PagoDetectado.reclamado_por.is_(None))
        | (PagoDetectado.reclamado_por == cuenta.id),
    )

    if monto is not None:
        q = q.filter(PagoDetectado.monto == _parse_monto(monto))
    if desde is not None:
        q = q.filter(PagoDetectado.recibido_en >= _parse_iso(desde, "desde"))
    if hasta is not None:
        q = q.filter(PagoDetectado.recibido_en <= _parse_iso(hasta, "hasta"))

    pagos = (
        q.order_by(PagoDetectado.recibido_en.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "pagos": [_serializar_pago(p) for p in pagos],
        "limit": limit,
        "offset": offset,
        "count": len(pagos),
    }


# =============================================================
# POST /api/v1/pagos/{id}/reclamar
# =============================================================

@router.post("/pagos/{pago_id}/reclamar")
def reclamar_pago(
    pago_id: str = Path(..., description="ID del pago a reclamar"),
    cuenta: CuentaApi = Depends(cuenta_actual),
    db: Session = Depends(get_db),
):
    """Reclama un pago de forma atómica e idempotente para el mismo reclamante.

    200 -> reclamado OK (o ya reclamado por esta misma cuenta: idempotente)
    409 -> ya reclamado por OTRA cuenta
    404 -> no existe o no pertenece a la cuenta
    """
    try:
        pid = int(pago_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    # Reclamo atómico: solo gana si reclamado_por seguía NULL.
    # Dos consumidores simultáneos -> exactamente uno hace rowcount == 1.
    res = db.execute(
        update(PagoDetectado)
        .where(
            PagoDetectado.id == pid,
            PagoDetectado.empresa_id == cuenta.empresa_id,
            PagoDetectado.reclamado_por.is_(None),
        )
        .values(reclamado_por=cuenta.id, reclamado_en=func.now())
    )

    if res.rowcount == 1:
        db.commit()
        return {"id": str(pid), "reclamado": True, "reclamado_por": cuenta.id}

    db.rollback()

    # No ganó el UPDATE: desambiguar 404 / 409 / idempotente.
    pago = (
        db.query(PagoDetectado)
        .filter(
            PagoDetectado.id == pid,
            PagoDetectado.empresa_id == cuenta.empresa_id,
        )
        .first()
    )
    if pago is None:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    if pago.reclamado_por == cuenta.id:
        # Idempotente: el mismo reclamante repite -> OK.
        return {"id": str(pid), "reclamado": True, "reclamado_por": cuenta.id}
    # Reclamado por otra cuenta.
    raise HTTPException(status_code=409, detail="Pago ya reclamado por otra cuenta")
