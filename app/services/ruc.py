"""Consulta de RUC (Perú) con la API que usa todo el ecosistema: apis.net.pe v2.

Mismo contrato que C:\\alertape\\app\\services\\ruc_validator.py:
  GET https://api.apis.net.pe/v2/sunat/ruc?numero={ruc}
  Authorization: Bearer $APIS_NET_PE_TOKEN

Nunca lanza excepción ni escribe el token en logs. Devuelve None si no se
pudo consultar (sin token, red, 4xx/5xx) para que quien llama decida.
"""
from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger("pagook")

URL_RUC = "https://api.apis.net.pe/v2/sunat/ruc"


def ruc_valido_formato(ruc: str) -> bool:
    """11 dígitos y prefijo de contribuyente peruano (10, 15, 16, 17, 20)."""
    return bool(re.fullmatch(r"(10|15|16|17|20)\d{9}", ruc or ""))


def consultar_ruc(ruc: str, timeout: float = 10.0) -> dict | None:
    """{ruc, razon_social, estado, condicion, direccion} o None."""
    token = os.getenv("APIS_NET_PE_TOKEN", "").strip()
    if not token:
        logger.warning("APIS_NET_PE_TOKEN no configurado: no se puede validar el RUC")
        return None
    try:
        r = httpx.get(
            URL_RUC, params={"numero": ruc}, timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        logger.warning(f"Consulta RUC {ruc}: error de red ({type(exc).__name__})")
        return None
    if r.status_code != 200:
        logger.warning(f"Consulta RUC {ruc}: HTTP {r.status_code}")
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    razon = (data.get("razonSocial") or data.get("nombre") or "").strip()
    if not razon:
        return None
    return {
        "ruc": ruc,
        "razon_social": razon,
        "estado": (data.get("estado") or "").strip(),
        "condicion": (data.get("condicion") or "").strip(),
        "direccion": (data.get("direccion") or "").strip(),
    }
