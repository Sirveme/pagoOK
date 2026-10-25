"""Canal de correo bancario (zWeb-06).

Flujo de cada correo (llega del Email Worker de Cloudflare con el MIME crudo):
  1. Empresa por el código del destinatario egresos+<codigo>@bancos.pagook.pro.
     Código inexistente o inactivo -> 'descartado' (no se guarda el cuerpo).
  2. DKIM (dkimpy): firma válida de un dominio que esté en
     banco_config.dominios_correo. Si no -> 'rechazado' (no crea nada).
     Excepción: la confirmación de reenvío de Gmail (firmada por google.com)
     -> 'confirmacion_gmail' con su código, para mostrarlo en el panel.
  3. Parser del banco (parser_correo). 'procesado' crea o vincula el registro;
     'sin_formato' y 'descartado' no crean nada.

Sin duplicados entre celular y correo: misma empresa, tipo, monto y moneda,
hora a ±3 min (el número de operación, si coincide, lo hace seguro). Si ya
existe, el correo se vincula a ese registro y completa lo que falte.
Los egresos por correo nunca disparan pago.confirmado.
"""
from __future__ import annotations

import email
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from app.admin.models import Empresa
from app.api.models_correo import CorreoEmpresa, CorreoRaw
from app.api.models_multiempresa import BancoConfig
from app.api.models_webhook import PagoDetectado
from app.services.parser_correo import parsear_correo

logger = logging.getLogger("pagook")

PREFIJO_DIRECCION = "egresos"
ALFABETO_CODIGO = "abcdefghjkmnpqrstuvwxyz23456789"   # sin 0/o, 1/l/i
LARGO_CODIGO = 8
VENTANA_DUPLICADO = timedelta(minutes=3)
REMITENTE_GMAIL_CONFIRMACION = "forwarding-noreply@google.com"
_RE_DESTINATARIO = re.compile(r"^\s*<?\s*egresos\+([a-z0-9]{4,16})@", re.IGNORECASE)
_RE_CODIGO_GMAIL = re.compile(
    r"(?:\(#(\d{6,12})\)|c[oó]digo de confirmaci[oó]n\D{0,15}(\d{6,12})|confirmation code\D{0,15}(\d{6,12}))",
    re.IGNORECASE,
)


def dominio_correo() -> str:
    return os.getenv("CORREO_DOMINIO", "bancos.pagook.pro").strip().lower()


def direccion(codigo: str) -> str:
    return f"{PREFIJO_DIRECCION}+{codigo}@{dominio_correo()}"


# =============================================================
# Dirección por empresa
# =============================================================

def _nuevo_codigo(db: Session) -> str:
    while True:
        c = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LARGO_CODIGO))
        if db.query(CorreoEmpresa).filter(CorreoEmpresa.codigo == c).first() is None:
            return c


def correo_de_empresa(db: Session, empresa_id: int, crear: bool = True) -> CorreoEmpresa | None:
    ce = (
        db.query(CorreoEmpresa)
        .filter(CorreoEmpresa.empresa_id == empresa_id, CorreoEmpresa.activo == True)  # noqa: E712
        .order_by(CorreoEmpresa.id.desc())
        .first()
    )
    if ce is None and crear:
        ce = CorreoEmpresa(empresa_id=empresa_id, codigo=_nuevo_codigo(db), activo=True)
        db.add(ce)
        db.commit()
    return ce


def regenerar_correo(db: Session, empresa_id: int) -> CorreoEmpresa:
    """La dirección anterior deja de funcionar en el acto."""
    ahora = datetime.now(timezone.utc)
    db.query(CorreoEmpresa).filter(CorreoEmpresa.empresa_id == empresa_id).update(
        {CorreoEmpresa.activo: False}, synchronize_session=False)
    ce = CorreoEmpresa(empresa_id=empresa_id, codigo=_nuevo_codigo(db), activo=True, regenerado_en=ahora)
    db.add(ce)
    db.commit()
    return ce


