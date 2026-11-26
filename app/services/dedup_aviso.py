"""Dedupe de avisos del celular (/notificaciones/inbound y /device/no-reconocido).

Incidente 26-nov (equipo #20): Interbank publica algunos avisos con id=0 y
tag=null, así que la clave de Android (`sbn_key`) sale IDÉNTICA en avisos
distintos. Dedupear solo por empresa + sbn_key descartaba pagos nuevos como
"duplicado" (200 OK: la app no reintenta) y se perdían en silencio.

Regla (zWeb-16):
  - Sin sbn_key: nunca es duplicado.
  - Sin `timestamp_celular` en el aviso entrante: regla anterior (basta la clave).
  - Con hora: es duplicado si un aviso previo con la misma clave tiene
      a) la MISMA hora del celular (reintento de la app), o
      b) el MISMO texto y una hora a menos de 60 s (el banco actualizó el aviso).
    Si la clave coincide pero ninguna fila cumple, se guarda como aviso NUEVO
    y se deja un WARNING (sin texto) para que el choque de claves no sea silencioso.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.api.models_webhook import NotificacionRaw
from app.services.log_seguro import log_aviso

logger = logging.getLogger("pagook")

VENTANA_MISMO_TEXTO_MS = 60_000
MAX_CANDIDATOS = 50
LARGO_TEXTO_GUARDADO = 5000  # como se trunca `texto` al guardar el raw


def _ms(valor) -> int | None:
    """timestamp_celular -> int (epoch ms) o None si falta o no es válido."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        ms = int(float(valor))
    except (TypeError, ValueError, OverflowError):
        return None
    return ms if ms > 0 else None


def buscar_aviso_duplicado(db: Session, dispositivo, sbn_key: str | None, ts_celular,
                           texto: str | None, package: str | None = None) -> NotificacionRaw | None:
    """Devuelve el aviso previo del que este es duplicado, o None si es nuevo."""
    if not sbn_key:
        return None
    candidatos = (
        db.query(NotificacionRaw)
        .filter(NotificacionRaw.empresa_id == dispositivo.empresa_id,
                NotificacionRaw.sbn_key == sbn_key)
        .order_by(NotificacionRaw.id.desc())
        .limit(MAX_CANDIDATOS)
        .all()
    )
    if not candidatos:
        return None

    cel = _ms(ts_celular)
    if cel is None:
        return candidatos[0]   # sin hora del celular: regla anterior

    texto_norm = (texto or "")[:LARGO_TEXTO_GUARDADO]
    for previo in candidatos:
        previo_cel = _ms(previo.timestamp_celular)
        if previo_cel == cel:
            return previo                                   # reintento de la app
        if (previo.texto or "") == texto_norm and (
                previo_cel is None or abs(previo_cel - cel) < VENTANA_MISMO_TEXTO_MS):
            return previo                                   # el banco actualizó el aviso

    previo = candidatos[0]
    previo_cel = _ms(previo.timestamp_celular)
    diferencia = round((cel - previo_cel) / 1000) if previo_cel is not None else "sin_hora_previa"
    logger.warning(log_aviso(
        "Clave sbn_key repetida con otra hora: se guarda como aviso nuevo",
        dispositivo, package, "nuevo", texto,
        raw_previo=previo.id, diferencia_s=diferencia, coincidencias=len(candidatos)))
    return None
