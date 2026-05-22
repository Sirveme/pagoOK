"""Parser de texto de notificaciones bancarias.

Cada parser toma `texto` y devuelve dict con keys:
  metodo, monto, moneda, titular, titular_corto, codigo_operacion, banco
o None si no detecta nada.

Probamos parsers en orden hasta que uno matche.
"""
import re
from decimal import Decimal


def _normalizar_monto(s: str) -> Decimal | None:
    if not s:
        return None
    s = s.strip().replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return Decimal(s)
    except Exception:
        return None


def _abreviar_titular(nombre: str) -> str:
    """'JUAN CARLOS PEREZ LOPEZ' -> 'J. C. PEREZ L.'
    'Bertha Mariana Restuccia' -> 'B. M. Restuccia'
    'DUILIO RESTUCCIA' -> 'D. RESTUCCIA'
    """
    if not nombre:
        return ""
    partes = nombre.strip().split()
    if len(partes) <= 1:
        return nombre.strip()[:50]
    if len(partes) == 2:
        return f"{partes[0][0]}. {partes[1]}"[:50]
    iniciales = " ".join(f"{p[0]}." for p in partes[:-2])
    return f"{iniciales} {partes[-2]} {partes[-1][0]}."[:50]


def _limpiar_titular(t: str) -> str:
    """Quita basura común del texto extraído como titular."""
    if not t:
        return ""
    t = t.strip()
    for prefijo in ["de ", "De ", "DE ", "Yape! ", "yape! "]:
        if t.startswith(prefijo):
            t = t[len(prefijo):]
    t = t.rstrip(".,!?¡¿:;")
    return t.strip()


# Caracteres permitidos en nombres: ASCII + acentos peruanos + ñ + asterisco
# El asterisco aparece en notif Yape como mascara de seguridad: "Angela Rob*"
NOMBRE_CHARS = r"A-Za-zÁÉÍÓÚáéíóúÑñ"
NOMBRE_INICIO = r"[A-ZÁÉÍÓÚÑ]"  # debe empezar con mayúscula