# =============================================================
# MIME -> texto
# =============================================================

class _HtmlATexto(HTMLParser):
    _BLOQUES = {"p", "div", "tr", "br", "li", "table", "h1", "h2", "h3", "h4", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._omitir = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._omitir += 1
        elif tag in ("td", "th"):
            self.partes.append("\t")
        elif tag in self._BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._omitir = max(0, self._omitir - 1)
        elif tag in self._BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._omitir:
            self.partes.append(data)


def html_a_texto(html: str) -> str:
    p = _HtmlATexto()
    p.feed(html or "")
    crudo = "".join(p.partes).replace("\xa0", " ")
    lineas = []
    for linea in crudo.split("\n"):
        celdas = [re.sub(r"[ \t\r\f\v]+", " ", c).strip() for c in linea.split("\t")]
        celdas = [c for c in celdas if c]
        if celdas:
            lineas.append("\t".join(celdas))
    return "\n".join(lineas)


def cuerpo_a_texto(msg) -> str:
    """Texto legible del correo: text/plain si existe; si no, el HTML convertido."""
    plano = html = None
    for parte in msg.walk() if msg.is_multipart() else [msg]:
        if parte.get_content_maintype() == "multipart" or parte.get_filename():
            continue
        tipo = parte.get_content_type()
        try:
            contenido = parte.get_content()
        except Exception:
            continue
        if tipo == "text/plain" and plano is None:
            plano = contenido
        elif tipo == "text/html" and html is None:
            html = contenido
    if html:
        return html_a_texto(html)
    return (plano or "").strip()


# =============================================================
# DKIM
# =============================================================

def verificar_dkim(mime: bytes) -> list[str]:
    """Dominios (d=) de las firmas DKIM VÁLIDAS del mensaje. Consulta DNS."""
    import dkim

    msg = email.message_from_bytes(mime)
    n = len(msg.get_all("DKIM-Signature") or [])
    dominios = []
    for idx in range(n):
        try:
            d = dkim.DKIM(mime)
            if d.verify(idx=idx) and d.domain:
                dominios.append(d.domain.decode().lower())
        except Exception as exc:
            logger.info(f"DKIM firma {idx} no verificada: {type(exc).__name__}")
    return dominios


def _coincide(dominio: str, autorizado: str) -> bool:
    autorizado = autorizado.lower().strip().lstrip("@")
    return dominio == autorizado or dominio.endswith("." + autorizado)


def banco_por_dominios(db: Session, pais: str, dominios: list[str]) -> BancoConfig | None:
    for b in db.query(BancoConfig).filter(BancoConfig.pais == pais, BancoConfig.activo == True).all():  # noqa: E712
        if any(_coincide(d, a) for d in dominios for a in (b.dominios_correo or [])):
            return b
    return None


# =============================================================
# Duplicados celular <-> correo
# =============================================================

def buscar_duplicado(db: Session, empresa_id: int, tipo: str, monto, moneda: str, cuando: datetime,
                     codigo_operacion: str | None = None, solo_origen: str | None = None,
                     sin_correo: bool = False, sin_notificacion: bool = False) -> PagoDetectado | None:
    """Registro de la misma operación: empresa, tipo, monto, moneda, ±3 min.
    Si hay varios, gana el de igual número de operación; si no, el más cercano."""
    q = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == empresa_id, PagoDetectado.tipo == tipo,
        PagoDetectado.monto == monto, PagoDetectado.moneda == moneda,
        PagoDetectado.recibido_en >= cuando - VENTANA_DUPLICADO,
        PagoDetectado.recibido_en <= cuando + VENTANA_DUPLICADO,
    )
    if solo_origen:
        q = q.filter(PagoDetectado.origen == solo_origen)
    if sin_notificacion:
        q = q.filter(PagoDetectado.notificacion_raw_id.is_(None))
    candidatos = q.all()
    if sin_correo and candidatos:
        con_correo = {pid for (pid,) in db.query(CorreoRaw.pago_id).filter(
            CorreoRaw.pago_id.in_([c.id for c in candidatos]))}
        candidatos = [c for c in candidatos if c.id not in con_correo]
    if not candidatos:
        return None
    if codigo_operacion:
        for c in candidatos:
            if (c.codigo_operacion or "").lstrip("0") == codigo_operacion.lstrip("0"):
                return c
    return min(candidatos, key=lambda c: abs((c.recibido_en - cuando).total_seconds()))


