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


def _detectar_moneda(texto: str) -> str:
    """USD si aparece '$'; PEN si aparece 'S/'. Default PEN.

    Cuando no hay símbolo claro devuelve PEN, pero el resultado se marca como
    `tipo_incierto` río arriba para que el usuario lo revise.
    """
    return "USD" if "$" in (texto or "") else "PEN"


def _extraer_monto(texto: str) -> Decimal | None:
    """Extrae el primer monto en soles (S/) o dólares ($)."""
    txt = texto or ""
    m = re.search(r"S/\.?\s*([\d.,]+)", txt)
    if not m:
        m = re.search(r"\$\s*([\d.,]+)", txt)
    return _normalizar_monto(m.group(1)) if m else None


# Verbos que confirman la dirección del dinero. Si no aparece ninguno, el
# resultado queda con tipo_incierto=True (se guarda como 'ingreso' por defecto).
VERBOS_INGRESO = (
    "te ha plineado", "te ha yapeado", "te plineó", "te plineo",
    "te yapeó", "te yapeo", "te envió un pago", "te envio un pago",
    "recibiste", "abono en tu cuenta", "te enviaron", "te depositaron",
)
VERBOS_EGRESO = (
    "plineaste", "yapeaste", "enviaste", "transferiste", "pagaste",
    "consumiste", "retiraste", "compraste", "se realizó un pago",
    "se realizo un pago", "compra con tu tarjeta", "transferencia realizada",
    "transferencia enviada", "retiro",
)


