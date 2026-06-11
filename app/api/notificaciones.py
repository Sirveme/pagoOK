"""Endpoints públicos del webhook de notificaciones.

POST /api/v1/notificaciones/inbound   <- celular envía
GET  /api/v1/pagos/buscar              <- PWA Caja consulta
GET  /api/v1/pagos/recientes           <- PWA Caja consulta últimos
POST /api/v1/pagos/{id}/consumir       <- marcar como usado en venta
GET  /api/v1/ping                       <- health check para el celular
"""
from fastapi import APIRouter, Request, Header, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado
from app.api.parser import parsear

logger = logging.getLogger("pagook")
router = APIRouter(prefix="/api/v1", tags=["webhook"])


# =============================================================
# HEALTH CHECK (para que el celular pruebe conectividad)
# =============================================================

@router.get("/ping")
async def ping():
    return {"status": "ok", "servidor": "pagoOK", "hora": datetime.utcnow().isoformat()}


# =============================================================
# INBOUND (celular -> servidor)
# =============================================================

@router.post("/notificaciones/inbound")
async def inbound(
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    """Recibe una notificación del celular del dueño.

    Headers:
      X-Device-Token: tok_xxx  (identifica el dispositivo y por ende la empresa)

    Body JSON:
      {
        "package": "com.bcp.innovacxion.yapeapp",
        "titulo": "Yape!",
        "texto": "Juan Perez te yapeó S/ 15.00",
        "timestamp_celular": 1731600000000,   // opcional
        "sbn_key": "0|com.xxx|123|null|10001" // opcional, dedup
      }
    """
    # 1) Validar token
    if not x_device_token:
        raise HTTPException(401, "Falta header X-Device-Token")

    dispositivo = db.query(Dispositivo).filter(
        Dispositivo.token == x_device_token,
        Dispositivo.activo == True,
    ).first()

    if not dispositivo:
        logger.warning(f"Token inválido o desactivado: {x_device_token[:20]}...")
        raise HTTPException(401, "Token inválido o desactivado")

    # 2) Leer JSON
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(400, f"JSON inválido: {e}")

    package = (data.get("package") or "")[:200]
    titulo = data.get("titulo") or ""
    texto = data.get("texto") or ""
    sbn_key = (data.get("sbn_key") or "")[:200] or None
    ts_celular = data.get("timestamp_celular")

    # 3) Dedup por sbn_key (si la app reintenta y manda el mismo, no duplicamos)
    if sbn_key:
        existente = db.query(NotificacionRaw).filter(
            NotificacionRaw.empresa_id == dispositivo.empresa_id,
            NotificacionRaw.sbn_key == sbn_key,
        ).first()
        if existente:
            logger.info(f"Dedup sbn_key={sbn_key}, ya existía como raw_id={existente.id}")
            return {"status": "duplicado", "raw_id": existente.id}

    # 4) Crear raw
    raw = NotificacionRaw(
        empresa_id=dispositivo.empresa_id,
        dispositivo_id=dispositivo.id,
        package_app=package,
        titulo=titulo[:1000] if titulo else None,
        texto=texto[:5000] if texto else None,
        sbn_key=sbn_key,
        timestamp_celular=ts_celular,
    )
    db.add(raw)
    db.flush()

    # 5) Actualizar dispositivo (ping + contador)
    dispositivo.ultimo_ping = datetime.utcnow()
    dispositivo.total_notificaciones = (dispositivo.total_notificaciones or 0) + 1

    # 6) Parsear
    pago_creado = None
    pago = None
    try:
        parsed = parsear(package, titulo, texto)
        if parsed:
            pago = PagoDetectado(
                empresa_id=dispositivo.empresa_id,
                notificacion_raw_id=raw.id,
                metodo=parsed["metodo"],
                monto=parsed["monto"],
                moneda=parsed.get("moneda", "PEN"),
                titular=parsed.get("titular") or "",
                titular_corto=parsed.get("titular_corto") or "",
                codigo_operacion=parsed.get("codigo_operacion"),
                banco=parsed.get("banco"),
                tipo=parsed.get("tipo", "ingreso"),
            )
            db.add(pago)
            db.flush()
            raw.estado_parseo = "parseada"
            raw.metodo_detectado = parsed["metodo"]
            pago_creado = pago.id
            logger.info(
                f"✓ Parseado: empresa={dispositivo.empresa_id} "
                f"metodo={parsed['metodo']} monto={parsed['monto']} "
                f"titular={parsed.get('titular_corto', '')}"
            )
        else:
            raw.estado_parseo = "ignorada"
            logger.debug(f"Sin match parser: {package} - {texto[:80]}")
    except Exception as e:
        raw.estado_parseo = "fallida"
        raw.error_parseo = str(e)[:500]
        logger.exception(f"Error parseando: {e}")

    db.commit()

    # 7) Enviar Web Push a receptores suscritos (best-effort, post-commit)
    if pago is not None:
        try:
            from app.api.push_service import enviar_push_a_empresa, formatear_push_pago
            from app.admin.db import VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_CLAIM_EMAIL

            slug_empresa = dispositivo.empresa.slug if dispositivo.empresa else ""
            titulo, cuerpo = formatear_push_pago(pago)
            resultado_push = enviar_push_a_empresa(
                db=db,
                empresa_id=pago.empresa_id,
                titulo=titulo,
                cuerpo=cuerpo,
                datos_extra={
                    "pago_id": pago.id,
                    "tipo": getattr(pago, "tipo", "ingreso"),
                    "monto": float(pago.monto) if pago.monto else 0,
                    "metodo": pago.metodo,
                    "titular": pago.titular,
                    "url_destino": f"/{slug_empresa}",
                },
                vapid_public_key=VAPID_PUBLIC_KEY,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_email=VAPID_CLAIM_EMAIL,
            )
            logger.info(f"Push enviado para pago {pago.id}: {resultado_push}")
        except Exception as exc:
            logger.exception(f"Error enviando push para pago {pago_creado}: {exc}")
            # NO relanzar - el pago ya está guardado, el push es best-effort

    return {
        "status": "ok",
        "raw_id": raw.id,
        "pago_id": pago_creado,
        "estado": raw.estado_parseo,
    }


# =============================================================
# BUSCAR PAGOS (PWA Caja consulta)
# =============================================================

@router.get("/pagos/buscar")
async def buscar_pagos(
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    monto: float | None = None,
    metodo: str | None = None,
    minutos: int = 30,
    incluir_consumidos: bool = False,
    db: Session = Depends(get_db),
):
    """La PWA Caja llama acá para verificar si llegó un pago.

    Params:
      monto: filtra por monto exacto (opcional)
      metodo: yape | plin | transferencia (opcional)
      minutos: ventana de búsqueda hacia atrás (default 30)
      incluir_consumidos: si false (default), solo pagos no asignados a venta
    """
    if not x_device_token:
        raise HTTPException(401, "Falta header X-Device-Token")

    dispositivo = db.query(Dispositivo).filter(
        Dispositivo.token == x_device_token,
        Dispositivo.activo == True,
    ).first()
    if not dispositivo:
        raise HTTPException(401, "Token inválido")

    desde = datetime.utcnow() - timedelta(minutes=minutos)
    q = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == dispositivo.empresa_id,
        PagoDetectado.recibido_en >= desde,
    )
    if not incluir_consumidos:
        q = q.filter(PagoDetectado.consumido == False)
    if monto is not None:
        q = q.filter(PagoDetectado.monto == Decimal(str(monto)))
    if metodo:
        q = q.filter(PagoDetectado.metodo == metodo)

    pagos = q.order_by(desc(PagoDetectado.recibido_en)).limit(20).all()

    return {
        "total": len(pagos),
        "pagos": [
            {
                "id": p.id,
                "metodo": p.metodo,
                "monto": float(p.monto),
                "moneda": p.moneda,
                "titular": p.titular,
                "titular_corto": p.titular_corto,
                "codigo_operacion": p.codigo_operacion,
                "banco": p.banco,
                "recibido_en": p.recibido_en.isoformat() if p.recibido_en else None,
                "consumido": p.consumido,
                "hace_segundos": int((datetime.utcnow() - p.recibido_en).total_seconds()) if p.recibido_en else None,
            }
            for p in pagos
        ],
    }


