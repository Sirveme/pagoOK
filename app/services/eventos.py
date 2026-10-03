"""Contrato de eventos v1 de PagoOK: webhooks salientes firmados.

PagoOK solo AVISA que pasó algo (ej. entró un pago). Qué hacer con eso
(descontar inventario, cerrar un pedido, emitir comprobante) lo decide el
sistema receptor. Así PagoOK no se modifica por cada sistema nuevo: basta
con registrar una suscripción (url_destino + secreto + tipos).

Flujo:
  1. emitir_evento(tipo, empresa_id, datos) arma el SOBRE y encola una fila
     en entrega_evento por cada suscripción activa de la empresa a ese tipo.
  2. El worker (hilo daemon) toma las entregas vencidas, hace POST firmado
     y registra el resultado. Si falla, reintenta con espera creciente.

Sobre (común a todo evento; congelado en "v1"):
  {
    "evento_id":   "uuid",                  # idempotencia en el receptor
    "tipo":        "pago.confirmado",
    "version":     "v1",
    "ocurrido_en": "2026-10-02T14:32:05-05:00",
    "empresa":     {"id_fiscal": "...", "pais": "PE", "id_fiscal_tipo": "RUC"},
    "moneda":      "PEN",
    "datos":       {...}                    # específico de cada tipo
  }

Cabeceras de cada POST:
  X-PagoOK-Firma:     sha256=<hex HMAC-SHA256(secreto, cuerpo_crudo)>
  X-PagoOK-Evento-Id: <evento_id>
  X-PagoOK-Evento-Tipo: <tipo>

Agregar un evento nuevo (pago.recibido, egreso.registrado, ...) = escribir
otra función armar_datos_* y llamar a emitir_evento. El emisor no cambia.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.models_eventos import (
    ESTADO_ENTREGADO,
    ESTADO_FALLIDO,
    ESTADO_PENDIENTE,
    EntregaEvento,
    SuscripcionWebhook,
)

logger = logging.getLogger("pagook")

TZ_LIMA = ZoneInfo("America/Lima")

VERSION_SOBRE = "v1"
TIPO_PAGO_CONFIRMADO = "pago.confirmado"

CABECERA_FIRMA = "X-PagoOK-Firma"
CABECERA_EVENTO_ID = "X-PagoOK-Evento-Id"
CABECERA_EVENTO_TIPO = "X-PagoOK-Evento-Tipo"
PREFIJO_FIRMA = "sha256="

# Reintentos: el intento N fallido espera ESPERAS_REINTENTO[N-1] antes del N+1.
# Con MAX_INTENTOS = 6 se usan las 5 esperas (1s, 5s, 30s, 2m, 10m): cubre
# caídas del receptor de ~13 min. Tras el 6.º fallo queda 'fallido' definitivo
# (proximo_reintento_en = NULL).
ESPERAS_REINTENTO = (
    timedelta(seconds=1),
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)
MAX_INTENTOS = 6
TIMEOUT_HTTP_SEGUNDOS = 10
# Mientras una entrega se está enviando, se "alquila" corriendo su
# proximo_reintento_en hacia adelante: otra réplica/worker no la toma.
DURACION_ALQUILER = timedelta(seconds=60)
TAMANO_LOTE = 20

# Datos fiscales por país cuando la tabla `pais` no los trae (prevé BO/CO).
_FISCAL_POR_PAIS = {
    "PE": ("RUC", "PEN"),
    "BO": ("NIT", "BOB"),
    "CO": ("NIT", "COP"),
}


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _a_iso_lima(dt: datetime | None) -> str | None:
    """ISO 8601 con zona (-05:00). Las fechas naive de la BD se asumen UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_LIMA).isoformat(timespec="seconds")


# =============================================================
# FIRMA (compartida por emisor y receptor)
# =============================================================

def generar_secreto() -> str:
    """Secreto HMAC nuevo para una suscripción (se muestra una sola vez)."""
    return "whsec_" + secrets.token_urlsafe(32)


