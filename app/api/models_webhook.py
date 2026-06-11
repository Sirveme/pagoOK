"""Modelos SQLAlchemy del webhook de notificaciones."""
from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, Numeric, DateTime, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.admin.db import Base


class Dispositivo(Base):
    __tablename__ = "dispositivo"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    nombre = Column(String(100), nullable=False)
    modelo = Column(String(100))
    activo = Column(Boolean, default=True)
    ultimo_ping = Column(DateTime)
    total_notificaciones = Column(Integer, default=0)
    creado_en = Column(DateTime, server_default=func.now())

    empresa = relationship("Empresa")


class NotificacionRaw(Base):
    __tablename__ = "notificacion_raw"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    dispositivo_id = Column(Integer, ForeignKey("dispositivo.id"))
    package_app = Column(String(200))
    titulo = Column(Text)
    texto = Column(Text)
    sbn_key = Column(String(200))
    timestamp_celular = Column(BigInteger)
    timestamp_servidor = Column(DateTime, server_default=func.now())
    estado_parseo = Column(String(20), default="pendiente")
    metodo_detectado = Column(String(20))
    error_parseo = Column(Text)


class PagoDetectado(Base):
    __tablename__ = "pago_detectado"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    notificacion_raw_id = Column(Integer, ForeignKey("notificacion_raw.id", ondelete="CASCADE"))
    metodo = Column(String(20), nullable=False)
    monto = Column(Numeric(12, 2), nullable=False)
    moneda = Column(String(3), default="PEN")
    titular = Column(Text)
    titular_corto = Column(String(50))
    codigo_operacion = Column(String(30))
    banco = Column(String(50))
    # ingreso = pago recibido; egreso = pago enviado desde el celular del titular
    tipo = Column(String(10), default="ingreso", nullable=False, server_default="ingreso")
    recibido_en = Column(DateTime, server_default=func.now())
    consumido = Column(Boolean, default=False)
    consumido_en = Column(DateTime)
    venta_id = Column(Integer)
    # --- API pública v1 (aditivo): reclamo por un consumidor externo ---
    # Independiente de `consumido` (que lo usa la PWA Caja interna).
    reclamado_por = Column(Integer, ForeignKey("cuenta_api.id", ondelete="SET NULL"))
    reclamado_en = Column(DateTime)
