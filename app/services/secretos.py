"""Generación y hash de secretos del panel multi-empresa.

- Tokens largos (sesión, invitación): SHA-256. Tienen 256 bits de entropía,
  un hash simple basta y permite el lookup directo por índice único.
- Códigos cortos (OTP de 6 dígitos, vinculación XXX-XXX): HMAC-SHA256 con un
  "pepper" del servidor (PAGOOK_PEPPER). Sin el pepper, un volcado de la BD
  no permite probar el millón de combinaciones de un OTP fuera de línea.

Nunca se escriben en logs (salvo el DevEnviador en PAGOOK_ENTORNO=desarrollo).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets

logger = logging.getLogger("pagook")

# Sin 0/O ni 1/I (ambiguos al dictar o leer en pantalla).
ALFABETO_VINCULACION = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_aviso_pepper = False


def _pepper() -> bytes:
    global _aviso_pepper
    valor = os.getenv("PAGOOK_PEPPER", "").strip()
    if not valor and not _aviso_pepper:
        logger.warning("PAGOOK_PEPPER no configurado: los códigos cortos se hashean sin pepper")
        _aviso_pepper = True
    return valor.encode("utf-8")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_codigo(codigo: str, contexto: str = "") -> str:
    """HMAC del código corto. `contexto` (ej. el celular) evita que dos OTP
    iguales de personas distintas tengan el mismo hash."""
    mensaje = f"{contexto}|{codigo}".encode("utf-8")
    return hmac.new(_pepper(), mensaje, hashlib.sha256).hexdigest()


def generar_token() -> str:
    return secrets.token_urlsafe(32)


def generar_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generar_codigo_vinculacion() -> str:
    """'XXX-XXX' sin caracteres ambiguos."""
    c = "".join(secrets.choice(ALFABETO_VINCULACION) for _ in range(6))
    return f"{c[:3]}-{c[3:]}"


def normalizar_codigo_vinculacion(codigo: str | None) -> str:
    """'abc-def ' -> 'ABCDEF' (lo que se hashea)."""
    return re.sub(r"[^A-Z0-9]", "", (codigo or "").upper())


def normalizar_celular(celular: str | None, pais_por_defecto: str = "51") -> str | None:
    """Devuelve el celular en E.164 (+51987654321) o None si no es válido.

    Acepta '987 654 321', '987654321', '51987654321', '+51 987-654-321'.
    Sin prefijo y con 9 dígitos que empiezan en 9 se asume Perú.
    """
    if not celular:
        return None
    digitos = re.sub(r"\D", "", celular)
    if celular.strip().startswith("+"):
        e164 = "+" + digitos
    elif len(digitos) == 9 and digitos.startswith("9"):
        e164 = f"+{pais_por_defecto}{digitos}"
    elif len(digitos) == 11 and digitos.startswith(pais_por_defecto):
        e164 = "+" + digitos
    else:
        return None
    return e164 if re.fullmatch(r"\+\d{8,15}", e164) else None


def enmascarar_celular(e164: str) -> str:
    """'+51987654321' -> '+51 ••• ••• 321' (para mostrar en pantalla)."""
    return f"{e164[:3]} ••• ••• {e164[-3:]}" if e164 and len(e164) > 6 else "•••"
