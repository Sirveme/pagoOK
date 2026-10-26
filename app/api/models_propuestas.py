"""Registro de visitas a las propuestas comerciales (/propuestas/<codigo>).

DDL: sql/propuestas_2026-10.sql.
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.admin.db import Base


class PropuestaVisita(Base):
    """Una apertura de la propuesta. Sin IP en claro: solo su hash."""
    __tablename__ = "propuesta_visita"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(80), nullable=False)
    visto_en = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user_agent = Column(String(60))          # resumido: "Android · Chrome", "WhatsApp (vista previa)"
    ip_hash = Column(String(64), nullable=False)
    vista_previa = Column(Boolean, nullable=False, default=False)   # robot de WhatsApp/Facebook armando la tarjeta
