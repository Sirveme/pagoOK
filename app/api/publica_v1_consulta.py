"""API pública v1 — consulta de pagos (pull) e idempotencia de uso.

  POST /api/v1/pagos/consultar              (auth: X-API-Key)
  POST /api/v1/pagos/{pago_id}/marcar-usado (auth: X-API-Key)

Autenticación: la misma credencial `cuenta_api` (X-API-Key) de la API v1, no
el secreto HMAC de los webhooks. El secreto de webhook sirve para que el
RECEPTOR verifique que el aviso viene de PagoOK; usarlo también como
credencial de entrada mezclaría dos funciones y obligaría a guardarlo en dos
lados. `cuenta_api` ya resuelve empresa_id en el backend, guarda solo el hash
y tiene rate limit; además, al ser una credencial por sistema, sabemos QUIÉN
usó cada pago sin confiar en lo que el cliente declare.

Lógica de coincidencia: app/services/conciliacion.py.
Contrato: docs/consulta-pagos-v1.md.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.api.models_cuenta import CuentaApi
from app.api.publica_v1 import cuenta_actual
from app.services.conciliacion import (
    PagoNoEncontrado,
    PagoYaUsado,
    consultar_pago,
    marcar_usado,
)
from app.services.eventos import CANALES

router = APIRouter(prefix="/api/v1", tags=["api-v1-publica"])


class ConsultaPagoIn(BaseModel):
    monto: str = Field(..., description='Decimal como texto, ej. "30.00"')
    fecha_hora_declarada: datetime = Field(
        ..., description="ISO 8601. Sin zona horaria se interpreta como hora de Lima."
    )
    ventana_minutos: int = Field(15, ge=1, le=1440, description="Tolerancia ± en minutos")
    canal: str | None = Field(None, description="|".join(CANALES))
    nombre_pagador_declarado: str | None = Field(
        None, max_length=200, description="Titular del medio de pago (NO el comprador)"
    )
    codigo_operacion: str | None = Field(None, max_length=30)

    @field_validator("monto")
    @classmethod
    def _monto_valido(cls, v: str) -> str:
        try:
            m = Decimal(str(v).strip())
        except (InvalidOperation, ValueError):
            raise ValueError('monto debe ser un decimal como texto, ej. "30.00"')
        if not m.is_finite() or m <= 0:
            raise ValueError("monto debe ser mayor que 0")
        return str(m)

    @field_validator("canal")
    @classmethod
    def _canal_valido(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if v not in CANALES:
            raise ValueError(f"canal debe ser uno de: {', '.join(CANALES)}")
        return v


class MarcarUsadoIn(BaseModel):
    referencia_externa: str = Field(
        ..., min_length=1, max_length=150,
        description="Para qué se usó: id de suscripción, pedido o comprobante en tu sistema",
    )
    sistema: str | None = Field(
        None, max_length=150, description="Nombre del sistema; por defecto, el de la credencial"
    )

    @field_validator("referencia_externa")
    @classmethod
    def _no_vacia(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("referencia_externa no puede estar vacía")
        return v


@router.post("/pagos/consultar")
def consultar(
    body: ConsultaPagoIn,
    cuenta: CuentaApi = Depends(cuenta_actual),
    db: Session = Depends(get_db),
):
    """¿Existe este pago? Responde nivel alta / media / sin_coincidencia."""
    return consultar_pago(
        db,
        # empresa_id SIEMPRE desde la credencial, nunca desde el cuerpo.
        empresa_id=cuenta.empresa_id,
        cuenta_id=cuenta.id,
        monto=Decimal(body.monto),
        fecha_hora_declarada=body.fecha_hora_declarada,
        ventana_minutos=body.ventana_minutos,
        canal=body.canal,
        nombre_pagador_declarado=body.nombre_pagador_declarado,
        codigo_operacion=body.codigo_operacion,
    )


@router.post("/pagos/{pago_id}/marcar-usado")
def marcar_pago_usado(
    body: MarcarUsadoIn,
    pago_id: int = Path(..., description="pago_id devuelto por /pagos/consultar"),
    cuenta: CuentaApi = Depends(cuenta_actual),
    db: Session = Depends(get_db),
):
    """Marca el pago como usado para `referencia_externa`.

    200 -> usado (o ya usado por esta credencial con la misma referencia: idempotente)
    409 -> ya usado para otra referencia u otro sistema
    404 -> no existe o no pertenece a la empresa de la credencial
    """
    try:
        return marcar_usado(
            db, cuenta=cuenta, pago_id=pago_id,
            referencia_externa=body.referencia_externa, sistema=body.sistema,
        )
    except PagoNoEncontrado:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    except PagoYaUsado as exc:
        raise HTTPException(status_code=409, detail=str(exc))
