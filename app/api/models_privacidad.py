"""Modelos de privacidad: solicitudes de eliminación y auditoría mínima.

DDL: sql/egresos_privacidad_2026-10.sql.
"""
from sqlalchemy import JSON, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.admin.db import Base


class SolicitudEliminacion(Base):
    """Pedido de eliminación desde el formulario público (sin acceso al panel)."""
    __tablename__ = "solicitud_eliminacion"
    id = Column(Integer, primary_key=True)
    pais = Column(String(2), nullable=False)
    id_fiscal = Column(String(20), nullable=False)
    nombre = Column(String(150))
    contacto = Column(String(150))
    motivo = Column(Text)
    ip_hash = Column(String(64), nullable=False)
    estado = Column(String(12), nullable=False, default="pendiente")
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    atendida_en = Column(DateTime(timezone=True))


class EliminacionAuditoria(Base):
    """Lo ÚNICO que queda de una empresa eliminada: sin nombres ni pagos."""
    __tablename__ = "eliminacion_auditoria"
    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    id_fiscal_hash = Column(String(64), nullable=False)
    conteos = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    canal = Column(String(12), nullable=False)  # autoservicio | solicitud
