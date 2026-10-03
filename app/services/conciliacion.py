"""Consulta de pagos (pull): "¿existe este pago?" con nivel de confianza.

PagoOK NO decide quién es el cliente ni qué se vendió. Solo responde qué tan
seguro está de que EL PAGO existe en la empresa de la credencial que pregunta.

Comprador vs pagador:
  - PagoOK solo conoce al TITULAR DEL MEDIO DE PAGO (lo que trae la push:
    "Fulano te yapeó"). Eso es `contraparte`.
  - PagoOK nunca conoce al COMPRADOR (RUC, razón social): lo sabe el sistema
    que pregunta. `nombre_pagador_declarado` se compara contra la contraparte,
    nunca contra el comprador (una empresa puede pagar con el Yape del dueño).

Jerarquía de desempate (de más fuerte a más débil):
  1. codigo_operacion exacto (+ monto)        → alta
  2. monto + ventana + nombre del pagador      → alta (si solo uno calza el nombre)
  3. monto + ventana, candidato único          → alta
  4. monto + ventana, varios candidatos        → media (se devuelven todos)
  5. nada calza                                → sin_coincidencia

Idempotencia de uso: un pago ya usado (tabla pago_uso) o reclamado por otra
credencial NO cuenta como candidato; se informa aparte en `pagos_ya_usados`
para que el sistema que pregunta detecte un voucher reutilizado.

SEGURIDAD: toda consulta filtra por `empresa_id` de la credencial, resuelto en
el backend. Ningún parámetro del cliente puede cambiar la empresa.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.models_pago_uso import PagoUso
from app.api.models_webhook import PagoDetectado
from app.services.eventos import (
    CANALES,
    TZ_LIMA,
    a_iso_lima,
    canal_de_metodo,
    institucion_de,
    monto_texto,
)

NIVEL_ALTA = "alta"
NIVEL_MEDIA = "media"
NIVEL_SIN = "sin_coincidencia"

ACCION_POR_NIVEL = {
    NIVEL_ALTA: "aceptar",
    NIVEL_MEDIA: "confirmar_manualmente",
    NIVEL_SIN: "pedir_voucher",
}

MAX_CANDIDATOS = 50


# =============================================================
# Comparación de nombres (pagador declarado vs contraparte capturada)
# =============================================================

_PALABRAS_VACIAS = {"de", "del", "la", "las", "los", "y", "e"}


def _tokens(nombre: str | None) -> list[str]:
    if not nombre:
        return []
    s = unicodedata.normalize("NFKD", nombre)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return [t for t in re.split(r"[^a-z0-9ñ]+", s) if t and t not in _PALABRAS_VACIAS]


def _token_calza(t: str, declarados: list[str]) -> str | None:
    """'completo' si el token calza entero o por prefijo (nombres truncados por
    la billetera, ej. 'pere' ~ 'perez'); 'inicial' si es una inicial; None si no."""
    if len(t) == 1:
        return "inicial" if any(d.startswith(t) for d in declarados) else None
    for d in declarados:
        if d == t or (len(t) >= 3 and d.startswith(t)) or (len(d) >= 3 and t.startswith(d)):
            return "completo"
    return None


def nombre_coincide(capturado: str | None, declarado: str | None) -> bool | None:
    """True = coincide razonablemente; False = no coincide; None = no concluyente
    (falta uno de los nombres, o solo se puede comparar un nombre de pila)."""
    cap, dec = _tokens(capturado), _tokens(declarado)
    if not cap or not dec:
        return None
    calces = [_token_calza(t, dec) for t in cap]
    completos = calces.count("completo")
    total = len([c for c in calces if c])
    if total == 0:
        return False
    requeridos = min(2, len(cap), len(dec))
    if completos >= 1 and total >= requeridos:
        return True if requeridos >= 2 else None
    return False


# =============================================================
# Serialización
# =============================================================

def _a_utc_naive(dt: datetime) -> datetime:
    """`recibido_en` se guarda como UTC naive. Una fecha declarada sin zona se
    interpreta como hora de Lima (los clientes declaran en hora local)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_LIMA)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def serializar_pago(p: PagoDetectado, referencia: datetime | None = None) -> dict:
    d = {
        "pago_id": p.id,
        "monto": monto_texto(p.monto),
        "moneda": p.moneda or "PEN",
        "canal": canal_de_metodo(p.metodo),
        "institucion": institucion_de(p),
        "contraparte": p.titular or "",
        "codigo_operacion": p.codigo_operacion or None,
        "fecha_hora": a_iso_lima(p.recibido_en),
    }
    if referencia is not None and p.recibido_en is not None:
        d["diferencia_minutos"] = round((p.recibido_en - referencia).total_seconds() / 60, 1)
    return d