@router.get("/pagos/recientes")
async def pagos_recientes(
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Últimos N pagos detectados (consumidos o no)."""
    if not x_device_token:
        raise HTTPException(401, "Falta header X-Device-Token")
    dispositivo = db.query(Dispositivo).filter(
        Dispositivo.token == x_device_token, Dispositivo.activo == True
    ).first()
    if not dispositivo:
        raise HTTPException(401, "Token inválido")

    pagos = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == dispositivo.empresa_id
    ).order_by(desc(PagoDetectado.recibido_en)).limit(min(limit, 50)).all()

    return {
        "pagos": [
            {
                "id": p.id,
                "metodo": p.metodo,
                "monto": float(p.monto),
                "titular_corto": p.titular_corto,
                "codigo_operacion": p.codigo_operacion,
                "recibido_en": p.recibido_en.isoformat() if p.recibido_en else None,
                "consumido": p.consumido,
            }
            for p in pagos
        ],
    }


@router.post("/pagos/{pago_id}/consumir")
async def consumir_pago(
    pago_id: int,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    """Marca un pago como consumido (asignado a una venta)."""
    if not x_device_token:
        raise HTTPException(401, "Falta header X-Device-Token")
    dispositivo = db.query(Dispositivo).filter(
        Dispositivo.token == x_device_token, Dispositivo.activo == True
    ).first()
    if not dispositivo:
        raise HTTPException(401, "Token inválido")

    pago = db.query(PagoDetectado).filter(
        PagoDetectado.id == pago_id,
        PagoDetectado.empresa_id == dispositivo.empresa_id,
    ).first()
    if not pago:
        raise HTTPException(404, "Pago no encontrado")

    pago.consumido = True
    pago.consumido_en = datetime.utcnow()
    db.commit()
    return {"status": "ok", "id": pago.id}