def serializar_cuerpo(sobre: dict) -> bytes:
    """Bytes exactos que se envían y se firman (claves ordenadas, UTF-8)."""
    return json.dumps(
        sobre, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def firmar(cuerpo: bytes, secreto: str) -> str:
    digest = hmac.new(secreto.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()
    return PREFIJO_FIRMA + digest


def verificar_firma(cuerpo: bytes, firma_recibida: str | None, secreto: str) -> bool:
    """Recalcula el HMAC sobre el cuerpo CRUDO y compara en tiempo constante."""
    if not firma_recibida or not secreto:
        return False
    return hmac.compare_digest(firmar(cuerpo, secreto), firma_recibida.strip())


# =============================================================
# SOBRE
# =============================================================

def _datos_empresa(db: Session, empresa_id: int) -> tuple[dict, str]:
    """Devuelve (objeto empresa del sobre, moneda por defecto del país)."""
    from app.admin.models import Empresa, Pais

    empresa = db.get(Empresa, empresa_id)
    if empresa is None:
        raise ValueError(f"Empresa {empresa_id} no existe")
    pais_codigo = (empresa.pais_codigo or "PE").upper()
    id_fiscal_tipo, moneda = _FISCAL_POR_PAIS.get(pais_codigo, ("ID_FISCAL", "USD"))
    pais = db.get(Pais, pais_codigo)
    if pais is not None:
        id_fiscal_tipo = pais.nombre_id_fiscal or id_fiscal_tipo
        moneda = pais.moneda_codigo or moneda
    return (
        {
            # Nombre neutro: id_fiscal_tipo dice qué es (RUC en Perú, NIT en Bolivia, ...).
            "id_fiscal": empresa.id_fiscal,
            "pais": pais_codigo,
            "id_fiscal_tipo": id_fiscal_tipo,
        },
        moneda,
    )


def armar_sobre(
    tipo: str,
    empresa: dict,
    moneda: str,
    datos: dict,
    *,
    evento_id: str | None = None,
    ocurrido_en: datetime | None = None,
) -> dict:
    return {
        "evento_id": evento_id or str(uuid.uuid4()),
        "tipo": tipo,
        "version": VERSION_SOBRE,
        "ocurrido_en": _a_iso_lima(ocurrido_en or ahora_utc()),
        "empresa": empresa,
        "moneda": moneda,
        "datos": datos,
    }


# =============================================================
# EMISOR
# =============================================================

_despertador = threading.Event()


def despertar_worker() -> None:
    """Avisa al worker que hay entregas nuevas (envío casi inmediato)."""
    _despertador.set()


def emitir_evento(
    tipo: str,
    empresa_id: int,
    datos: dict,
    *,
    moneda: str | None = None,
    db: Session | None = None,
) -> dict | None:
    """Arma el sobre y encola una entrega por cada suscripción activa al tipo.

    No hace HTTP: solo inserta en entrega_evento y despierta al worker, así
    no demora a quien emite (ej. la ingesta del celular).
    Devuelve el sobre, o None si nadie está suscrito.
    """
    if db is None:
        from app.admin.db import db_session

        with db_session() as nueva:
            return emitir_evento(tipo, empresa_id, datos, moneda=moneda, db=nueva)

    # Pocas suscripciones por empresa: el filtro por tipo se hace en Python
    # (portable entre Postgres y SQLite de tests).
    suscripciones = [
        s
        for s in db.query(SuscripcionWebhook).filter(
            SuscripcionWebhook.empresa_id == empresa_id,
            SuscripcionWebhook.activo == True,  # noqa: E712
        )
        if s.suscrita_a(tipo)
    ]
    if not suscripciones:
        return None

    empresa, moneda_pais = _datos_empresa(db, empresa_id)
    sobre = armar_sobre(tipo, empresa, (moneda or moneda_pais).upper(), datos)
    ahora = ahora_utc()
    for s in suscripciones:
        db.add(EntregaEvento(
            evento_id=sobre["evento_id"],
            suscripcion_id=s.id,
            tipo=tipo,
            payload=sobre,
            intentos=0,
            ultimo_estado=ESTADO_PENDIENTE,
            proximo_reintento_en=ahora,
        ))
    db.commit()
    logger.info(
        f"Evento {tipo} {sobre['evento_id']} encolado para "
        f"{len(suscripciones)} suscripción(es) de empresa={empresa_id}"
    )
    despertar_worker()
    return sobre


# =============================================================
# EVENTO pago.confirmado
# =============================================================

_CANALES = {"yape", "plin", "transferencia", "tarjeta", "cajero"}
_INSTITUCION_POR_METODO = {
    "yape": "Yape",
    "plin": "Plin",
    "bim": "BIM",
    "p51": "P51",
    "pix": "Pix",
}


def _monto_texto(monto) -> str:
    """Decimal exacto como string con 2 decimales ("15.00"), nunca float."""
    return str(Decimal(str(monto)).quantize(Decimal("0.01")))


def armar_datos_pago_confirmado(pago, referencia_pedido: str | None = None) -> dict:
    metodo = (pago.metodo or "").lower()
    return {
        "pago_id": pago.id,
        "monto": _monto_texto(pago.monto),
        "canal": metodo if metodo in _CANALES else "otro",
        "institucion": pago.banco or _INSTITUCION_POR_METODO.get(metodo) or (metodo or "otro"),
        "contraparte": pago.titular or "",
        "codigo_operacion": pago.codigo_operacion or None,
        # Hoy el parser no extrae la hora que imprime el banco: se usa la hora
        # en que el SERVIDOR recibió la notificación (no la del celular).
        "fecha_hora_banco": _a_iso_lima(pago.recibido_en),
        "referencia_pedido": referencia_pedido,
    }


def debe_emitir_pago_confirmado(pago) -> bool:
    """Solo ingresos clasificados con certeza. Los dudosos (tipo_incierto)
    quedan para un futuro pago.recibido / pago.no_conciliado."""
    return (pago.tipo or "ingreso") == "ingreso" and not bool(pago.tipo_incierto)


def emitir_pago_confirmado(db: Session, pago) -> dict | None:
    if not debe_emitir_pago_confirmado(pago):
        return None
    return emitir_evento(
        TIPO_PAGO_CONFIRMADO,
        pago.empresa_id,
        armar_datos_pago_confirmado(pago),
        moneda=pago.moneda,
        db=db,
    )


# =============================================================
# ENTREGA + REINTENTOS
# =============================================================

ResultadoHttp = tuple[int | None, str | None]  # (código HTTP, error)
Enviador = Callable[[str, bytes, dict], ResultadoHttp]


def enviar_http(url: str, cuerpo: bytes, cabeceras: dict) -> ResultadoHttp:
    try:
        r = httpx.post(
            url, content=cuerpo, headers=cabeceras,
            timeout=TIMEOUT_HTTP_SEGUNDOS, follow_redirects=False,
        )
        return r.status_code, None if 200 <= r.status_code < 300 else r.text[:500]
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]


def cabeceras_para(sobre: dict, cuerpo: bytes, secreto: str) -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "PagoOK-Webhooks/1",
        CABECERA_FIRMA: firmar(cuerpo, secreto),
        CABECERA_EVENTO_ID: str(sobre["evento_id"]),
        CABECERA_EVENTO_TIPO: str(sobre["tipo"]),
    }


