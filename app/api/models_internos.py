"""Modelos para cuentas propias del dueño y detección de transferencias internas.

- `PersonaCuenta`: los nombres/alias con que el dueño aparece en las push del
  banco (varían entre Yape, Plin, BCP, etc.). Sirven para detectar cuando un
  pago es en realidad una transferencia entre las billeteras del propio dueño.
- `TitularConfirmadoExterno`: nombres que el usuario ya marcó como NO propios
  (ej. un familiar con apellido parecido). Una vez confirmado externo, el
  sistema no vuelve a sugerir "posible interno" para ese nombre en esa empresa.

Tablas ADITIVAS. El esquema real se crea con sql/egresos_internos.sql (PGAdmin).
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class PersonaCuenta(Base):
    __tablename__ = "persona_cuenta"

    id = Column(Integer, primary_key=True)
    persona_id = Column(Integer, ForeignKey("persona.id", ondelete="CASCADE"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    # Nombre tal cual aparece en la notificación del banco/billetera.
    nombre_en_push = Column(String(200), nullable=False)
    # Nombre normalizado (minúsculas, sin tildes ni puntos) para comparar.
    nombre_normalizado = Column(String(200), nullable=False)
    # 'yape' | 'plin' | 'bcp' | 'bbva' | 'interbank' | 'scotiabank' | ... | 'otro'
    billetera = Column(String(20), nullable=False)
    banco = Column(String(50), default=None)
    telefono = Column(String(20), default=None)
    activa = Column(Boolean, default=True, nullable=False)
    creada_en = Column(DateTime, server_default=func.now())
    actualizada_en = Column(DateTime, server_default=func.now())

    persona = relationship("Persona")
    empresa = relationship("Empresa")


class TitularConfirmadoExterno(Base):
    __tablename__ = "titular_confirmado_externo"
    __table_args__ = (
        UniqueConstraint("empresa_id", "nombre_normalizado", name="uq_titular_externo_empresa_nombre"),
    )

    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    nombre_normalizado = Column(String(200), nullable=False)
    veces_confirmado = Column(Integer, default=1, nullable=False)
    ultima_confirmacion = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    empresa = relationship("Empresa")
