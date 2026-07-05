"""Modelos de heartbeat/estado de dispositivos Android.

Las tablas YA existen en Railway (DDL ejecutado por separado). Estos modelos
solo las mapean para el ORM.

- `DeviceEstadoActual`: una fila por dispositivo (upsert en cada heartbeat).
- `DeviceEstadoHistorico`: una fila por dispositivo por hora (consolidación).

NOTA: el DDL de referencia del prompt venía truncado; las columnas de
`device_estado_historico` se infieren de la descripción de la consolidación.
Verificar contra el esquema real de Railway antes de habilitar la consolidación.
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.admin.db import Base


class DeviceEstadoActual(Base):
    __tablename__ = "device_estado_actual"

    dispositivo_id = Column(
        Integer, ForeignKey("dispositivo.id", ondelete="CASCADE"), primary_key=True
    )
    estado = Column(String(20), nullable=False, default="verde", server_default="verde")
    ultimo_ping_guardian_ok = Column(DateTime(timezone=True))
    ultimo_pago_bancario = Column(DateTime(timezone=True))
    pings_fallidos_consecutivos = Column(Integer, default=0)
    veces_zombie_total = Column(Integer, default=0)
    veces_alarma_disparada = Column(Integer, default=0)
    veces_rebind_intentado = Column(Integer, default=0)
    veces_rebind_exitoso = Column(Integer, default=0)
    manufacturer = Column(String(50))
    modelo = Column(String(80))
    android_version = Column(String(20))
    app_version = Column(String(30))
    ultimo_heartbeat = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    dispositivo = relationship("Dispositivo")


class DeviceEstadoHistorico(Base):
    __tablename__ = "device_estado_historico"
    __table_args__ = (
        UniqueConstraint("dispositivo_id", "hora_inicio", name="uq_device_hist_disp_hora"),
    )

    id = Column(Integer, primary_key=True)
    dispositivo_id = Column(
        Integer, ForeignKey("dispositivo.id", ondelete="CASCADE"), nullable=False
    )
    hora_inicio = Column(DateTime(timezone=True), nullable=False)
    hora_fin = Column(DateTime(timezone=True), nullable=False)
    estado_predominante = Column(String(20))
    manufacturer = Column(String(50))
    modelo = Column(String(80))
    android_version = Column(String(20))
    app_version = Column(String(30))
    # Métricas consolidadas de la hora (Tarea 2).
    heartbeats_recibidos = Column(Integer, default=0)
    minutos_verde = Column(Integer, default=0)
    minutos_amarillo = Column(Integer, default=0)
    minutos_rojo = Column(Integer, default=0)
    pings_guardian_perdidos = Column(Integer, default=0)
    alarmas_disparadas = Column(Integer, default=0)
    rebinds_intentados = Column(Integer, default=0)
    rebinds_exitosos = Column(Integer, default=0)

    dispositivo = relationship("Dispositivo")