def _registrar_resultado(entrega: EntregaEvento, codigo: int | None, error: str | None,
                         ahora: datetime) -> None:
    entrega.intentos = (entrega.intentos or 0) + 1
    entrega.ultimo_codigo_http = codigo
    if codigo is not None and 200 <= codigo < 300:
        entrega.ultimo_estado = ESTADO_ENTREGADO
        entrega.entregado_en = ahora
        entrega.proximo_reintento_en = None
        entrega.ultimo_error = None
        return
    entrega.ultimo_estado = ESTADO_FALLIDO
    entrega.ultimo_error = error or f"HTTP {codigo}"
    if entrega.intentos >= MAX_INTENTOS:
        entrega.proximo_reintento_en = None  # fallido definitivo
    else:
        entrega.proximo_reintento_en = ahora + ESPERAS_REINTENTO[entrega.intentos - 1]


def entregar(db: Session, entrega: EntregaEvento, enviar: Enviador = enviar_http,
             ahora: datetime | None = None) -> EntregaEvento:
    """Un intento de entrega. Hace commit del resultado."""
    s = entrega.suscripcion
    if s is None or not s.activo:
        entrega.ultimo_estado = ESTADO_FALLIDO
        entrega.ultimo_error = "Suscripción inactiva o eliminada"
        entrega.proximo_reintento_en = None
        db.commit()
        return entrega

    cuerpo = serializar_cuerpo(entrega.payload)
    codigo, error = enviar(s.url_destino, cuerpo, cabeceras_para(entrega.payload, cuerpo, s.secreto_hmac))
    _registrar_resultado(entrega, codigo, error, ahora or ahora_utc())
    db.commit()
    if entrega.ultimo_estado == ESTADO_ENTREGADO:
        logger.info(f"Webhook entregado: entrega={entrega.id} evento={entrega.evento_id} HTTP {codigo}")
    else:
        logger.warning(
            f"Webhook fallido: entrega={entrega.id} intento={entrega.intentos} "
            f"HTTP {codigo} error={entrega.ultimo_error!r} "
            f"proximo={entrega.proximo_reintento_en}"
        )
    return entrega


