"""Aviso al dueño cuando su celular de PagoOK deja de conectarse.

Si un dispositivo ACTIVO no da señales de vida (ni heartbeat del guardián ni
notificaciones) en 30 min, se le manda UN WhatsApp al dueño de la empresa. Al
volver a conectarse, otro WhatsApp. Un solo aviso por caída y uno por regreso:
el estado se guarda en `dispositivo.alerta_caida_en` (persiste entre reinicios).

Thread de fondo, best-effort: cualquier error se loguea y nunca tumba la app.
La lógica de decisión (`evaluar`) es pura y está cubierta por tests.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.admin.db import db_session
from app.admin.models import Empresa
from app.api.models_device import DeviceEstadoActual
from app.api.models_webhook import Dispositivo
from app.services import whatsapp

logger = logging.getLogger("pagook")
TZ_LIMA = ZoneInfo("America/Lima")

UMBRAL_CAIDA = timedelta(minutes=30)
INTERVALO_REVISION_SEG = 120      # revisa cada 2 min
# Con varios workers (Gunicorn/uvicorn), solo UNO debe revisar por vuelta. Se usa
# un advisory lock de PostgreSQL con esta clave fija; los demás workers la saltan.
LOCK_KEY = 823641
# plantilla_key -> variable de entorno con el nombre real de la plantilla en Meta
_PLANTILLAS = {"caida": "WA_PLANTILLA_CAIDA", "regreso": "WA_PLANTILLA_REGRESO"}


def _a_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def ultimo_contacto(ultimo_ping: datetime | None, ultimo_heartbeat: datetime | None) -> datetime | None:
    """La señal de vida más reciente entre la notificación y el heartbeat (UTC naive)."""
    señales = [d for d in (_a_naive_utc(ultimo_ping), _a_naive_utc(ultimo_heartbeat)) if d]
    return max(señales) if señales else None


def evaluar(activo: bool, ultimo: datetime | None, alerta_caida_en: datetime | None,
            ahora: datetime, umbral: timedelta = UMBRAL_CAIDA) -> str | None:
    """Decisión pura: 'caida', 'regreso' o None.

    - 'caida'   : está caído (sin señal > umbral, o nunca) y aún NO se avisó.
    - 'regreso' : volvió (señal reciente) y había una caída avisada pendiente.
    """
    if not activo:
        return None
    caido = ultimo is None or (ahora - ultimo) > umbral
    if caido and alerta_caida_en is None:
        return "caida"
    if not caido and alerta_caida_en is not None:
        return "regreso"
    return None


def _hora_lima(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ_LIMA).strftime("%H:%M")


def _enviar_real(destino_e164: str | None, plantilla_key: str, parametros: list[str]) -> bool:
    """Mapea 'caida'/'regreso' a la plantilla de Meta y la envía. False si falta algo."""
    if not destino_e164:
        return False
    nombre = os.getenv(_PLANTILLAS[plantilla_key], "").strip()
    return whatsapp.enviar_plantilla(destino_e164, nombre, parametros)


def revisar(db, ahora: datetime | None = None, enviar=None) -> dict:
    """Revisa todos los dispositivos activos y envía los avisos que correspondan.

    `enviar(destino_e164, plantilla_key, parametros) -> bool` es inyectable (tests).
    Solo marca/limpia `alerta_caida_en` cuando el envío fue aceptado, así un fallo
    de WhatsApp se reintenta en la próxima vuelta.
    """
    ahora = ahora or datetime.utcnow()
    enviar = enviar or _enviar_real
    caidas = regresos = 0
    for disp in db.query(Dispositivo).filter(Dispositivo.activo == True).all():  # noqa: E712
        dea = db.get(DeviceEstadoActual, disp.id)
        ultimo = ultimo_contacto(disp.ultimo_ping, dea.ultimo_heartbeat if dea else None)
        accion = evaluar(True, ultimo, disp.alerta_caida_en, ahora)
        if not accion:
            continue
        empresa = db.get(Empresa, disp.empresa_id)
        duenio = getattr(empresa, "duenio", None) if empresa else None
        telefono = whatsapp.telefono_e164(getattr(duenio, "telefono", None))
        modelo = disp.modelo or (dea.modelo if dea else None) or "celular"
        if accion == "caida":
            if enviar(telefono, "caida", [modelo, _hora_lima(ultimo)]):
                disp.alerta_caida_en = ahora
                caidas += 1
                logger.info("Aviso de caída enviado: dispositivo %s (%s), empresa %s",
                            disp.id, modelo, disp.empresa_id)
        else:  # regreso
            if enviar(telefono, "regreso", [modelo]):
                disp.alerta_caida_en = None
                regresos += 1
                logger.info("Aviso de regreso enviado: dispositivo %s (%s), empresa %s",
                            disp.id, modelo, disp.empresa_id)
    db.commit()
    return {"caidas": caidas, "regresos": regresos}


def _es_lider(db) -> bool:
    """True si este proceso tomó el advisory lock de la revisión (transaccional:
    se libera solo al cerrar la transacción). Con varios workers, solo uno lo toma.
    En motores sin advisory locks (SQLite en tests) devuelve True: ahí no hay
    varios procesos compitiendo."""
    try:
        return bool(db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                               {"k": LOCK_KEY}).scalar())
    except Exception:
        return True


def revisar_con_lock(db, ahora: datetime | None = None, enviar=None) -> dict:
    """Revisa solo si este proceso es el líder (un solo worker por vuelta)."""
    if not _es_lider(db):
        return {"lider": False, "caidas": 0, "regresos": 0}
    return {"lider": True, **revisar(db, ahora, enviar)}


def _loop() -> None:
    while True:
        try:
            time.sleep(INTERVALO_REVISION_SEG)
            if not whatsapp.configurado():
                continue   # feature dormida hasta tener token y plantillas
            with db_session() as db:   # hace commit al salir (libera el advisory lock)
                revisar_con_lock(db)
        except Exception as exc:
            logger.exception(f"Error en alerta de dispositivos (se continúa): {exc}")


def iniciar_alertas_background() -> threading.Thread:
    """Arranca el thread daemon de alertas de caída. Se llama en el startup."""
    hilo = threading.Thread(target=_loop, name="alerta-dispositivos", daemon=True)
    hilo.start()
    logger.info("Thread de alertas de dispositivos iniciado")
    return hilo
