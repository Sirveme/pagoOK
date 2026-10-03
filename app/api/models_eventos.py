"""Modelos SQLAlchemy del contrato de eventos (webhooks salientes).

DDL: sql/eventos_webhook.sql (SQL-primero, se aplica a mano en PGAdmin).
Los tipos ARRAY/JSONB tienen variante JSON para que los tests corran en SQLite.
"""
from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, BigInteger, Integer, String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.admin.db import Base

ESTADO_PENDIENTE = "pendiente"
ESTADO_ENTREGADO = "entregado"
ESTADO_FALLIDO = "fallido"

_TiposSuscritos = JSON().with_variant(ARRAY(Text), "postgresql")
_Payload = JSON().with_variant(JSONB(), "postgresql")


class SuscripcionWebhook(Base):
    __tablename__ = "suscripcion_webhook"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    nombre = Column(String(150))
    url_destino = Column(Text, nullable=False)
    secreto_hmac = Column(String(128), unique=True, nullable=False)
    tipos_suscritos = Column(_TiposSuscritos, nullable=False, default=lambda: ["pago.confirmado"])
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())

    def suscrita_a(self, tipo: str) -> bool:
        return tipo in (self.tipos_suscritos or [])


class EntregaEvento(Base):
    __tablename__ = "entrega_evento"
    __table_args__ = (
        UniqueConstraint("evento_id", "suscripcion_id", name="uq_entrega_evento_evento_suscripcion"),
    )
    # BigInteger en Postgres; Integer en SQLite para que el autoincremento funcione.
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    evento_id = Column(Uuid(as_uuid=False), nullable=False)
    suscripcion_id = Column(
        Integer, ForeignKey("suscripcion_webhook.id", ondelete="CASCADE"), nullable=False
    )
    tipo = Column(String(60), nullable=False)
    payload = Column(_Payload, nullable=False)
    intentos = Column(Integer, nullable=False, default=0)
    ultimo_estado = Column(String(20), nullable=False, default=ESTADO_PENDIENTE)
    ultimo_codigo_http = Column(Integer)
    ultimo_error = Column(Text)
    proximo_reintento_en = Column(DateTime(timezone=True))
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    entregado_en = Column(DateTime(timezone=True))

    suscripcion = relationship("SuscripcionWebhook")
