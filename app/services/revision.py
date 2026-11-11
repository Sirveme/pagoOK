"""Cuenta de revisión de Google Play (zWeb-09).

Una sola empresa marcada con `es_cuenta_revision=True` (slug `revision-play` del
seed) sirve para que un revisor pruebe PagoOK de punta a punta sin datos reales.
Este módulo centraliza:

  - `empresa_revision(db)`: la empresa de revisión (por la bandera, no por slug).
  - Acceso al PANEL del revisor sin OTP por WhatsApp: un celular ficticio y un
    código fijo, ambos en variables de Railway. El código se guarda como HASH
    (SHA-256 en hex) en `REVISION_PANEL_CODIGO_HASH`; el texto en claro nunca
    vive en el servidor. Funciona SOLO para ese celular; cualquier otro usa el
    OTP normal. El rol lo decide la membresía sembrada (contador = solo lectura).

Variables (solo nombres; Duilio carga los valores en Railway):
  REVISION_PANEL_CELULAR        celular ficticio del revisor (E.164 o local PE)
  REVISION_PANEL_CODIGO_HASH    sha256(hex) del código fijo del revisor
    Generar el hash:  python -c "import hashlib;print(hashlib.sha256(b'EL-CODIGO').hexdigest())"
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time

from sqlalchemy.orm import Session

from app.admin.models import Empresa
from app.services.secretos import normalizar_celular

# Límite de intentos FALLIDOS del código fijo del panel, por IP. El código es
# fijo y reutilizable, así que el acceso de revisión necesita su propio candado
# (el OTP normal ya tiene su tope de 5 intentos por código, MAX_INTENTOS_OTP).
MAX_FALLOS_PANEL = 5
VENTANA_FALLOS_PANEL = 600  # segundos (10 min)
_lock_fallos = threading.Lock()
_fallos_panel: dict[str, list[float]] = {}


def panel_bloqueado(clave: str) -> bool:
    """True si esa clave (IP) superó el tope de fallos en la ventana."""
    limite = time.time() - VENTANA_FALLOS_PANEL
    with _lock_fallos:
        lista = [t for t in _fallos_panel.get(clave, []) if t > limite]
        _fallos_panel[clave] = lista
        return len(lista) >= MAX_FALLOS_PANEL


def registrar_fallo_panel(clave: str) -> None:
    with _lock_fallos:
        _fallos_panel.setdefault(clave, []).append(time.time())


def limpiar_fallos_panel(clave: str) -> None:
    with _lock_fallos:
        _fallos_panel.pop(clave, None)


def reiniciar_fallos_panel() -> None:
    """Limpia el estado del límite (tests)."""
    with _lock_fallos:
        _fallos_panel.clear()


def empresa_revision(db: Session) -> Empresa | None:
    """La empresa de revisión (bandera es_cuenta_revision). None si no existe."""
    return (
        db.query(Empresa)
        .filter(Empresa.es_cuenta_revision == True)  # noqa: E712
        .order_by(Empresa.id.asc())
        .first()
    )


def es_cuenta_revision(empresa) -> bool:
    """True si la empresa es la cuenta de revisión de Play."""
    return bool(getattr(empresa, "es_cuenta_revision", False))


def _celular_revision_raw() -> str:
    return os.getenv("REVISION_PANEL_CELULAR", "").strip()


def _codigo_hash_esperado() -> str:
    return os.getenv("REVISION_PANEL_CODIGO_HASH", "").strip().lower()


def acceso_panel_configurado() -> bool:
    """Hay celular ficticio y hash del código configurados."""
    return bool(_celular_revision_raw() and _codigo_hash_esperado())


def celular_revision_e164() -> str | None:
    """El celular ficticio del revisor normalizado a E.164, o None."""
    crudo = _celular_revision_raw()
    return normalizar_celular(crudo) if crudo else None


def es_celular_revision(celular_e164: str | None) -> bool:
    """True si ese celular (ya normalizado) es el acceso de revisión configurado."""
    if not acceso_panel_configurado() or not celular_e164:
        return False
    esperado = celular_revision_e164()
    return bool(esperado) and hmac.compare_digest(celular_e164, esperado)


def codigo_revision_valido(codigo: str | None) -> bool:
    """Compara el código recibido contra REVISION_PANEL_CODIGO_HASH (sha256 hex)."""
    esperado = _codigo_hash_esperado()
    if not esperado:
        return False
    recibido = hashlib.sha256((codigo or "").strip().encode("utf-8")).hexdigest()
    return hmac.compare_digest(recibido, esperado)
