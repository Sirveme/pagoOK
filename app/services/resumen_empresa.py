"""Cálculos de resumen diario de una empresa (ingresos, egresos, negocio).

Distingue el movimiento TOTAL del movimiento "del negocio", que excluye las
transferencias entre las cuentas propias del dueño confirmadas como internas.

Decisión de diseño: NO se sobrescribe `tipo` a 'interno' al confirmar, porque
eso borraría la dirección ingreso/egreso. La exclusión del negocio se hace con
`posible_interno=true AND confirmacion_usuario='confirmado_interno'`, así se
pueden calcular las dos filas del resumen ("Tu día" y "Del negocio").
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.api.models_webhook import PagoDetectado

TZ_LIMA = ZoneInfo("America/Lima")
CERO = Decimal("0.00")


def _rango_utc_del_dia(fecha: date) -> tuple[datetime, datetime]:
    """Convierte un día local de Lima al rango [inicio, fin] en UTC naive.

    `recibido_en` se guarda en UTC naive, por eso se devuelve sin tzinfo.
    """
    inicio = (
        datetime.combine(fecha, time.min, tzinfo=TZ_LIMA)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    fin = (
        datetime.combine(fecha, time.max, tzinfo=TZ_LIMA)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    return inicio, fin


def _es_interno_confirmado(pago: PagoDetectado) -> bool:
    return bool(getattr(pago, "posible_interno", False)) and (
        getattr(pago, "confirmacion_usuario", None) == "confirmado_interno"
    )


def _es_interno_sin_confirmar(pago: PagoDetectado) -> bool:
    return bool(getattr(pago, "posible_interno", False)) and (
        getattr(pago, "confirmacion_usuario", None) is None
    )


def calcular_resumen_dia(empresa_id: int, fecha: date, session: Session) -> dict:
    """Resumen del día para una empresa.

    Devuelve montos Decimal y contadores int:
      total_ingresos, total_egresos, diferencia,
      ingresos_negocio, egresos_negocio, diferencia_negocio,
      cuenta_ingresos, cuenta_egresos, cuenta_internos_sin_confirmar
    """
    inicio, fin = _rango_utc_del_dia(fecha)

    pagos = (
        session.query(PagoDetectado)
        .filter(
            PagoDetectado.empresa_id == empresa_id,
            PagoDetectado.recibido_en >= inicio,
            PagoDetectado.recibido_en <= fin,
        )
        .all()
    )

    total_ingresos = CERO
    total_egresos = CERO
    ingresos_negocio = CERO
    egresos_negocio = CERO
    cuenta_ingresos = 0
    cuenta_egresos = 0
    cuenta_internos_sin_confirmar = 0

    for p in pagos:
        monto = p.monto if p.monto is not None else CERO
        tipo = (p.tipo or "ingreso").lower()
        excluir_del_negocio = _es_interno_confirmado(p)
        if _es_interno_sin_confirmar(p):
            cuenta_internos_sin_confirmar += 1

        if tipo == "egreso":
            total_egresos += monto
            cuenta_egresos += 1
            if not excluir_del_negocio:
                egresos_negocio += monto
        else:  # 'ingreso' (default); 'interno'/'externo_confirmado' cuentan como ingreso
            total_ingresos += monto
            cuenta_ingresos += 1
            if not excluir_del_negocio:
                ingresos_negocio += monto

    return {
        "total_ingresos": total_ingresos,
        "total_egresos": total_egresos,
        "diferencia": total_ingresos - total_egresos,
        "ingresos_negocio": ingresos_negocio,
        "egresos_negocio": egresos_negocio,
        "diferencia_negocio": ingresos_negocio - egresos_negocio,
        "cuenta_ingresos": cuenta_ingresos,
        "cuenta_egresos": cuenta_egresos,
        "cuenta_internos_sin_confirmar": cuenta_internos_sin_confirmar,
    }


def resumen_serializable(resumen: dict) -> dict:
    """Convierte los Decimal del resumen a float para JSON."""
    return {
        k: (float(v) if isinstance(v, Decimal) else v)
        for k, v in resumen.items()
    }
