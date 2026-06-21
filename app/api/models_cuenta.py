"""Modelo de credenciales para la API pública v1.

Una `CuentaApi` es una credencial de CONSUMIDOR (un sistema externo, ej. el
producto "pagos") autorizada a leer y reclamar los pagos de UNA empresa.

- Varias cuentas pueden apuntar a la misma empresa: todas ven el mismo pool de
  pagos y compiten por reclamarlos (de ahí el 409 "ya reclamado por otro").
- La api_key se guarda SOLO como hash SHA-256 (lookup determinista por hash).
  El valor en claro se muestra UNA sola vez al crearla.
- `prefijo` (los primeros caracteres del token, no secreto) sirve para
  identificar visualmente la credencial sin exponer el secreto.

Esta tabla es ADITIVA: no toca la ingesta Android ni el modelo de pagos.
"""
import hashlib
import secrets

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.admin.db import Base

# Prefijo legible para reconocer una key de PagoOK de un vistazo.
API_KEY_PREFIJO = "pk_live_"


def generar_api_key() -> str:
    """Genera una api_key nueva en claro: 'pk_live_<43 chars url-safe>'."""
    return API_KEY_PREFIJO + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    """Hash determinista (SHA-256 hex) para almacenar y para lookup."""
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def prefijo_visible(api_key: str) -> str:
    """Primeros caracteres no secretos, para mostrar en listados/admin."""
    return api_key[: len(API_KEY_PREFIJO) + 6]


class CuentaApi(Base):
    __tablename__ = "cuenta_api"

    id = Column(Integer, primary_key=True)
    empresa_id = Column(
        Integer,
        ForeignKey("empresa.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nombre del cliente/consumidor (ej. "Producto pagos", "Conciliador X").
    nombre = Column(String(150), nullable=False)
    # Solo el hash; el valor en claro nunca se persiste.
    api_key_hash = Column(String(64), unique=True, nullable=False)
    # Parte no secreta del token, para identificar la credencial visualmente.
    prefijo = Column(String(20))
    activa = Column(Boolean, default=True, nullable=False)
    creada_en = Column(DateTime, server_default=func.now())

    empresa = relationship("Empresa")
