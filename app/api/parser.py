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
    'Bertha Mariana Restuccia' -> 'B. M. Restuccia'"""
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
    # Quitar prefijos comunes
    for prefijo in ["de ", "De ", "DE "]:
        if t.startswith(prefijo):
            t = t[len(prefijo):]
    # Quitar puntuación final
    t = t.rstrip(".,!?¡¿:;")
    return t.strip()


# =============================================================
# YAPE (BCP)
# =============================================================

def parse_yape(package: str, titulo: str, texto: str) -> dict | None:
    if "yape" not in (package or "").lower() and "yape" not in (titulo or "").lower():
        return None

    # Filtrar egresos (no son pagos recibidos)
    txt_low = (texto or "").lower()
    es_egreso = any(x in txt_low for x in [
        "yapeaste",     # "Yapeaste S/ X a Y"
        "enviaste",
        "envíaste",
    ])
    if es_egreso:
        return None

    monto = None
    titular = None
    codigo_op = None

    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    # "X te yapeó"
    m = re.search(
        r"([A-Za-zÁÉÍÓÚáéíóúñÑ][\wÁÉÍÓÚáéíóúñÑ\s.]+?)\s+te\s+yape[oóò]",
        texto, re.IGNORECASE
    )
    if m:
        titular = m.group(1).strip()

    # "Recibiste S/ X de Y"
    if not titular:
        m = re.search(
            r"\bde\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s.]+?)(?:\s+con\s|$|[.,])",
            texto
        )
        if m:
            titular = m.group(1).strip()

    m = re.search(r"c[oó]digo\s*:?\s*(\d{6,12})", texto, re.IGNORECASE)
    if m:
        codigo_op = m.group(1)
    if not codigo_op:
        m = re.search(r"\b(\d{8,9})\b", texto)
        if m:
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

    # Detección de Plin en cualquiera de los campos
    es_plin = (
        "plin" in pkg or "plin" in tit or "plin" in txt or
        "plineado" in txt or "plineo" in txt or
        # Interbank usa "te ha plineado" como pattern fuerte
        ("interbank" in pkg and ("plineado" in txt or "plineo" in txt))
    )
    if not es_plin:
        return None

    # IMPORTANTE: filtrar egresos (no son pagos recibidos)
    es_egreso = any(x in txt for x in [
        "plineaste",   # "Plineaste S/ X a Y"
        "enviaste",    # "Enviaste un Plin"
        "envíaste",
        "transferiste",
    ])
    if es_egreso:
        return None

    monto = None
    titular = None
    codigo_op = None

    # Monto S/ X.YZ
    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    # Patrón Interbank: "Bertha Mariana Restuccia te ha plineado S/ 1.00"
    m = re.search(
        r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s.]+?)\s+te\s+ha\s+plineado",
        texto, re.IGNORECASE
    )
    if m:
        titular = m.group(1).strip()

    # Patrón alterno: "X te plineó" / "X te plineo"
    if not titular:
        m = re.search(
            r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s.]+?)\s+te\s+plin[eé]?[oó]",
            texto, re.IGNORECASE
        )
        if m:
            titular = m.group(1).strip()

    # Patrón "Recibiste S/ X de Y"
    if not titular:
        m = re.search(
            r"\bde\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s.]+?)(?:\s+con\s|$|[.,]|\s+c[oó]digo)",
            texto
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

    # Solo procesar entradas, no salidas
    txt_low = (texto or "").lower()
    es_egreso = any(x in txt_low for x in [
        "consumiste", "consumo", "realizaste", "transferiste",
        "pagaste", "retiraste", "compraste", "envíaste", "enviaste",
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
    # Acá solo capturamos transferencias / abonos genéricos.
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
