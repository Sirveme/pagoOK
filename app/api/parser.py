"""Parser de texto de notificaciones bancarias.

Cada parseador toma `texto` (string crudo de la notificación) y devuelve:
  dict con keys: metodo, monto, moneda, titular, codigo_operacion, banco
  o None si no detecta nada.

Probamos cada parser en orden hasta que uno haga match. El orden importa:
los más específicos primero, los más genéricos al final.
"""
import re
from decimal import Decimal


def _normalizar_monto(s: str) -> Decimal | None:
    """Convierte '1,234.56' o '1.234,56' a Decimal."""
    if not s:
        return None
    s = s.strip().replace(" ", "")
    # Si tiene ambos separadores, el último es el decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Solo coma: asumimos decimal solo si hay 2 dígitos después
        if re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return Decimal(s)
    except Exception:
        return None


def _abreviar_titular(nombre: str) -> str:
    """'JUAN CARLOS PEREZ LOPEZ' -> 'J. C. PEREZ L.'"""
    if not nombre:
        return ""
    partes = nombre.strip().split()
    if len(partes) <= 1:
        return nombre.strip()[:50]
    if len(partes) == 2:
        return f"{partes[0][0]}. {partes[1]}"[:50]
    # Apellido = última palabra, nombres = todas las demás
    iniciales = " ".join(f"{p[0]}." for p in partes[:-2])
    return f"{iniciales} {partes[-2]} {partes[-1][0]}."[:50]


# =============================================================
# PARSERS POR APP / BANCO
# =============================================================

def parse_yape(package: str, titulo: str, texto: str) -> dict | None:
    if "yape" not in (package or "").lower() and "yape" not in (titulo or "").lower():
        return None

    # Patrón típico: "Juan Carlos Perez te yapeó S/ 15.00"
    # Variación:    "Recibiste S/ 15.00 de Juan Perez"
    # Variación:    "Yape! Recibiste S/15 de Juan"

    monto = None
    titular = None
    codigo_op = None

    # Buscar monto: S/ 15.00, S/15.00, S/. 15
    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    # Patrón 1: "X te yapeó"
    m = re.search(r"([A-Za-zÁÉÍÓÚáéíóúñÑ][\wÁÉÍÓÚáéíóúñÑ\s.]+?)\s+te\s+yape[oóò]", texto, re.IGNORECASE)
    if m:
        titular = m.group(1).strip()

    # Patrón 2: "de X"
    if not titular:
        m = re.search(r"\bde\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s.]+?)(?:\s+con\s|$|[.,])", texto)
        if m:
            titular = m.group(1).strip()

    # Código de operación (suele ser 8 dígitos al final o "código X")
    m = re.search(r"c[oó]digo\s*:?\s*(\d{6,12})", texto, re.IGNORECASE)
    if m:
        codigo_op = m.group(1)
    if not codigo_op:
        m = re.search(r"\b(\d{8,9})\b", texto)
        if m:
            codigo_op = m.group(1)

    if monto is None:
        return None

    return {
        "metodo": "yape",
        "monto": monto,
        "moneda": "PEN",
        "titular": titular or "",
        "titular_corto": _abreviar_titular(titular) if titular else "",
        "codigo_operacion": codigo_op,
        "banco": None,
    }


def parse_plin(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower()
    txt = (texto or "").lower()
    if "plin" not in pkg and "plin" not in tit and "plin" not in txt:
        return None

    monto = None
    titular = None
    codigo_op = None

    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))

    # "Recibiste S/ 30 de JUAN PEREZ"
    m = re.search(r"\bde\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s.]+?)(?:\s+con\s|$|[.,]|\s+c[oó]digo)", texto)
    if m:
        titular = m.group(1).strip()

    # Plin a veces dice "código: XXXXX"
    m = re.search(r"c[oó]digo\s*:?\s*(\w{4,15})", texto, re.IGNORECASE)
    if m:
        codigo_op = m.group(1)
    if not codigo_op:
        m = re.search(r"\b(\d{6,12})\b", texto)
        if m:
            codigo_op = m.group(1)

    if monto is None:
        return None

    return {
        "metodo": "plin",
        "monto": monto,
        "moneda": "PEN",
        "titular": titular or "",
        "titular_corto": _abreviar_titular(titular) if titular else "",
        "codigo_operacion": codigo_op,
        "banco": None,
    }


def parse_bcp(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower() + " " + (texto or "").lower()
    if "bcp" not in pkg and "credito del per" not in tit and "viabcp" not in pkg:
        return None

    # BCP suele decir: "Se realizó un abono en tu cuenta soles X***1234 por S/ 100.00"
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
        "banco": "BCP",
    }


def parse_bbva(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower() + " " + (texto or "").lower()
    if "bbva" not in pkg and "bbva" not in tit:
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
        "banco": "BBVA",
    }


def parse_interbank(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower() + " " + (texto or "").lower()
    if "interbank" not in pkg and "interbank" not in tit:
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
        "banco": "Interbank",
    }


def parse_scotiabank(package: str, titulo: str, texto: str) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower() + " " + (texto or "").lower()
    if "scotia" not in pkg and "scotia" not in tit:
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
        "banco": "Scotiabank",
    }


PARSERS = [parse_yape, parse_plin, parse_bcp, parse_bbva, parse_interbank, parse_scotiabank]


def parsear(package: str, titulo: str, texto: str) -> dict | None:
    """Itera todos los parsers, devuelve el primer match."""
    if not texto:
        return None
    for parser in PARSERS:
        try:
            res = parser(package or "", titulo or "", texto)
            if res:
                return res
        except Exception as e:
            continue
    return None
