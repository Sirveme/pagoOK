"""Modelos del canal de correo bancario (zWeb-06).

DDL: sql/correo_bancos_2026-10.sql.
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.sql import func

from app.admin.db import Base


class CorreoEmpresa(Base):
    """Dirección egresos+<codigo>@bancos.pagook.pro de cada empresa."""
    __tablename__ = "correo_empresa"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    codigo = Column(String(16), unique=True, nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    regenerado_en = Column(DateTime(timezone=True))


class CorreoRaw(Base):
    """Cada correo recibido. cuerpo_texto, asunto y mime se purgan a los 90 días."""
    __tablename__ = "correo_raw"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"))
    destinatario = Column(String(200))
    remitente = Column(String(200))
    asunto = Column(Text)
    cuerpo_texto = Column(Text)
    mime = Column(LargeBinary)
    message_id = Column(String(300))
    recibido_en = Column(DateTime(timezone=True), server_default=func.now())
    dkim_ok = Column(Boolean, nullable=False, default=False)
    dkim_dominios = Column(String(300))
    banco_clave = Column(String(30))
    # procesado | sin_formato | rechazado | descartado | confirmacion_gmail
    estado = Column(String(20), nullable=False)
    motivo = Column(Text)
    codigo_confirmacion = Column(String(20))
    pago_id = Column(Integer, ForeignKey("pago_detectado.id", ondelete="SET NULL"))


class SolicitudBanco(Base):
    """Pedido de un banco o billetera que todavía no está en el catálogo."""
    __tablename__ = "solicitud_banco"
    id = Column(Integer, primary_key=True)
    pais = Column(String(2), nullable=False)
    nombre_banco = Column(String(80), nullable=False)
    nombre_normalizado = Column(String(80), nullable=False)
    dispositivo_id = Column(Integer, ForeignKey("dispositivo.id", ondelete="SET NULL"))
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="SET NULL"))
    ip_hash = Column(String(64), nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
