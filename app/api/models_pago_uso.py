"""Registro de "pago usado" (idempotencia de uso de la API de consulta).

DDL: sql/consulta_pagos.sql. UNIQUE(pago_id): un pago confirma una sola
suscripción/pedido, en todo el ecosistema.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.admin.db import Base


class PagoUso(Base):
    __tablename__ = "pago_uso"
    id = Column(Integer, primary_key=True)
    pago_id = Column(
        Integer, ForeignKey("pago_detectado.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    cuenta_api_id = Column(Integer, ForeignKey("cuenta_api.id", ondelete="SET NULL"))
    sistema_que_uso = Column(String(150), nullable=False)
    referencia_externa = Column(String(150), nullable=False)
    usado_en = Column(DateTime(timezone=True), server_default=func.now())