def _serializar_uso(p: PagoDetectado, uso: PagoUso | None, referencia: datetime) -> dict:
    d = serializar_pago(p, referencia)
    if uso is not None:
        d["estado"] = "usado"
        d["uso"] = {
            "sistema": uso.sistema_que_uso,
            "referencia_externa": uso.referencia_externa,
            "usado_en": a_iso_lima(uso.usado_en),
        }
    else:
        d["estado"] = "reclamado_por_otro_sistema"
    return d


# =============================================================
# Consulta
# =============================================================

def _respuesta(nivel: str, motivo: str, mensaje: str, criterios: dict, *,
               pago: dict | None = None, candidatos: list | None = None,
               ya_usados: list | None = None) -> dict:
    return {
        "nivel": nivel,
        "motivo": motivo,
        "accion_sugerida": ACCION_POR_NIVEL[nivel],
        "mensaje": mensaje,
        "pago": pago,
        "candidatos": candidatos or [],
        "pagos_ya_usados": ya_usados or [],
        "criterios": criterios,
    }


def consultar_pago(
    db: Session,
    *,
    empresa_id: int,
    cuenta_id: int,
    monto: Decimal,
    fecha_hora_declarada: datetime,
    ventana_minutos: int = 15,
    canal: str | None = None,
    nombre_pagador_declarado: str | None = None,
    codigo_operacion: str | None = None,
) -> dict:
    declarada = _a_utc_naive(fecha_hora_declarada)
    desde = declarada - timedelta(minutes=ventana_minutos)
    hasta = declarada + timedelta(minutes=ventana_minutos)
    codigo = (codigo_operacion or "").strip() or None
    nombre = (nombre_pagador_declarado or "").strip() or None

    criterios = {
        "monto": monto_texto(monto),
        "desde": a_iso_lima(desde),
        "hasta": a_iso_lima(hasta),
        "canal": canal,
        "uso_codigo_operacion": codigo is not None,
        "uso_nombre_pagador": nombre is not None,
    }

    # Base: SIEMPRE la empresa de la credencial. Solo ingresos (un egreso o una
    # transferencia entre cuentas propias confirmada no es pago de un cliente).
    base = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == empresa_id,
        PagoDetectado.tipo == "ingreso",
        PagoDetectado.monto == monto,
        or_(PagoDetectado.confirmacion_usuario.is_(None),
            PagoDetectado.confirmacion_usuario != "confirmado_interno"),
    )
    if canal == "otro":
        base = base.filter(PagoDetectado.metodo.notin_([c for c in CANALES if c != "otro"]))
    elif canal:
        base = base.filter(PagoDetectado.metodo == canal)

    por_codigo = base.filter(PagoDetectado.codigo_operacion == codigo).limit(MAX_CANDIDATOS).all() if codigo else []
    en_ventana = (
        base.filter(PagoDetectado.recibido_en >= desde, PagoDetectado.recibido_en <= hasta)
        .limit(MAX_CANDIDATOS)
        .all()
    )

    todos = {p.id: p for p in [*por_codigo, *en_ventana]}
    usos = {
        u.pago_id: u
        for u in db.query(PagoUso).filter(PagoUso.pago_id.in_(list(todos))).all()
    } if todos else {}

    def disponible(p: PagoDetectado) -> bool:
        return p.id not in usos and (p.reclamado_por is None or p.reclamado_por == cuenta_id)

    cercania = lambda p: abs((p.recibido_en - declarada).total_seconds()) if p.recibido_en else 1e12  # noqa: E731
    ya_usados = [
        _serializar_uso(p, usos.get(p.id), declarada)
        for p in sorted(todos.values(), key=cercania) if not disponible(p)
    ]

    # 1) Código de operación exacto: el desempate más fuerte.
    if codigo:
        hits = [p for p in por_codigo if disponible(p)]
        if len(hits) == 1:
            return _respuesta(NIVEL_ALTA, "codigo_operacion",
                              "Pago encontrado por código de operación.", criterios,
                              pago=serializar_pago(hits[0], declarada), ya_usados=ya_usados)
        if len(hits) > 1:
            return _respuesta(NIVEL_MEDIA, "codigo_operacion_repetido",
                              "Varios pagos con el mismo código de operación; confírmalo manualmente.",
                              criterios, candidatos=[serializar_pago(p, declarada) for p in hits],
                              ya_usados=ya_usados)

    # Candidatos por monto + ventana. Si vino un código, se descartan los que
    # tienen OTRO código (contradicción); los que no tienen código siguen.
    candidatos = sorted(
        (p for p in en_ventana
         if disponible(p) and not (codigo and p.codigo_operacion and p.codigo_operacion.strip() != codigo)),
        key=cercania,
    )

    if not candidatos:
        if ya_usados:
            return _respuesta(NIVEL_SIN, "ya_usado",
                              "El pago que calza ya fue usado para otra operación. Pide el voucher.",
                              criterios, ya_usados=ya_usados)
        return _respuesta(NIVEL_SIN, "sin_pagos",
                          "No hay ningún pago que calce con el monto y la hora declarados. Pide el voucher.",
                          criterios)

    def con_nombre(p):
        d = serializar_pago(p, declarada)
        if nombre:
            d["coincide_nombre"] = nombre_coincide(p.titular, nombre)
        return d

    # 2) Nombre del pagador como desempate.
    if nombre:
        evaluacion = {p.id: nombre_coincide(p.titular, nombre) for p in candidatos}
        calzan = [p for p in candidatos if evaluacion[p.id] is True]
        if len(calzan) == 1:
            return _respuesta(NIVEL_ALTA, "monto_ventana_nombre",
                              "Pago encontrado por monto, hora y nombre del pagador.", criterios,
                              pago=con_nombre(calzan[0]), ya_usados=ya_usados)
        if len(candidatos) == 1 and evaluacion[candidatos[0].id] is False:
            return _respuesta(NIVEL_MEDIA, "nombre_no_coincide",
                              "Hay un pago con ese monto y hora, pero el nombre del pagador no coincide. "
                              "Confírmalo manualmente.",
                              criterios, candidatos=[con_nombre(candidatos[0])], ya_usados=ya_usados)

    # 3) Candidato único.
    if len(candidatos) == 1:
        return _respuesta(NIVEL_ALTA, "candidato_unico",
                          "Pago encontrado: es el único con ese monto en la ventana de tiempo.",
                          criterios, pago=con_nombre(candidatos[0]), ya_usados=ya_usados)

    # 4) Varios candidatos sin desempate.
    return _respuesta(NIVEL_MEDIA, "varios_candidatos",
                      "Hay varios pagos posibles; confirma manualmente cuál corresponde.",
                      criterios, candidatos=[con_nombre(p) for p in candidatos], ya_usados=ya_usados)


