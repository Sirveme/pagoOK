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
    # --- Detección de cuentas propias (aditivo) ---
    # Sugerencia calculada AL RECIBIR: el titular parece una cuenta del dueño.
    # NUNCA cambia el `tipo` por sí solo; requiere confirmación manual del usuario.
    posible_interno = Column(Boolean, default=False, nullable=False, server_default="false")
    # NULL = sin revisar | 'confirmado_interno' | 'confirmado_externo'
    confirmacion_usuario = Column(String(20), default=None)
    # El parser no pudo clasificar ingreso/egreso con certeza (guardado como
    # 'ingreso' por defecto, marcado para revisión).
    tipo_incierto = Column(Boolean, default=False, nullable=False, server_default="false")


# Co-registro del destino del FK `reclamado_por -> cuenta_api.id`.
# PagoDetectado declara ese FK, así que la tabla `cuenta_api` (modelo CuentaApi)
# DEBE estar en el mismo metadata/registry ANTES de resolver el FK (DDL/flush).
# Sin esto, `cuenta_api` solo se registraba como efecto colateral de importar la
# API v1 (app/api/publica_v1.py); si la ingesta resolvía el FK sin ese import,
# rompía con NoReferencedTableError y NINGÚN pago se guardaba. Import al final
# para evitar ciclos (models_cuenta no depende de este módulo).
from app.api.models_cuenta import CuentaApi  # noqa: E402,F401