# =============================================================
# YAPE (BCP)
# =============================================================
def parse_yape(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower()
    txt = (texto or "").lower()

    es_yape = (
        "yape" in pkg or
        "yape" in tit or
        "yape" in txt or
        "te yape" in txt or
        "te envió un pago" in txt or
        "te envio un pago" in txt
    )
    if not es_yape:
        return None

    # Filtrar egresos
    es_egreso = any(x in txt for x in [
        "yapeaste",
        "enviaste un yape",
        "tu yape de",  # "Tu Yape de S/ X fue enviado"
    ])
    if es_egreso:
        return None

    monto = None
    titular = None
    codigo_op = None

    # Extraer monto: "S/ 1", "S/1.50", "S/. 100,50"
    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    # ============ PATRONES DE TITULAR EN ORDEN DE PRIORIDAD ============

    # Patrón 1: "DUILIO RESTUCCIA te envió un pago por S/ 1"
    # Patrón 2: "Angela Rob* te envió un pago por S/ 80"
    # Permite asterisco para mascaras parciales del nombre
    if not titular:
        m = re.search(
            rf"({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.*]+?)\s+te\s+envi[oó]\s+un\s+pago",
            texto,
            re.IGNORECASE,
        )
        if m:
            titular = m.group(1).strip()

    # Patrón 3: "Juan Carlos Perez Lopez te yapeó S/ 15.00"
    if not titular:
        m = re.search(
            rf"({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.]+?)\s+te\s+yape[oó]",
            texto,
            re.IGNORECASE,
        )
        if m:
            titular = m.group(1).strip()

    # Patrón 4: "Recibiste S/ X de Juan Perez"
    if not titular:
        m = re.search(
            rf"\bde\s+({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.]+?)(?:\s+con\s|$|[.,])",
            texto,
        )
        if m:
            titular = m.group(1).strip()

    # Si el texto empieza con "Yape! NOMBRE te ..." la regex ya lo captura,
    # pero el "Yape!" puede quedar pegado. Lo limpiamos al final.

    # ============ CODIGO DE OPERACION ============
    # "código 12345678" o "cód. 12345678"
    m = re.search(r"c[oó]d(?:igo)?\.?\s*:?\s*(\d{4,12})", texto, re.IGNORECASE)
    if m:
        codigo_op = m.group(1)

    # Fallback: cualquier numero largo NO sea el monto
    if not codigo_op:
        m = re.search(r"\b(\d{8,12})\b", texto)
        if m:
            # No confundir con el código de seguridad ("El cód. de seguridad es: 135")
            # ese son 3 digitos, no entran aquí
            codigo_op = m.group(1)

    if monto is None:
        return None

    titular = _limpiar_titular(titular)

    return {
        "metodo": "yape",
        "monto": monto,
        "moneda": "PEN",
        "titular": titular,
        "titular_corto": _abreviar_titular(titular),
        "codigo_operacion": codigo_op,
        "banco": None,
    }


# =============================================================
# PLIN (multi-banco)
# =============================================================
def parse_plin(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower()
    txt = (texto or "").lower()

    es_plin = (
        "plin" in pkg or "plin" in tit or "plin" in txt or
        "plineado" in txt or "plineo" in txt or
        ("interbank" in pkg and ("plineado" in txt or "plineo" in txt))
    )
    if not es_plin:
        return None

    # Filtrar egresos
    es_egreso = any(x in txt for x in [
        "plineaste",
        "enviaste",
        "transferiste",
    ])
    if es_egreso:
        return None

    monto = None
    titular = None
    codigo_op = None

    # Monto
    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    # Patrón Interbank: "Bertha Mariana Restuccia te ha plineado S/ 1.00"
    m = re.search(
        rf"({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.*]+?)\s+te\s+ha\s+plineado",
        texto,
        re.IGNORECASE,
    )
    if m:
        titular = m.group(1).strip()

    # Patrón alterno: "X te plineó" / "X te plineo"
    if not titular:
        m = re.search(
            rf"({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.*]+?)\s+te\s+plin[eé]?[oó]",
            texto,
            re.IGNORECASE,
        )
        if m:
            titular = m.group(1).strip()

    # Patrón "Recibiste S/ X de Y"
    if not titular:
        m = re.search(
            rf"\bde\s+({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.]+?)(?:\s+con\s|$|[.,]|\s+c[oó]digo)",
            texto,
        )
        if m:
            titular = m.group(1).strip()

    # Código de operación
    m = re.search(r"c[oó]digo\s*:?\s*(\w{4,15})", texto, re.IGNORECASE)
    if m:
        codigo_op = m.group(1)

    if not codigo_op:
        m = re.search(r"\b(\d{6,12})\b", texto)
        if m:
            codigo_op = m.group(1)

    if monto is None:
        return None

    titular = _limpiar_titular(titular)

    return {
        "metodo": "plin",
        "monto": monto,
        "moneda": "PEN",
        "titular": titular,
        "titular_corto": _abreviar_titular(titular),
        "codigo_operacion": codigo_op,
        "banco": None,
    }


# =============================================================
# BANCOS (transferencias)
# =============================================================
def _parse_banco_generico(package: str, titulo: str, texto: str,
                          claves_pkg: list[str], nombre_banco: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower() + " " + (texto or "").lower()

    if not any(k in pkg for k in claves_pkg) and not any(k in tit for k in claves_pkg):
        return None

    txt_low = (texto or "").lower()
    es_egreso = any(x in txt_low for x in [
        "consumiste", "consumo", "realizaste", "transferiste",
        "pagaste", "retiraste", "compraste", "enviaste",
        "plineaste", "yapeaste",
    ])
    if es_egreso:
        return None

    monto = None
    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    if monto is None:
        return None

    return {
        "metodo": "transferencia",
        "monto": monto,
        "moneda": "PEN",
        "titular": "",
        "titular_corto": "",
        "codigo_operacion": None,
        "banco": nombre_banco,
    }


def parse_bcp(package: str, titulo: str, texto: str) -> dict | None:
    return _parse_banco_generico(
        package, titulo, texto,
        claves_pkg=["bcp", "viabcp", "credito del per"],
        nombre_banco="BCP",
    )


def parse_bbva(package: str, titulo: str, texto: str) -> dict | None:
    return _parse_banco_generico(
        package, titulo, texto,
        claves_pkg=["bbva"],
        nombre_banco="BBVA",
    )


def parse_interbank(package: str, titulo: str, texto: str) -> dict | None:
    # OJO: si es Plin Interbank, el parser de Plin debe haber matcheado antes.
    # Aquí solo capturamos transferencias / abonos genéricos.
    return _parse_banco_generico(
        package, titulo, texto,
        claves_pkg=["interbank"],
        nombre_banco="Interbank",
    )


def parse_scotiabank(package: str, titulo: str, texto: str) -> dict | None:
    return _parse_banco_generico(
        package, titulo, texto,
        claves_pkg=["scotia"],
        nombre_banco="Scotiabank",
    )


# =============================================================
# REGISTRO Y DISPATCH
# =============================================================
# OJO al orden: Plin antes de Interbank, porque Plin Interbank tiene texto
# que contiene "interbank" en package. Si Interbank corre primero,
# matchearía como transferencia genérica perdiendo info de titular.
PARSERS = [
    parse_yape,
    parse_plin,
    parse_bcp,
    parse_bbva,
    parse_interbank,
    parse_scotiabank,
]


def parsear(package: str, titulo: str, texto: str) -> dict | None:
    if not texto:
        return None
    for parser in PARSERS:
        try:
            res = parser(package or "", titulo or "", texto)
            if res:
                return res
        except Exception:
            continue
    return None