# =============================================================
# Marcar como usado
# =============================================================

class PagoNoEncontrado(Exception):
    pass


class PagoYaUsado(Exception):
    def __init__(self, mensaje: str, uso: dict | None = None):
        super().__init__(mensaje)
        self.uso = uso


def _uso_dict(u: PagoUso) -> dict:
    return {
        "pago_id": u.pago_id,
        "sistema": u.sistema_que_uso,
        "referencia_externa": u.referencia_externa,
        "usado_en": a_iso_lima(u.usado_en),
    }


def marcar_usado(db: Session, *, cuenta, pago_id: int, referencia_externa: str,
                 sistema: str | None = None) -> dict:
    """Registra que `cuenta` usó el pago para `referencia_externa`.

    Idempotente para la MISMA credencial + MISMA referencia (devuelve el uso
    existente). Cualquier otro intento sobre un pago ya usado → PagoYaUsado.
    Atómico: UNIQUE(pago_id) en pago_uso + el reclamo condicional del pago.
    """
    referencia_externa = referencia_externa.strip()
    pago = db.query(PagoDetectado).filter(
        PagoDetectado.id == pago_id,
        PagoDetectado.empresa_id == cuenta.empresa_id,
    ).first()
    if pago is None:
        raise PagoNoEncontrado()

    def _existente() -> dict:
        db.rollback()
        u = db.query(PagoUso).filter(PagoUso.pago_id == pago_id).first()
        if u is None:
            raise PagoYaUsado("Pago ya reclamado por otro sistema")
        if u.cuenta_api_id == cuenta.id and u.referencia_externa == referencia_externa:
            return {**_uso_dict(u), "usado": True, "ya_estaba_usado": True}
        raise PagoYaUsado(
            f"Pago ya usado por {u.sistema_que_uso} (referencia {u.referencia_externa})",
            _uso_dict(u),
        )

    # Reclamo condicional (mismo criterio que POST /reclamar): así el pago
    # también deja de aparecer como disponible en GET /api/v1/pagos.
    res = db.query(PagoDetectado).filter(
        PagoDetectado.id == pago_id,
        PagoDetectado.empresa_id == cuenta.empresa_id,
        or_(PagoDetectado.reclamado_por.is_(None), PagoDetectado.reclamado_por == cuenta.id),
    ).update(
        {PagoDetectado.reclamado_por: cuenta.id,
         PagoDetectado.reclamado_en: datetime.now(timezone.utc).replace(tzinfo=None)},
        synchronize_session=False,
    )
    if res != 1:
        return _existente()

    uso = PagoUso(
        pago_id=pago_id,
        empresa_id=cuenta.empresa_id,
        cuenta_api_id=cuenta.id,
        sistema_que_uso=(sistema or "").strip() or cuenta.nombre,
        referencia_externa=referencia_externa,
    )
    db.add(uso)
    try:
        db.commit()
    except IntegrityError:
        return _existente()
    db.refresh(uso)
    return {**_uso_dict(uso), "usado": True, "ya_estaba_usado": False}