def vincular_notificacion_con_correo(db: Session, empresa_id: int, parsed: dict,
                                     cuando: datetime) -> PagoDetectado | None:
    """Para el inbound: si el correo llegó ANTES, la notificación se vincula a
    ese registro en vez de crear otro. Solo registros de origen 'correo' que
    aún no tienen notificación."""
    return buscar_duplicado(
        db, empresa_id, parsed.get("tipo", "ingreso"), parsed["monto"], parsed.get("moneda", "PEN"), cuando,
        codigo_operacion=parsed.get("codigo_operacion"), solo_origen="correo", sin_notificacion=True,
    )


def _registrar(db: Session, empresa: Empresa, banco: BancoConfig, tipo: str, d: dict) -> tuple[PagoDetectado, bool]:
    existente = buscar_duplicado(db, empresa.id, tipo, d["monto"], d["moneda"], d["fecha_utc"],
                                 codigo_operacion=d.get("codigo_operacion"), sin_correo=True)
    if existente is not None:
        # Completa lo que el aviso del celular no traía.
        existente.codigo_operacion = existente.codigo_operacion or d.get("codigo_operacion")
        existente.tipo_egreso = existente.tipo_egreso or (d.get("tipo_egreso") if tipo == "egreso" else None)
        existente.destino = existente.destino or d.get("destino")
        existente.tarjeta_ultimos4 = existente.tarjeta_ultimos4 or d.get("ultimos4")
        existente.titular = existente.titular or d.get("contraparte")
        return existente, False
    contraparte = (d.get("contraparte") or "").strip()
    pago = PagoDetectado(
        empresa_id=empresa.id, notificacion_raw_id=None, metodo=d["metodo"], monto=d["monto"],
        moneda=d["moneda"], titular=contraparte, titular_corto=contraparte[:50],
        codigo_operacion=d.get("codigo_operacion"), banco=banco.nombre, tipo=tipo,
        tipo_egreso=d.get("tipo_egreso") if tipo == "egreso" else None,
        tarjeta_ultimos4=d.get("ultimos4"), destino=d.get("destino"),
        origen="correo", tipo_incierto=False, posible_interno=False, recibido_en=d["fecha_utc"],
    )
    db.add(pago)
    db.flush()
    return pago, True


# =============================================================
# Procesamiento
# =============================================================

def _verificar_y_parsear(db: Session, correo: CorreoRaw, empresa: Empresa, mime: bytes, verificar) -> None:
    """Pasos 2 y 3 (también los usa el reproceso desde /admin/bancos)."""
    dominios = verificar(mime) or []
    correo.dkim_ok = bool(dominios)
    correo.dkim_dominios = ",".join(dominios)[:300] or None

    if correo.remitente == REMITENTE_GMAIL_CONFIRMACION:
        if any(_coincide(d, "google.com") for d in dominios):
            m = _RE_CODIGO_GMAIL.search(f"{correo.asunto}\n{correo.cuerpo_texto}")
            correo.estado = "confirmacion_gmail"
            correo.codigo_confirmacion = next((g for g in m.groups() if g), None) if m else None
            correo.motivo = None if m else "No se encontró el código en el correo de Gmail"
        else:
            correo.estado, correo.motivo = "rechazado", "Confirmación de Gmail sin firma DKIM válida de google.com"
        return

    banco = banco_por_dominios(db, empresa.pais_codigo, dominios) if dominios else None
    if banco is None:
        correo.estado = "rechazado"
        correo.motivo = ("Sin firma DKIM válida" if not dominios else
                         f"Firmado por {', '.join(dominios)}, que no es dominio de ningún banco configurado")
        return
    correo.banco_clave = banco.clave
    r = parsear_correo(banco.clave, correo.asunto or "", correo.cuerpo_texto or "")
    correo.estado, correo.motivo = r["estado"], r.get("motivo")
    if r["estado"] != "procesado":
        return
    pago, creado = _registrar(db, empresa, banco, r["tipo"], r["datos"])
    correo.pago_id = pago.id
    if not creado:
        correo.motivo = f"Ya registrado (pago {pago.id}); se vinculó sin duplicar"
    # Un egreso nunca dispara pago.confirmado. Ingresos por correo: todavía no
    # hay formatos; cuando existan, emitir aquí solo si `creado` (una sola vez).