def _clasificar_tipo(texto: str) -> tuple[str, bool]:
    """Devuelve (tipo, incierto) según los verbos presentes en el texto.

    - Egreso si hay un verbo en primera persona activa ("Plineaste", "Pagaste").
    - Ingreso si hay un verbo en pasiva / recepción ("te ha plineado", "recibiste").
    - Si no matchea ninguno: ('ingreso', True) — default seguro, marcado a revisión.
    """
    txt = (texto or "").lower()
    if any(v in txt for v in VERBOS_EGRESO):
        return "egreso", False
    if any(v in txt for v in VERBOS_INGRESO):
        return "ingreso", False
    return "ingreso", True


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

    # Dirección del dinero (ingreso/egreso) por verbos. Los egresos antes se
    # descartaban; ahora se guardan para el Flujo de Caja y los resúmenes.
    tipo, tipo_incierto = _clasificar_tipo(texto)
    es_egreso = tipo == "egreso"

    monto = _extraer_monto(texto)
    moneda = _detectar_moneda(texto)
    titular = None
    codigo_op = None

    # ============ TITULAR EN EGRESOS: "Yapeaste S/ X a NOMBRE" ============
    if es_egreso:
        m = re.search(
            rf"yapeaste\s+S/\.?\s*[\d.,]+\s+a\s+({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.*]+?)(?:\.|,|$)",
            texto,
            re.IGNORECASE,
        )
        if m:
            titular = m.group(1).strip()

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
        "moneda": moneda,
        "titular": titular,
        "titular_corto": _abreviar_titular(titular),
        "codigo_operacion": codigo_op,
        "banco": None,
        "tipo": tipo,
        "tipo_incierto": tipo_incierto,
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

    # Dirección del dinero por verbos (ingreso/egreso). Los egresos ahora se
    # guardan con su titular destino.
    tipo, tipo_incierto = _clasificar_tipo(texto)
    es_egreso = tipo == "egreso"

    monto = _extraer_monto(texto)
    moneda = _detectar_moneda(texto)
    titular = None
    codigo_op = None

    # ============ TITULAR EN EGRESOS: "Plineaste S/ X a NOMBRE" ============
    if es_egreso:
        m = re.search(
            rf"plineaste\s+S/\.?\s*[\d.,]+\s+a\s+({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.*]+?)(?:\.|,|$)",
            texto,
            re.IGNORECASE,
        )
        if m:
            titular = m.group(1).strip()

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
        "moneda": moneda,
        "titular": titular,
        "titular_corto": _abreviar_titular(titular),
        "codigo_operacion": codigo_op,
        "banco": None,
        "tipo": tipo,
        "tipo_incierto": tipo_incierto,
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

    tipo, tipo_incierto = _clasificar_tipo(texto)

    monto = _extraer_monto(texto)
    if monto is None:
        return None

    return {
        "metodo": "transferencia",
        "monto": monto,
        "moneda": _detectar_moneda(texto),
        "titular": "",
        "titular_corto": "",
        "codigo_operacion": None,
        "banco": nombre_banco,
        "tipo": tipo,
        "tipo_incierto": tipo_incierto,
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
# BILLETERAS ADICIONALES: BIM, P51 (ex Tunki), PIX
# =============================================================
def _parse_billetera_generica(
    package: str, titulo: str, texto: str,
    metodo: str, claves: list[str], patrones_egreso: list[str],
) -> dict | None:
    pkg = (package or "").lower()
    tit = (titulo or "").lower()
    txt = (texto or "").lower()

    if not any(k in pkg for k in claves) and \
       not any(k in tit for k in claves) and \
       not any(k in txt for k in claves):
        return None

    monto = None
    m = re.search(r"S/\.?\s*([\d.,]+)", texto)
    if m:
        monto = _normalizar_monto(m.group(1))
    if monto is None:
        return None

    es_egreso = any(x in txt for x in patrones_egreso)
    if es_egreso:
        tipo, tipo_incierto = "egreso", False
    else:
        tipo, tipo_incierto = _clasificar_tipo(texto)

    # Intento básico de titular: "X te ..." al inicio
    titular = ""
    m = re.search(
        rf"({NOMBRE_INICIO}[{NOMBRE_CHARS}\s.*]+?)\s+te\s+",
        texto,
    )
    if m:
        titular = _limpiar_titular(m.group(1).strip())

    return {
        "metodo": metodo,
        "monto": monto,
        "moneda": _detectar_moneda(texto),
        "titular": titular,
        "titular_corto": _abreviar_titular(titular),
        "codigo_operacion": None,
        "banco": None,
        "tipo": tipo,
        "tipo_incierto": tipo_incierto,
    }


def parse_bim(package: str, titulo: str, texto: str) -> dict | None:
    return _parse_billetera_generica(
        package, titulo, texto,
        metodo="bim",
        claves=["bim de", "transferencia bim", "te bimearon", "billetera bim", " bim "],
        patrones_egreso=["enviaste", "transferiste", "pagaste"],
    )


def parse_p51(package: str, titulo: str, texto: str) -> dict | None:
    # P51 = nombre nuevo de Tunki
    return _parse_billetera_generica(
        package, titulo, texto,
        metodo="p51",
        claves=["p51", "tunki", "tunkiaste"],
        patrones_egreso=["enviaste", "transferiste", "pagaste", "tunkiaste"],
    )


def parse_pix(package: str, titulo: str, texto: str) -> dict | None:
    return _parse_billetera_generica(
        package, titulo, texto,
        metodo="pix",
        claves=["pix", "te enviaron pix"],
        patrones_egreso=["enviaste", "transferiste", "pagaste"],
    )


# =============================================================
# TARJETA (compras y pagos recurrentes con tarjeta débito/crédito)
# =============================================================
def parse_tarjeta(package: str, titulo: str, texto: str) -> dict | None:
    """Compras y pagos con tarjeta. Siempre egreso.

    Ejemplos:
      "Se realizó un pago recurrente de $ 100.00 en ANTHROPIC* CLAUDE SUB
       con tu Tarjeta de Débito"  -> egreso, USD, titular='ANTHROPIC* CLAUDE SUB'
      "Compra con tu tarjeta por S/ 50.00 en PLAZA VEA"
    """
    txt = (texto or "").lower()
    señales = (
        "con tu tarjeta", "pago recurrente", "compra con tu tarjeta",
        "consumo con tu tarjeta", "tarjeta de débito", "tarjeta de debito",
        "tarjeta de crédito", "tarjeta de credito", "se realizó un pago",
        "se realizo un pago",
    )
    if not any(s in txt for s in señales):
        return None

    monto = _extraer_monto(texto)
    if monto is None:
        return None
    moneda = _detectar_moneda(texto)

    # Comercio como titular: "... en COMERCIO con tu tarjeta" / "... en COMERCIO"
    comercio = ""
    m = re.search(r"\ben\s+(.+?)\s+con\s+tu\s+tarjeta", texto, re.IGNORECASE)
    if not m:
        m = re.search(r"\ben\s+(.+?)(?:\.|$)", texto, re.IGNORECASE)
    if m:
        comercio = m.group(1).strip().rstrip(".,")

    return {
        "metodo": "tarjeta",
        "monto": monto,
        "moneda": moneda,
        "titular": comercio,
        "titular_corto": (comercio or "")[:50],
        "codigo_operacion": None,
        "banco": None,
        "tipo": "egreso",
        "tipo_incierto": False,
    }


# =============================================================
# REGISTRO Y DISPATCH
# =============================================================
# OJO al orden: Plin antes de Interbank, porque Plin Interbank tiene texto
# que contiene "interbank" en package. Si Interbank corre primero,
# matchearía como transferencia genérica perdiendo info de titular.
PARSERS = [
    parse_yape,
    parse_plin,
    parse_tarjeta,   # antes de los bancos: captura compras/pagos con tarjeta (USD)
    parse_bim,
    parse_p51,
    parse_pix,
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