def _tomar_lote(db: Session, ahora: datetime, limite: int) -> list[int]:
    """Reserva entregas vencidas (SKIP LOCKED + alquiler) y devuelve sus ids."""
    filas = (
        db.query(EntregaEvento)
        .filter(
            or_(EntregaEvento.ultimo_estado == ESTADO_PENDIENTE,
                EntregaEvento.ultimo_estado == ESTADO_FALLIDO),
            EntregaEvento.proximo_reintento_en.isnot(None),
            EntregaEvento.proximo_reintento_en <= ahora,
        )
        .order_by(EntregaEvento.proximo_reintento_en)
        .limit(limite)
        .with_for_update(skip_locked=True)
        .all()
    )
    for f in filas:
        f.proximo_reintento_en = ahora + DURACION_ALQUILER
    ids = [f.id for f in filas]
    db.commit()
    return ids


def procesar_pendientes(db: Session, enviar: Enviador = enviar_http,
                        ahora: datetime | None = None, limite: int = TAMANO_LOTE) -> int:
    """Recorre entregas pendientes/fallidas cuyo reintento ya venció. Devuelve cuántas intentó."""
    ids = _tomar_lote(db, ahora or ahora_utc(), limite)
    for entrega_id in ids:
        entrega = db.get(EntregaEvento, entrega_id)
        if entrega is None:
            continue
        try:
            entregar(db, entrega, enviar=enviar, ahora=ahora)
        except Exception as exc:
            db.rollback()
            logger.exception(f"Error entregando webhook {entrega_id} (se continúa): {exc}")
    return len(ids)


# =============================================================
# WORKER (hilo daemon, mismo patrón que consolidacion_heartbeat)
# =============================================================

INTERVALO_WORKER_SEGUNDOS = 1.0


def _loop() -> None:
    from app.admin.db import SessionLocal

    while True:
        _despertador.wait(timeout=INTERVALO_WORKER_SEGUNDOS)
        _despertador.clear()
        try:
            db = SessionLocal()
            try:
                # Vaciar en lotes mientras haya trabajo vencido.
                while procesar_pendientes(db) >= TAMANO_LOTE:
                    pass
            finally:
                db.close()
        except Exception as exc:
            # Ej. DDL aún no aplicado: no tumbar el hilo; esperar más.
            logger.warning(f"Worker de webhooks: {exc}")
            _despertador.wait(timeout=30)


def iniciar_worker_eventos() -> threading.Thread | None:
    """Arranca el worker de entregas. Desactivable con EVENTOS_WORKER_ACTIVO=0."""
    if os.getenv("EVENTOS_WORKER_ACTIVO", "1").strip() in ("0", "false", "no"):
        logger.info("Worker de webhooks desactivado por EVENTOS_WORKER_ACTIVO")
        return None
    hilo = threading.Thread(target=_loop, name="eventos-webhook", daemon=True)
    hilo.start()
    logger.info("Worker de webhooks salientes iniciado")
    return hilo
