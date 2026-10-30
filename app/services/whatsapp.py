"""Avisos operativos al dueño por WhatsApp (hoy: celular desconectado).

NO duplica el cliente: reutiliza la configuración y el envío del OTP
(app/services/otp.py → WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, GRAPH_API_VERSION).
Aquí solo va lo propio de estos avisos: normalizar el celular del dueño y armar
el componente `body` de la plantilla a partir de sus variables.
"""
from __future__ import annotations

import os
import re

from app.services import otp

# Idioma de las plantillas de alerta (las de Meta se aprueban en este idioma).
IDIOMA = os.getenv("WA_ALERTA_IDIOMA", "es").strip() or "es"


def configurado() -> bool:
    """Hay token y phone id para enviar (misma config que el OTP)."""
    return otp.whatsapp_disponible()


def telefono_e164(texto: str | None) -> str | None:
    """'987 654 321' -> '51987654321'. Acepta ya-internacional (11-15 dígitos).
    None si no parece un celular enviable."""
    n = re.sub(r"\D", "", texto or "")
    if len(n) == 11 and n.startswith("519"):
        n = n[2:]
    if re.fullmatch(r"9\d{8}", n):          # celular peruano local
        return "51" + n
    if re.fullmatch(r"\d{11,15}", n):       # ya en formato internacional
        return n
    return None


def enviar_plantilla(destino_e164: str, plantilla: str, parametros: list[str] | None = None) -> bool:
    """Envía una plantilla aprobada con sus variables {{1}}, {{2}}… en el body.
    Delega el POST en el cliente compartido del OTP. False si falta algo."""
    if not (destino_e164 and plantilla):
        return False
    componentes = []
    if parametros:
        componentes = [{"type": "body",
                        "parameters": [{"type": "text", "text": str(p)} for p in parametros]}]
    return otp.enviar_plantilla_whatsapp(destino_e164, plantilla, IDIOMA, componentes)
