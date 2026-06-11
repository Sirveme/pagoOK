"""Servicio para enviar Web Push Notifications."""
import json
import logging
from datetime import datetime
from typing import Optional

from pywebpush import webpush, WebPushException
from sqlalchemy.orm import Session

from app.admin.models import PushSuscripcion

logger = logging.getLogger(__name__)


def enviar_push_a_empresa(
    db: Session,
    empresa_id: int,
    titulo: str,
    cuerpo: str,
    datos_extra: Optional[dict] = None,
    vapid_public_key: str = "",
    vapid_private_key: str = "",
    vapid_email: str = "info@perusistemas.pro",
) -> dict:
    """Envía push notification a todos los receptores activos de una empresa.

    Returns:
        dict con estadísticas: {enviados, fallidos, total_suscripciones}
    """
    suscripciones = (
        db.query(PushSuscripcion)
        .filter(PushSuscripcion.empresa_id == empresa_id)
        .filter(PushSuscripcion.activo == True)
        .all()
    )

    if not suscripciones:
        return {"enviados": 0, "fallidos": 0, "total_suscripciones": 0}

    if not vapid_private_key:
        logger.warning("VAPID_PRIVATE_KEY no configurado, no se envían push")
        return {"enviados": 0, "fallidos": 0, "total_suscripciones": len(suscripciones)}

    payload = json.dumps({
        "titulo": titulo,
        "cuerpo": cuerpo,
        "datos": datos_extra or {},
        "icon": "/static/img/pagook-icon-192.png",
        "badge": "/static/img/pagook-badge-72.png",
    })

    enviados = 0
    fallidos = 0

    for sus in suscripciones:
        try:
            webpush(
                subscription_info={
                    "endpoint": sus.endpoint,
                    "keys": {
                        "p256dh": sus.p256dh_key,
                        "auth": sus.auth_key,
                    },
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={
                    "sub": f"mailto:{vapid_email}",
                },
                ttl=60,
            )
            sus.ultimo_envio_en = datetime.utcnow()
            sus.envios_exitosos = (sus.envios_exitosos or 0) + 1
            enviados += 1
        except WebPushException as exc:
            logger.error(f"Push falló para suscripción {sus.id}: {exc}")
            sus.envios_fallidos = (sus.envios_fallidos or 0) + 1
            # Si endpoint inválido o 410, desactivar
            if exc.response and exc.response.status_code in (404, 410):
                sus.activo = False
                logger.info(f"Suscripción {sus.id} desactivada (endpoint inválido)")
            fallidos += 1
        except Exception:
            logger.exception(f"Push falló inesperadamente para suscripción {sus.id}")
            sus.envios_fallidos = (sus.envios_fallidos or 0) + 1
            fallidos += 1

    try:
        db.commit()
    except Exception:
        db.rollback()

    return {
        "enviados": enviados,
        "fallidos": fallidos,
        "total_suscripciones": len(suscripciones),
    }


def formatear_push_pago(pago) -> tuple[str, str]:
    """Formatea un objeto PagoDetectado para mostrar en push.

    Returns:
        (titulo, cuerpo) para la notificación
    """
    metodo = (pago.metodo or "Pago").upper()
    monto = pago.monto or 0
    tipo = getattr(pago, "tipo", None) or "ingreso"
    titular = pago.titular or "?"

    if tipo == "ingreso":
        titulo = f"💰 {metodo} recibido S/ {monto:.2f}"
    else:
        titulo = f"📤 {metodo} enviado S/ {monto:.2f}"

    hora = pago.recibido_en.strftime("%H:%M") if pago.recibido_en else ""
    cuerpo = f"{titular} · {hora}".strip(" ·")

    return titulo, cuerpo
