"""Consolidación horaria de estado de dispositivos (device_estado_actual ->
device_estado_historico).

MVP simple: un thread background arranca en el startup de la app. Cada 60 s
verifica si cambió la hora local de Lima; al cambiar, consolida la hora anterior.

Aproximación MVP: estado_predominante = estado actual del dispositivo. Para
exactitud futura se necesitaría un log por-heartbeat (device_heartbeat_log).

DEFENSIVO: como el DDL de device_estado_historico no vino completo en el prompt,
la escritura va envuelta en try/except y NUNCA tumba el thread ni la app. Si el
esquema real difiere, se loguea el error cada hora y se puede corregir sin
afectar la recepción de heartbeats.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.admin.db import db_session
from app.api.models_device import DeviceEstadoActual, DeviceEstadoHistorico

logger = logging.getLogger("pagook")
TZ_LIMA = ZoneInfo("America/Lima")


def consolidar_hora_anterior(session) -> int:
    """Consolida la hora completa anterior. Devuelve cuántas filas se insertaron.

    Idempotente por UNIQUE (dispositivo_id, hora_inicio): si ya existe la fila
    de ese dispositivo+hora, no la duplica.
    """
    ahora_lima = datetime.now(TZ_LIMA)
    hora_actual = ahora_lima.replace(minute=0, second=0, microsecond=0)
    hora_inicio = hora_actual - timedelta(hours=1)
    hora_fin = hora_actual

    # Dispositivos con actividad dentro (o después) de esa hora.
    filas = (
        session.query(DeviceEstadoActual)
        .filter(DeviceEstadoActual.ultimo_heartbeat >= hora_inicio)
        .all()
    )

    insertados = 0
    for dea in filas:
        existe = (
            session.query(DeviceEstadoHistorico)
            .filter(
                DeviceEstadoHistorico.dispositivo_id == dea.dispositivo_id,
                DeviceEstadoHistorico.hora_inicio == hora_inicio,
            )
            .first()
        )
        if existe:
            continue
        session.add(DeviceEstadoHistorico(
            dispositivo_id=dea.dispositivo_id,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            estado_predominante=dea.estado,  # aproximación MVP
            manufacturer=dea.manufacturer,
            modelo=dea.modelo,
            android_version=dea.android_version,
            app_version=dea.app_version,
        ))
        insertados += 1
    return insertados


def _loop() -> None:
    ultima_hora = None
    while True:
        try:
            time.sleep(60)
            hora_actual = datetime.now(TZ_LIMA).replace(minute=0, second=0, microsecond=0)
            if ultima_hora is None:
                ultima_hora = hora_actual
                continue
            if hora_actual != ultima_hora:
                try:
                    with db_session() as db:  # hace commit al salir
                        n = consolidar_hora_anterior(db)
                    logger.info(f"Consolidación horaria de dispositivos: {n} filas nuevas")
                except Exception as exc:
                    logger.exception(f"Error en consolidación horaria (se continúa): {exc}")
                ultima_hora = hora_actual
        except Exception as exc:
            logger.exception(f"Error en loop de consolidación (se continúa): {exc}")
            time.sleep(60)


def iniciar_consolidacion_background() -> threading.Thread:
    """Arranca el thread daemon de consolidación. Se llama en el startup."""
    hilo = threading.Thread(target=_loop, name="consolidacion-heartbeat", daemon=True)
    hilo.start()
    logger.info("Thread de consolidación horaria de dispositivos iniciado")
    return hilo