def procesar_correo(db: Session, destinatario: str, mime: bytes, verificar=None) -> CorreoRaw:
    verificar = verificar or verificar_dkim
    msg = email.message_from_bytes(mime, policy=policy.default)
    remitente = parseaddr(str(msg.get("From") or ""))[1].lower()[:200]
    asunto = str(msg.get("Subject") or "")[:1000]
    message_id = str(msg.get("Message-ID") or "").strip()[:300] or None

    m = _RE_DESTINATARIO.match(destinatario or "")
    ce = None
    if m:
        ce = (
            db.query(CorreoEmpresa)
            .filter(CorreoEmpresa.codigo == m.group(1).lower(), CorreoEmpresa.activo == True)  # noqa: E712
            .first()
        )
    if ce is None:
        correo = CorreoRaw(empresa_id=None, destinatario=(destinatario or "")[:200], remitente=remitente,
                           estado="descartado", motivo="Destinatario desconocido o desactivado")
        db.add(correo)
        db.commit()
        return correo

    if message_id:  # reintentos del Worker o de Gmail: no se procesa dos veces
        previo = (
            db.query(CorreoRaw)
            .filter(CorreoRaw.empresa_id == ce.empresa_id, CorreoRaw.message_id == message_id)
            .first()
        )
        if previo is not None:
            return previo

    empresa = db.get(Empresa, ce.empresa_id)
    correo = CorreoRaw(
        empresa_id=empresa.id, destinatario=destinatario[:200], remitente=remitente, asunto=asunto,
        cuerpo_texto=cuerpo_a_texto(msg), mime=mime, message_id=message_id, estado="descartado",
    )
    db.add(correo)
    db.flush()
    _verificar_y_parsear(db, correo, empresa, mime, verificar)
    db.commit()
    logger.info(f"Correo {correo.id} empresa={empresa.id}: {correo.estado}")
    return correo


def reprocesar_correo(db: Session, correo: CorreoRaw, verificar=None) -> CorreoRaw:
    """Vuelve a verificar y parsear (p. ej. tras agregar el dominio del banco)."""
    verificar = verificar or verificar_dkim
    if correo.empresa_id is None or not correo.mime:
        raise ValueError("Este correo no se puede reprocesar (sin empresa o ya purgado).")
    if correo.estado == "procesado":
        return correo
    empresa = db.get(Empresa, correo.empresa_id)
    _verificar_y_parsear(db, correo, empresa, bytes(correo.mime), verificar)
    db.commit()
    return correo


def ultima_confirmacion_gmail(db: Session, empresa_id: int, dias: int = 7) -> CorreoRaw | None:
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    return (
        db.query(CorreoRaw)
        .filter(CorreoRaw.empresa_id == empresa_id, CorreoRaw.estado == "confirmacion_gmail",
                CorreoRaw.codigo_confirmacion.isnot(None), CorreoRaw.recibido_en >= desde)
        .order_by(CorreoRaw.id.desc())
        .first()
    )


