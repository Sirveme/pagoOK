"""Parser de correos de alerta de los bancos (zWeb-06).

SOLO formatos vistos en correos reales (no se inventan). Hoy: BCP.
  a/b) "Realizaste un consumo de S/ X con tu Tarjeta de Débito BCP en EMPRESA."
       -> envio_billetera si Empresa empieza con PLIN- o YAPE-; si no, consumo_tarjeta
  c)   "Realizaste un yapeo a celular de S/ X desde tu ..."   -> envio_billetera
  d)   "Operación realizada: Pago de servicios"                -> pago_servicio
Sin ejemplos reales todavía: Tarjeta de Crédito BCP, ingresos por correo
("Recibiste una transferencia"), y cualquier otro banco.

Reglas: fechas en hora de Lima -> UTC naive; solo los últimos 4 dígitos de
tarjetas y cuentas; "Número de operación" -> codigo_operacion; S/ -> PEN,
US$ o $ -> USD (sin convertir).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.api.parser import _normalizar_monto

TZ_LIMA = ZoneInfo("America/Lima")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# "24 de octubre de 2026 - 08:16 AM"  |  "Martes, 20 Octubre 2026 - 11:51 A. M."
_RE_FECHA = re.compile(
    r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)\s+(?:de\s+)?(\d{4})\s*-\s*(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?",
    re.IGNORECASE,
)
_RE_MONTO = re.compile(r"(US\$|S/\.?|\$)\s*([\d.,]+)")
_RE_ULTIMOS4 = re.compile(r"^[*•xX\s]{2,}(\d{4})$")

# Promociones y avisos de seguridad: se descartan si no encajan en un formato.
_NO_TRANSACCIONAL = re.compile(
    r"promoci[oó]n|oferta|descuento|campa[nñ]a|sorteo|\bgana\b|cashback|beneficio|"
    r"\bclave\b|contrase[nñ]a|\btoken\b|inicio de sesi[oó]n|ingresaste|dispositivo nuevo|"
    r"verifica tu identidad|actualiza tus datos",
    re.IGNORECASE,
)


def lineas(texto: str) -> list[str]:
    return [l.strip() for l in (texto or "").replace("\r", "").split("\n") if l.strip()]


def campo(ls: list[str], etiqueta: str) -> tuple[str | None, int]:
    """Valor de 'Etiqueta<tab o :>valor' o, si va vacío, de la línea siguiente."""
    patron = re.compile(rf"^{re.escape(etiqueta)}\s*:?(?:\s+(.*))?$", re.IGNORECASE)
    for i, l in enumerate(ls):
        m = patron.match(l)
        if m:
            valor = (m.group(1) or "").strip(" \t:")
            if valor:
                return valor, i
            return (ls[i + 1] if i + 1 < len(ls) else None), i + 1
    return None, -1


def ultimos4_despues(ls: list[str], indice: int) -> str | None:
    """Últimos 4 dígitos enmascarados ('**** 5053') en las 2 líneas siguientes."""
    for l in ls[indice + 1: indice + 3] if indice >= 0 else []:
        m = _RE_ULTIMOS4.match(l)
        if m:
            return m.group(1)
    return None


def solo_ultimos4(valor: str | None) -> str | None:
    digitos = re.sub(r"\D", "", valor or "")
    return digitos[-4:] if len(digitos) >= 4 else None


def fecha_lima_a_utc(texto: str | None) -> datetime | None:
    m = _RE_FECHA.search(texto or "")
    if not m:
        return None
    dia, mes_txt, anio, hora, minuto, ampm = m.groups()
    mes = MESES.get(mes_txt.lower())
    if not mes:
        return None
    h = int(hora) % 12 + (12 if ampm.lower() == "p" else 0)
    local = datetime(int(anio), mes, int(dia), h, int(minuto), tzinfo=TZ_LIMA)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def monto_y_moneda(texto: str | None) -> tuple[Decimal | None, str | None]:
    m = _RE_MONTO.search(texto or "")
    if not m:
        return None, None
    simbolo, cifra = m.groups()
    return _normalizar_monto(cifra), ("PEN" if simbolo.startswith("S/") else "USD")


# =============================================================
# BCP
# =============================================================

_RE_BCP_CONSUMO = re.compile(
    r"^Realizaste un consumo de ((?:US\$|S/\.?|\$)\s*[\d.,]+) con tu Tarjeta de D[eé]bito BCP en (.+?)\.?$",
    re.IGNORECASE,
)
_RE_BCP_YAPEO = re.compile(
    r"^Realizaste un yapeo a celular de ((?:US\$|S/\.?|\$)\s*[\d.,]+) desde tu (.+?)\.?$", re.IGNORECASE,
)


def _bcp_consumo(ls: list[str]) -> dict | None:
    m = next((_RE_BCP_CONSUMO.match(l) for l in ls if _RE_BCP_CONSUMO.match(l)), None)
    operacion, _ = campo(ls, "Operación realizada")
    if not m or (operacion or "").lower() != "consumo tarjeta de débito":
        return None
    total, _ = campo(ls, "Total del consumo")
    monto, moneda = monto_y_moneda(total or m.group(1))
    empresa = (campo(ls, "Empresa")[0] or m.group(2)).strip()
    prefijo = empresa.upper().split("-", 1)[0] if "-" in empresa else ""
    es_billetera = prefijo in ("PLIN", "YAPE")
    tarjeta, _ = campo(ls, "Número de Tarjeta de Débito")
    return {
        "tipo_egreso": "envio_billetera" if es_billetera else "consumo_tarjeta",
        "metodo": "tarjeta", "monto": monto, "moneda": moneda, "contraparte": empresa,
        "destino": prefijo.capitalize() if es_billetera else None,
        "codigo_operacion": campo(ls, "Número de operación")[0],
        "fecha_utc": fecha_lima_a_utc(campo(ls, "Fecha y hora")[0]),
        "ultimos4": solo_ultimos4(tarjeta),
    }


def _bcp_yapeo(ls: list[str]) -> dict | None:
    m = next((_RE_BCP_YAPEO.match(l) for l in ls if _RE_BCP_YAPEO.match(l)), None)
    operacion, _ = campo(ls, "Operación realizada")
    if not m or (operacion or "").lower() != "yapear a celular":
        return None
    enviado, _ = campo(ls, "Monto enviado")
    monto, moneda = monto_y_moneda(enviado or m.group(1))
    _, i_desde = campo(ls, "Desde")
    return {
        "tipo_egreso": "envio_billetera", "metodo": "yape", "monto": monto, "moneda": moneda,
        "contraparte": campo(ls, "Enviado a")[0],
        "destino": campo(ls, "Destino")[0],
        "codigo_operacion": campo(ls, "Número de operación")[0],
        "fecha_utc": fecha_lima_a_utc(campo(ls, "Fecha y hora")[0]),
        "ultimos4": ultimos4_despues(ls, i_desde),
    }


def _bcp_servicio(ls: list[str]) -> dict | None:
    operacion, _ = campo(ls, "Operación realizada")
    if (operacion or "").lower() != "pago de servicios":
        return None
    total, _ = campo(ls, "Monto total")
    monto, moneda = monto_y_moneda(total)
    _, i_origen = campo(ls, "Cuenta de origen")
    return {
        "tipo_egreso": "pago_servicio", "metodo": "servicio", "monto": monto, "moneda": moneda,
        "contraparte": campo(ls, "Empresa")[0], "destino": campo(ls, "Servicio")[0],
        "codigo_operacion": campo(ls, "Número de operación")[0],
        "fecha_utc": fecha_lima_a_utc(campo(ls, "Fecha y hora")[0]),
        "ultimos4": ultimos4_despues(ls, i_origen),
    }


# Por banco: formatos de EGRESO verificados con correos reales.
FORMATOS_EGRESO = {"bcp": [_bcp_consumo, _bcp_yapeo, _bcp_servicio]}
# Ingresos por correo ("Recibiste una transferencia"): se agregan aquí cuando
# haya ejemplos reales. Misma firma; el resultado lleva tipo='ingreso'.
FORMATOS_INGRESO: dict[str, list] = {}


def parsear_correo(banco_clave: str | None, asunto: str, texto: str) -> dict:
    """{'estado': 'procesado'|'sin_formato'|'descartado', 'tipo', 'datos', 'motivo'}."""
    ls = lineas(texto)
    for tipo, registro in (("egreso", FORMATOS_EGRESO), ("ingreso", FORMATOS_INGRESO)):
        for formato in registro.get(banco_clave or "", []):
            datos = formato(ls)
            if datos:
                if datos.get("monto") is None or datos["monto"] <= 0 or datos.get("fecha_utc") is None:
                    return {"estado": "sin_formato", "motivo": "Formato reconocido pero sin monto o fecha legible"}
                return {"estado": "procesado", "tipo": tipo, "datos": datos}
    if _NO_TRANSACCIONAL.search(f"{asunto}\n{texto}"):
        return {"estado": "descartado", "motivo": "Promoción o aviso de seguridad"}
    if banco_clave not in FORMATOS_EGRESO:
        return {"estado": "sin_formato", "motivo": f"Sin formatos de correo para el banco {banco_clave or '?'}"}
    return {"estado": "sin_formato", "motivo": "El correo no coincide con ningún formato conocido"}
