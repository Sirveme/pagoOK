"""Modelos multi-empresa: usuarios del panel, membresías, sesiones, OTP,
códigos de vinculación de celulares, invitaciones y catálogo de bancos.

DDL: sql/multiempresa_2026-10.sql (SQL-primero, se aplica a mano en PGAdmin).
Códigos, tokens y OTP se guardan SIEMPRE hasheados (ver app/services/secretos.py).
No se guarda DNI ni ningún documento de identidad del usuario.
"""
from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.admin.db import Base

ROLES = ("dueno", "cajero", "almacen", "contador")
_JSON = JSON().with_variant(JSONB(), "postgresql")


class Usuario(Base):
    __tablename__ = "usuario"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    celular = Column(String(20), unique=True, nullable=False)  # E.164
    email = Column(String(150))
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    activo = Column(Boolean, nullable=False, default=True)


class Membresia(Base):
    __tablename__ = "membresia"
    __table_args__ = (UniqueConstraint("usuario_id", "empresa_id", name="uq_membresia_usuario_empresa"),)
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    rol = Column(String(12), nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    revocado_en = Column(DateTime(timezone=True))

    usuario = relationship("Usuario")
    empresa = relationship("Empresa")


class Sesion(Base):
    __tablename__ = "sesion"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    expira_en = Column(DateTime(timezone=True), nullable=False)
    revocada_en = Column(DateTime(timezone=True))


class Otp(Base):
    __tablename__ = "otp"
    id = Column(Integer, primary_key=True)
    celular = Column(String(20), nullable=False)
    codigo_hash = Column(String(64), nullable=False)
    proposito = Column(String(12), nullable=False)  # login | invitacion
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    expira_en = Column(DateTime(timezone=True), nullable=False)
    intentos = Column(Integer, nullable=False, default=0)
    usado_en = Column(DateTime(timezone=True))


class CodigoVinculacion(Base):
    __tablename__ = "codigo_vinculacion"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    codigo_hash = Column(String(64), unique=True, nullable=False)
    creado_por = Column(Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    expira_en = Column(DateTime(timezone=True), nullable=False)
    usado_en = Column(DateTime(timezone=True))
    dispositivo_id = Column(Integer, ForeignKey("dispositivo.id", ondelete="SET NULL"))


class Invitacion(Base):
    __tablename__ = "invitacion"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    rol = Column(String(12), nullable=False)
    creado_por = Column(Integer, ForeignKey("usuario.id", ondelete="SET NULL"))
    creado_en = Column(DateTime(timezone=True), server_default=func.now())
    expira_en = Column(DateTime(timezone=True), nullable=False)
    usado_en = Column(DateTime(timezone=True))
    usuario_id = Column(Integer, ForeignKey("usuario.id", ondelete="SET NULL"))


class BancoConfig(Base):
    __tablename__ = "banco_config"
    __table_args__ = (UniqueConstraint("pais", "clave", name="uq_banco_config_pais_clave"),)
    id = Column(Integer, primary_key=True)
    pais = Column(String(2), nullable=False)
    clave = Column(String(30), nullable=False)
    nombre = Column(String(80), nullable=False)
    tipo = Column(String(10), nullable=False)  # billetera | banco | caja
    activo = Column(Boolean, nullable=False, default=True)
    orden = Column(Integer, nullable=False, default=0)
    # --- zWeb-06: "cerebro dinámico" (se editan en /admin/bancos) ---
    paquetes_android = Column(_JSON, nullable=False, default=list)   # ej. ["com.bcp.innovacxion.yapeapp"]
    dominios_correo = Column(_JSON, nullable=False, default=list)    # ej. ["bcp.com.pe"]
    guia_alertas = Column(Text)
    color = Column(String(9))
