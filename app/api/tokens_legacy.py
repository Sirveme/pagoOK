"""Deprecación del token por defecto compartido.

Antes del auto-registro, todas las instalaciones sin token propio compartían un
único token hardcodeado. Es inseguro y arruina la telemetría por dispositivo.

Plan: NO borrar ni desactivar por ahora (sigue funcionando). Solo se loguea un
WARNING cada vez que se usa, para detectar quién falta re-registrarse.

TODO(deprecación ~2026-08-03, 30 días tras el deploy): una vez que las apps se
hayan re-registrado con su token propio, desactivar este token por defecto y
exigir re-registro. Revisar esta fecha.
"""
import logging

TOKEN_POR_DEFECTO = "tok_dKObQrhUGJGg28kW5yD_qXahsNVsVRTx"


def advertir_si_token_legacy(token: str | None, endpoint: str, logger: logging.Logger) -> None:
    """Loguea un WARNING si se usó el token por defecto compartido."""
    if token and token == TOKEN_POR_DEFECTO:
        logger.warning(
            f"Token por defecto usado en {endpoint}, dispositivo debería re-registrarse"
        )
