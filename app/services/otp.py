"""Códigos OTP para ingresar al panel y aceptar invitaciones.

Envío (interfaz EnviadorOTP):
  - WhatsAppEnviador: WhatsApp Cloud API de Meta, igual que Te Atiendo
    (C:\\teatiendo\\app\\meta.py). Usa una plantilla de categoría
    AUTHENTICATION aprobada en Meta (el texto libre solo llega dentro de la
    ventana de 24 h, por eso no sirve para un OTP).
  - DevEnviador: escribe el OTP en el log, SOLO con PAGOOK_ENTORNO=desarrollo.

En producción, si WhatsApp no está configurado se responde 503
otp_no_disponible. Nunca se cae en silencio al modo desarrollo.

Reglas: 6 dígitos, vence en 5 minutos, máximo 5 intentos, un solo uso.
El código se guarda como HMAC (ver secretos.py), nunca en claro.
"""
from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.api.models_multiempresa import Otp
from app.services.secretos import enmascarar_celular, generar_otp, hash_codigo

logger = logging.getLogger("pagook")

VIGENCIA_OTP = timedelta(minutes=5)
MAX_INTENTOS_OTP = 5
# Anti-abuso del envío (cada OTP cuesta un mensaje de WhatsApp).
MAX_OTP_POR_CELULAR = 3
VENTANA_ENVIO = timedelta(minutes=10)
PROPOSITOS = ("login", "invitacion")


class OtpNoDisponible(Exception):
    """No hay forma de enviar el OTP en este entorno (-> 503)."""


class OtpDemasiados(Exception):
    """Se pidieron demasiados códigos para ese celular (-> 429)."""


def entorno() -> str:
    return os.getenv("PAGOOK_ENTORNO", "produccion").strip().lower()


def es_desarrollo() -> bool:
    return entorno() == "desarrollo"


# =============================================================
# ENVIADORES
# =============================================================

class EnviadorOTP:
    def enviar(self, celular_e164: str, codigo: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class DevEnviador(EnviadorOTP):
    """Solo desarrollo: el código va al log para poder probar sin WhatsApp."""

    def enviar(self, celular_e164: str, codigo: str) -> bool:
        if not es_desarrollo():
            raise OtpNoDisponible("DevEnviador fuera de desarrollo")
        logger.warning(f"[DESARROLLO] OTP para {celular_e164}: {codigo}")
        return True


class WhatsAppEnviador(EnviadorOTP):
    """WhatsApp Cloud API (Meta) con plantilla de autenticación.

    Variables: WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, WA_OTP_PLANTILLA,
    WA_OTP_IDIOMA (default es), GRAPH_API_VERSION (default v23.0).
    Las plantillas AUTHENTICATION llevan el código en el cuerpo y en el botón
    "Copiar código"; WA_OTP_BOTON=0 lo omite si la plantilla no tiene botón.
    """

    def __init__(self) -> None:
        self.token = os.getenv("WA_ACCESS_TOKEN", "").strip()
        self.phone_number_id = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
        self.plantilla = os.getenv("WA_OTP_PLANTILLA", "").strip()
        self.idioma = os.getenv("WA_OTP_IDIOMA", "es").strip() or "es"
        self.version = os.getenv("GRAPH_API_VERSION", "v23.0").strip() or "v23.0"
        self.con_boton = os.getenv("WA_OTP_BOTON", "1").strip() not in ("0", "false", "no")

    def configurado(self) -> bool:
        return bool(self.token and self.phone_number_id and self.plantilla)

    def enviar(self, celular_e164: str, codigo: str) -> bool:
        componentes = [{"type": "body", "parameters": [{"type": "text", "text": codigo}]}]
        if self.con_boton:
            componentes.append({
                "type": "button", "sub_type": "url", "index": "0",
                "parameters": [{"type": "text", "text": codigo}],
            })
        cuerpo = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": celular_e164.lstrip("+"),
            "type": "template",
            "template": {
                "name": self.plantilla,
                "language": {"code": self.idioma},
                "components": componentes,
            },
        }
        url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/messages"
        try:
            r = httpx.post(url, json=cuerpo, timeout=10,
                           headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as exc:
            logger.error(f"WhatsApp OTP: error de red enviando a {enmascarar_celular(celular_e164)}: "
                         f"{type(exc).__name__}")
            return False
        if 200 <= r.status_code < 300:
            return True
        # El cuerpo de error de Meta no contiene el código (solo el error).
        logger.error(f"WhatsApp OTP: HTTP {r.status_code} para {enmascarar_celular(celular_e164)}: {r.text[:300]}")
        return False


_enviador_forzado: EnviadorOTP | None = None


def usar_enviador(enviador: EnviadorOTP | None) -> None:
    """Inyecta un enviador (tests). None vuelve a la selección normal."""
    global _enviador_forzado
    _enviador_forzado = enviador


def obtener_enviador() -> EnviadorOTP:
    if _enviador_forzado is not None:
        return _enviador_forzado
    wa = WhatsAppEnviador()
    if wa.configurado():
        return wa
    if es_desarrollo():
        return DevEnviador()
    raise OtpNoDisponible("WhatsApp no configurado")


# =============================================================
# EMISIÓN Y VERIFICACIÓN
# =============================================================

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def emitir_otp(db: Session, celular_e164: str, proposito: str, enviador: EnviadorOTP | None = None) -> None:
    """Genera, guarda (hasheado) y envía un OTP. Lanza OtpNoDisponible,
    OtpDemasiados o RuntimeError si el envío falla."""
    assert proposito in PROPOSITOS
    enviador = enviador or obtener_enviador()
    ahora = _ahora()
    recientes = (
        db.query(Otp)
        .filter(Otp.celular == celular_e164, Otp.creado_en >= ahora - VENTANA_ENVIO)
        .count()
    )
    if recientes >= MAX_OTP_POR_CELULAR:
        raise OtpDemasiados()

    codigo = generar_otp()
    db.add(Otp(
        celular=celular_e164,
        codigo_hash=hash_codigo(codigo, celular_e164),
        proposito=proposito,
        creado_en=ahora,
        expira_en=ahora + VIGENCIA_OTP,
        intentos=0,
    ))
    db.commit()
    if not enviador.enviar(celular_e164, codigo):
        raise RuntimeError("No se pudo enviar el código por WhatsApp")


def verificar_otp(db: Session, celular_e164: str, codigo: str, proposito: str) -> bool:
    """True si el código es correcto. Consume el OTP; suma intentos si falla."""
    ahora = _ahora()
    otp = (
        db.query(Otp)
        .filter(Otp.celular == celular_e164, Otp.proposito == proposito, Otp.usado_en.is_(None))
        .order_by(Otp.id.desc())
        .first()
    )
    if otp is None or _aware(otp.expira_en) < ahora or (otp.intentos or 0) >= MAX_INTENTOS_OTP:
        return False
    if not hmac.compare_digest(otp.codigo_hash, hash_codigo((codigo or "").strip(), celular_e164)):
        otp.intentos = (otp.intentos or 0) + 1
        db.commit()
        return False
    otp.usado_en = ahora
    db.commit()
    return True
