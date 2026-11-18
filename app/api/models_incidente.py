"""Incidentes de un dispositivo: zombies y alarmas con fecha (zWeb-11).

Hasta zWeb-11 solo existían los CONTADORES de device_estado_actual
(veces_zombie_total, veces_alarma_disparada): no había forma de saber cuándo
pasó cada uno ni por qué. Desde ahora, el heartbeat deja una fila aquí cada vez
que esos contadores suben. Los contadores anteriores quedan "sin fecha".

  tipo   = 'zombie' | 'alarma'
  causa  = 'lector_desconectado' | 'sin_permiso' | NULL (la app no la informó)
  activa = la condición sigue presente; se cierra sola (cerrada_en) cuando un
           heartbeat posterior llega sano.
  revisada_* = auditoría del botón "Marcar como revisada" del panel de estado.
  listener_conectado / permiso_notificaciones = cómo estaba el lector AL INICIO.
  recuperacion = 'sola' | 'intervencion' | 'sin_determinar' (al cerrarse), con
           su explicación en recuperacion_detalle. Ver services/incidentes.py.

Tabla creada por sql/incidentes_dispositivo_2026-11-17.sql (zWeb-11/12).
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.admin.db import Base


class DispositivoIncidente(Base):
    __tablename__ = "dispositivo_incidente"
    __table_args__ = (
        Index("ix_dispositivo_incidente_disp_fecha", "dispositivo_id", "ocurrido_en"),
    )

    id = Column(Integer, primary_key=True)
    dispositivo_id = Column(Integer, ForeignKey("dispositivo.id", ondelete="CASCADE"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(String(20), nullable=False)
    causa = Column(String(30))
    mensaje = Column(String(300), nullable=False)
    # Un heartbeat puede traer el contador de zombies subido en más de 1.
    cantidad = Column(Integer, nullable=False, default=1, server_default="1")
    app_version = Column(String(30))
    # Estado del lector al inicio (NULL = la app no lo informa: versiones viejas).
    listener_conectado = Column(Boolean)
    permiso_notificaciones = Column(Boolean)
    # Contador de reconexiones exitosas de la app al inicio: si sube antes del
    # cierre, la app se recuperó sola.
    rebind_exitoso_inicio = Column(Integer)
    recuperacion = Column(String(20))
    recuperacion_detalle = Column(String(150))
    ocurrido_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    activa = Column(Boolean, nullable=False, default=True, server_default="true")
    cerrada_en = Column(DateTime(timezone=True))
    revisada_en = Column(DateTime(timezone=True))
    revisada_por = Column(String(150))
    revisada_ip = Column(String(64))
