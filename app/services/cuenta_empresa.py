"""Estado de la cuenta de una empresa: prueba (7 + 15 días), activa, vencida,
suspendida.

- Sin cron: el paso de 'prueba' a 'vencida' es PEREZOSO, ocurre al leer la
  empresa (refrescar_estado), cuando prueba_fin ya pasó.
- perusistemas es SIEMPRE 'activa'.
- Con la cuenta vencida (o suspendida) la captura SIGUE funcionando y los
  pagos se guardan: aquí solo se decide qué queda en solo lectura.
- No hay cobros: el paso a 'activa' lo hace Duilio con scripts/gestion_empresas.py.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

PRUEBA = "prueba"
ACTIVA = "activa"
VENCIDA = "vencida"
SUSPENDIDA = "suspendida"
ESTADOS = (PRUEBA, ACTIVA, VENCIDA, SUSPENDIDA)

# Estados en los que el panel queda en solo lectura, la consulta responde 402
# y los webhooks se retienen en cola.
ESTADOS_BLOQUEADOS = (VENCIDA, SUSPENDIDA)

DIAS_PRUEBA = 7
DIAS_AMPLIACION = 15
DIAS_AVISO_AMPLIAR = 2
SLUG_PRINCIPAL = "perusistemas"


class CuentaError(Exception):
    """Operación no permitida sobre la cuenta (409 con motivo legible)."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def es_principal(empresa) -> bool:
    return (empresa.slug or "").lower() == SLUG_PRINCIPAL


def iniciar_prueba(empresa, ahora: datetime | None = None) -> None:
    """Deja una empresa nueva en prueba de 7 días."""
    ahora = ahora or _ahora()
    empresa.estado_cuenta = PRUEBA
    empresa.prueba_inicio = ahora
    empresa.prueba_fin = ahora + timedelta(days=DIAS_PRUEBA)
    empresa.prueba_ampliada = False


def refrescar_estado(db: Session | None, empresa, ahora: datetime | None = None) -> str:
    """Estado efectivo. Si la prueba ya terminó, la marca 'vencida' (y hace
    commit si se pasa `db`). perusistemas siempre queda 'activa'."""
    ahora = ahora or _ahora()
    if es_principal(empresa):
        if empresa.estado_cuenta != ACTIVA:
            empresa.estado_cuenta = ACTIVA
            if db is not None:
                db.commit()
        return ACTIVA
    estado = empresa.estado_cuenta or PRUEBA
    fin = _aware(empresa.prueba_fin)
    if estado == PRUEBA and fin is not None and fin < ahora:
        empresa.estado_cuenta = VENCIDA
        if db is not None:
            db.commit()
        return VENCIDA
    return estado


def esta_bloqueada(db: Session | None, empresa, ahora: datetime | None = None) -> bool:
    return refrescar_estado(db, empresa, ahora) in ESTADOS_BLOQUEADOS


def dias_restantes(empresa, ahora: datetime | None = None) -> int | None:
    """Días que le quedan a la prueba (redondeo hacia arriba, mínimo 0).
    0 si ya venció; None si la cuenta no está en prueba (activa, suspendida)."""
    estado = empresa.estado_cuenta or PRUEBA
    if estado == VENCIDA:
        return 0
    if estado != PRUEBA or empresa.prueba_fin is None:
        return None
    segundos = (_aware(empresa.prueba_fin) - (ahora or _ahora())).total_seconds()
    return max(0, math.ceil(segundos / 86400))


def info_cuenta(db: Session | None, empresa, ahora: datetime | None = None) -> dict:
    """Bloque `cuenta` del heartbeat y del panel."""
    estado = refrescar_estado(db, empresa, ahora)
    dias = dias_restantes(empresa, ahora)
    return {
        "estado": estado,
        "dias_restantes": dias,
        "puede_ampliar": bool(
            estado == PRUEBA
            and not empresa.prueba_ampliada
            and dias is not None
            and dias <= DIAS_AVISO_AMPLIAR
        ),
    }


def ampliar_prueba(db: Session, empresa, ahora: datetime | None = None) -> dict:
    """+15 días, una sola vez, solo en prueba. CuentaError (-> 409) si no."""
    estado = refrescar_estado(db, empresa, ahora)
    if estado == VENCIDA:
        raise CuentaError("La prueba ya venció; ya no se puede ampliar. Escríbenos para activar tu cuenta.")
    if estado != PRUEBA:
        raise CuentaError("La cuenta no está en periodo de prueba.")
    if empresa.prueba_ampliada:
        raise CuentaError("La prueba ya fue ampliada una vez.")
    empresa.prueba_fin = _aware(empresa.prueba_fin) + timedelta(days=DIAS_AMPLIACION)
    empresa.prueba_ampliada = True
    db.commit()
    return info_cuenta(db, empresa, ahora)


def activar(empresa) -> None:
    empresa.estado_cuenta = ACTIVA
    empresa.prueba_fin = None


def suspender(empresa) -> None:
    if es_principal(empresa):
        raise CuentaError("perusistemas siempre está activa; no se puede suspender.")
    empresa.estado_cuenta = SUSPENDIDA
