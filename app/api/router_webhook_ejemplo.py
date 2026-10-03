"""Receptor de EJEMPLO del contrato de eventos v1 (lado de quien recibe).

POST /api/v1/webhooks/receptor-ejemplo

Sirve como referencia ejecutable para Facturalo.pro, QueVendi, Te Atiendo,
alerta.pe o el ERP de un cliente, y para probar una suscripción de punta a
punta apuntándola a este mismo servidor.

Reglas que todo receptor debe cumplir:
  1. Leer el cuerpo CRUDO (bytes) y recalcular el HMAC-SHA256 con el secreto
     de la suscripción. Si no coincide con X-PagoOK-Firma → 401 y no procesar.
     (No re-serializar el JSON antes de verificar: cambia los bytes.)
  2. Idempotencia por evento_id: si ya se procesó, responder 2xx igual (para
     que PagoOK deje de reintentar) pero NO volver a aplicarlo.
  3. Responder 2xx rápido; el trabajo pesado, en segundo plano.

Configuración: PAGOOK_WEBHOOK_SECRETO_EJEMPLO = secreto_hmac de la
suscripción que apunta a este endpoint. Sin esa variable responde 503.
"""
import json
import logging
import os
import threading
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request

from app.services.eventos import CABECERA_FIRMA, verificar_firma

logger = logging.getLogger("pagook")
router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks-ejemplo"])

# Idempotencia de EJEMPLO, en memoria (se pierde al reiniciar y no se comparte
# entre réplicas). En un receptor real: tabla con evento_id UNIQUE e
# INSERT ... ON CONFLICT DO NOTHING en la misma transacción que aplica el pago.
_MAX_RECORDADOS = 10_000
_procesados: "OrderedDict[str, None]" = OrderedDict()
_lock = threading.Lock()


def _marcar_si_nuevo(evento_id: str) -> bool:
    """True si el evento es nuevo (y lo registra); False si ya se procesó."""
    with _lock:
        if evento_id in _procesados:
            return False
        _procesados[evento_id] = None
        if len(_procesados) > _MAX_RECORDADOS:
            _procesados.popitem(last=False)
        return True


def _secreto() -> str:
    return os.getenv("PAGOOK_WEBHOOK_SECRETO_EJEMPLO", "").strip()


@router.post("/receptor-ejemplo")
async def receptor_ejemplo(request: Request):
    secreto = _secreto()
    if not secreto:
        raise HTTPException(503, "Receptor de ejemplo sin configurar (PAGOOK_WEBHOOK_SECRETO_EJEMPLO)")

    # 1) Firma sobre los bytes crudos.
    cuerpo = await request.body()
    if not verificar_firma(cuerpo, request.headers.get(CABECERA_FIRMA), secreto):
        logger.warning("Receptor ejemplo: firma inválida, evento rechazado")
        raise HTTPException(401, "Firma inválida")

    try:
        sobre = json.loads(cuerpo)
        evento_id = str(sobre["evento_id"])
        tipo = sobre["tipo"]
        version = sobre.get("version", "v1")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Sobre inválido")

    # 2) Idempotencia.
    if not _marcar_si_nuevo(evento_id):
        logger.info(f"Receptor ejemplo: evento {evento_id} duplicado, descartado")
        return {"status": "duplicado", "evento_id": evento_id}

    # 3) Despachar por tipo + versión. Tipos desconocidos se aceptan (2xx)
    #    para que agregar eventos nuevos en PagoOK no rompa a nadie.
    if tipo == "pago.confirmado" and version == "v1":
        d = sobre.get("datos") or {}
        logger.info(
            f"Receptor ejemplo: pago.confirmado {sobre.get('moneda')} "
            f"{d.get('monto')} vía {d.get('canal')}/{d.get('institucion')} "
            f"de {d.get('contraparte')!r} ref_pedido={d.get('referencia_pedido')}"
        )
        # Aquí el sistema real: buscar el pedido pendiente por
        # referencia_pedido (o por monto + ventana de tiempo), marcarlo pagado,
        # descontar inventario, emitir comprobante, etc.
    else:
        logger.info(f"Receptor ejemplo: tipo {tipo} {version} ignorado")

    return {"status": "ok", "evento_id": evento_id}
