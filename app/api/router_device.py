"""Heartbeat de dispositivos Android + dashboard admin de estado.

Endpoints:
  POST /api/v1/device/heartbeat          (auth X-Device-Token)  -> upsert estado
  GET  /api/v1/admin/dispositivos        (auth X-Admin-Token)   -> JSON estado + resumen
  GET  /admin/dispositivos/estado        (auth ?token=)         -> dashboard HTML

NOTA: el dashboard va en /admin/dispositivos/ESTADO (no /admin/dispositivos)
porque esa ruta ya la usa el panel de gestión de tokens (admin_dispositivos.py).

Español neutro peruano en textos visibles.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import CodigoInvitacion, Empresa
from app.admin import superadmin
from app.api.models_device import DeviceEstadoActual
from app.api.models_cuenta import CuentaApi
from app.api.models_webhook import Dispositivo, NotificacionRaw
from app.api.deps_empresa import resolver_dispositivo
from app.api.publica_v1 import cuenta_actual
from app.api.router_demo import _obtener_empresa_por_slug
from app.services import cuenta_empresa
from app.api.tokens_legacy import advertir_si_token_legacy

logger = logging.getLogger("pagook")
router = APIRouter(tags=["device-heartbeat"])

ESTADOS_VALIDOS = {"verde", "amarillo", "rojo"}
# Conexión según el último heartbeat. Con la pantalla apagada (modo de reposo
# de Android) el dispositivo 17 envía uno cada ~5 min, aunque se le pida 60 s
# (medido en producción, 2026-10-26): 20 min = 3-4 heartbeats perdidos.
UMBRAL_AMARILLO_MIN = 20
UMBRAL_ROJO_MIN = 45
DIAS_INACTIVO = 7
# Ventana para contar avisos de billetera que la app no pudo interpretar
# (estado 'no_reconocido'): es una alerta rodante que se limpia sola.
HORAS_NO_RECONOCIDO = 24

# Rate limit del auto-registro: máx 10 requests por IP en 60 s (en memoria).
LIMITE_REGISTRO = 10
VENTANA_REGISTRO_SEG = 60
_lock_registro = threading.Lock()
_buckets_registro: dict[str, tuple[int, int]] = {}  # ip -> (ventana, conteo)


def _ip_cliente(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def _registro_excedido(ip: str) -> bool:
    ventana = int(time.time() // VENTANA_REGISTRO_SEG)
    with _lock_registro:
        v, conteo = _buckets_registro.get(ip, (ventana, 0))
        if v != ventana:
            v, conteo = ventana, 0
        conteo += 1
        _buckets_registro[ip] = (v, conteo)
    return conteo > LIMITE_REGISTRO


def reiniciar_rate_limit_registro() -> None:
    """Limpia el estado del rate limit (usado por los tests)."""
    with _lock_registro:
        _buckets_registro.clear()

# Campos obligatorios del body del heartbeat.
CAMPOS_REQUERIDOS = (
    "estado",
    "ultimo_ping_guardian_ok",
    "ultimo_pago_bancario",
    "pings_fallidos_consecutivos",
    "veces_zombie_total",
    "manufacturer",
    "modelo",
    "android_version",
    "app_version",
)


def _epoch_ms_a_dt(valor) -> Optional[datetime]:
    """Convierte epoch milliseconds (long) a datetime UTC tz-aware."""
    if valor is None:
        return None
    try:
        return datetime.fromtimestamp(int(valor) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _bool_opcional(valor) -> Optional[bool]:
    """None si la app no mandó el campo (versiones viejas); si no, bool(valor)."""
    return None if valor is None else bool(valor)


def _fecha_flexible(valor) -> Optional[datetime]:
    """ISO 8601 o epoch en milisegundos -> datetime UTC. None si no se entiende."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        return _epoch_ms_a_dt(valor)
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def guardar_campos_opcionales(disp: Dispositivo, data: dict) -> None:
    """Campos nuevos de registro/heartbeat/vinculación. Solo se tocan los que
    vienen; las versiones viejas de la app no los envían."""
    if data.get("consentimiento_version") is not None:
        disp.consentimiento_version = str(data["consentimiento_version"])[:20]
    if data.get("consentimiento_fecha") is not None:
        disp.consentimiento_fecha = _fecha_flexible(data["consentimiento_fecha"])
    if data.get("pais") is not None:
        disp.pais = str(data["pais"]).upper()[:2]
    if isinstance(data.get("bancos_declarados"), list):
        disp.bancos_declarados = [str(b)[:30] for b in data["bancos_declarados"]][:50]
    if data.get("nivel_equipo") is not None:
        disp.nivel_equipo = str(data["nivel_equipo"])[:20]
    if data.get("egresos_activo") is not None:
        disp.egresos_activo = bool(data["egresos_activo"])


# =============================================================
# AUTO-REGISTRO DE DISPOSITIVOS
# =============================================================

VERSION_MINIMA_SIN_REGISTRO_LEGACY = (1, 6, 0)
VERSION_CODE_MINIMO_SIN_REGISTRO_LEGACY = 6000


def es_app_1_6_o_mayor(data: dict) -> bool:
    """True si la app es 1.6.0 o mayor. Usa `version_code` si viene (>= 6000);
    si no, el `app_version` (versionName, ej. "1.6.0", "1.5.1", "1.4-beta-fix3")."""
    vc = data.get("version_code")
    if vc is not None:
        try:
            return int(vc) >= VERSION_CODE_MINIMO_SIN_REGISTRO_LEGACY
        except (TypeError, ValueError):
            pass
    numeros = [int(n) for n in re.findall(r"\d+", str(data.get("app_version") or "").split("-")[0])[:3]]
    if not numeros:
        return False  # sin versión: se trata como app vieja (comportamiento de siempre)
    return tuple(numeros + [0] * (3 - len(numeros))) >= VERSION_MINIMA_SIN_REGISTRO_LEGACY


def _respuesta_registro(disp: Dispositivo, empresa: Empresa) -> dict:
    return {
        "token": disp.token,
        "dispositivo_id": disp.id,
        "empresa": (empresa.nombre_comercial or empresa.razon_social or "").upper(),
        "empresa_slug": empresa.slug,
    }


@router.post("/api/v1/dispositivos/registrar", status_code=201)
async def registrar_dispositivo(request: Request, db: Session = Depends(get_db)):
    """Alta de un dispositivo. Sin auth previa; rate limit estricto por IP.

    Idempotente por (empresa, instalacion_id): un reintento devuelve el mismo
    token en vez de crear otro dispositivo.

    Los tokens se guardan en TEXTO PLANO (patrón actual del sistema).
    """
    # Rate limit: 10 por IP por minuto.
    ip = _ip_cliente(request)
    if _registro_excedido(ip):
        raise HTTPException(429, "Demasiados intentos de registro. Espera un minuto.")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(data, dict):
        raise HTTPException(400, "El body debe ser un objeto JSON")

    slug = (data.get("empresa_slug") or "").strip()
    if not slug:
        raise HTTPException(400, "Falta empresa_slug")

    # 1) Empresa existe?
    empresa = _obtener_empresa_por_slug(db, slug)
    if empresa is None:
        raise HTTPException(404, "Empresa no encontrada")

    instalacion_id = (data.get("instalacion_id") or "").strip()[:64] or None

    # 2) Idempotencia por instalacion_id + empresa.
    if instalacion_id:
        existente = (
            db.query(Dispositivo)
            .filter(
                Dispositivo.empresa_id == empresa.id,
                Dispositivo.instalacion_id == instalacion_id,
            )
            .first()
        )
        if existente is not None:
            if existente.activo:
                return _respuesta_registro(existente, empresa)  # 201 idempotente
            raise HTTPException(403, "Dispositivo desactivado. Contacta al administrador.")

    # 3) Validar código de invitación (o auto-registro abierto).
    codigo = (data.get("codigo_invitacion") or "").strip().upper()
    inv = None
    # zWeb-06: desde la app 1.6.0 (versionCode 6000) el celular se vincula con
    # el código del panel (/api/v1/dispositivos/vincular). El registro SIN
    # código, que cae en perusistemas, queda solo para las apps 1.5.x: así un
    # celular nuevo que acepta el consentimiento y se reinicia antes de
    # vincular no termina enviando sus pagos a perusistemas.
    if not codigo and es_app_1_6_o_mayor(data):
        raise HTTPException(403, "Esta versión de PagoOK se vincula con el código de tu panel. "
                                 "Abre la app y toca «Vincular».")
    if codigo:
        inv = (
            db.query(CodigoInvitacion)
            .filter(
                CodigoInvitacion.codigo == codigo,
                CodigoInvitacion.empresa_id == empresa.id,
                CodigoInvitacion.activo == True,  # noqa: E712
            )
            .first()
        )
        if inv is None or inv.expira_en < datetime.utcnow() or (inv.usos_actuales or 0) >= inv.max_usos:
            raise HTTPException(400, "Código de invitación inválido")
    else:
        if not getattr(empresa, "acepta_autoregistro", False):
            raise HTTPException(403, "Este comercio requiere código de invitación.")

    # 4) Crear el dispositivo con token único.
    token = "tok_" + secrets.token_urlsafe(24)
    manufacturer = str(data.get("manufacturer") or "")[:50] or None
    modelo = str(data.get("modelo") or "")[:80] or None
    nombre = (f"{manufacturer or ''} {modelo or ''}").strip()[:100] or "Dispositivo"

    disp = Dispositivo(
        empresa_id=empresa.id,
        token=token,
        nombre=nombre,
        modelo=modelo,
        instalacion_id=instalacion_id,
        manufacturer=manufacturer,
        android_version=str(data.get("android_version") or "")[:20] or None,
        app_version=str(data.get("app_version") or "")[:30] or None,
        activo=True,
    )
    guardar_campos_opcionales(disp, data)
    db.add(disp)
    if inv is not None:
        inv.usos_actuales = (inv.usos_actuales or 0) + 1
    db.commit()
    db.refresh(disp)

    logger.info(f"Dispositivo auto-registrado id={disp.id} empresa={empresa.id} instalacion={instalacion_id}")
    return _respuesta_registro(disp, empresa)


# =============================================================
# ENDPOINT 1: POST /api/v1/device/heartbeat
# =============================================================

@router.post("/api/v1/device/heartbeat")
async def heartbeat(
    request: Request,
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    # 1) Auth por token de dispositivo -> empresa (nunca desde el cliente).
    dispositivo = resolver_dispositivo(db, x_device_token)

    advertir_si_token_legacy(x_device_token, "heartbeat", logger)

    # 2) Validar body y campos requeridos.
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(data, dict):
        raise HTTPException(400, "El body debe ser un objeto JSON")

    faltantes = [c for c in CAMPOS_REQUERIDOS if data.get(c) is None]
    if faltantes:
        raise HTTPException(400, f"Faltan campos requeridos: {', '.join(faltantes)}")

    estado = str(data.get("estado")).lower()
    if estado not in ESTADOS_VALIDOS:
        raise HTTPException(400, "estado debe ser 'verde', 'amarillo' o 'rojo'")

    # 3) UPSERT portable (get-or-update): mismo efecto que INSERT ... ON CONFLICT.
    #    Se usa ORM en vez de ON CONFLICT crudo para que corra igual en los tests
    #    (SQLite) y en producción (Postgres); el heartbeat es serial por
    #    dispositivo, así que no hay carrera relevante.
    fila = (
        db.query(DeviceEstadoActual)
        .filter(DeviceEstadoActual.dispositivo_id == dispositivo.id)
        .first()
    )
    estado_anterior = fila.estado if fila else None

    def _campos_comunes(f: DeviceEstadoActual) -> None:
        f.estado = estado
        f.ultimo_ping_guardian_ok = _epoch_ms_a_dt(data.get("ultimo_ping_guardian_ok"))
        f.ultimo_pago_bancario = _epoch_ms_a_dt(data.get("ultimo_pago_bancario"))
        f.pings_fallidos_consecutivos = int(data.get("pings_fallidos_consecutivos") or 0)
        f.veces_zombie_total = int(data.get("veces_zombie_total") or 0)
        f.veces_rebind_intentado = int(data.get("veces_rebind_intentado") or 0)
        f.veces_rebind_exitoso = int(data.get("veces_rebind_exitoso") or 0)
        # Estado del lector de notificaciones (incidente 06-11): la app 1.6.x los
        # envía; las viejas no, y quedan en NULL (desconocido, no fuerza rojo).
        f.listener_conectado = _bool_opcional(data.get("listener_conectado"))
        f.permiso_notificaciones = _bool_opcional(data.get("permiso_notificaciones"))
        f.manufacturer = str(data.get("manufacturer"))[:50]
        f.modelo = str(data.get("modelo"))[:80]
        f.android_version = str(data.get("android_version"))[:20]
        f.app_version = str(data.get("app_version"))[:30]
        f.ultimo_heartbeat = _ahora_utc()

    if fila is None:
        fila = DeviceEstadoActual(dispositivo_id=dispositivo.id, veces_alarma_disparada=0)
        _campos_comunes(fila)
        db.add(fila)
    else:
        # Alarma: incrementar SOLO en la transición a 'rojo' desde un estado != 'rojo'.
        if estado == "rojo" and estado_anterior != "rojo":
            fila.veces_alarma_disparada = (fila.veces_alarma_disparada or 0) + 1
        _campos_comunes(fila)

    # Campos nuevos opcionales (consentimiento, país, bancos...): las apps
    # viejas no los envían y no pasa nada.
    guardar_campos_opcionales(dispositivo, data)
    db.commit()

    # 4) Log según estado. El lector caído es el incidente 06-11: el equipo
    #    sigue vivo (heartbeat), pero Android desconectó el NotificationListener
    #    y no capturará ningún pago. Se avisa aunque el estado llegue "verde".
    if data.get("listener_conectado") is False:
        logger.warning(
            f"Lector de notificaciones DESCONECTADO dispositivo={dispositivo.id} "
            f"empresa={dispositivo.empresa_id} estado_reportado={estado} "
            f"permiso_notificaciones={data.get('permiso_notificaciones')}"
        )
    if estado == "verde":
        logger.info(f"Heartbeat verde dispositivo={dispositivo.id} empresa={dispositivo.empresa_id}")
    else:
        logger.warning(
            f"Heartbeat {estado.upper()} dispositivo={dispositivo.id} "
            f"empresa={dispositivo.empresa_id} "
            f"pings_fallidos={data.get('pings_fallidos_consecutivos')}"
        )

    # 5) Response. `cuenta` es aditivo: las apps viejas lo ignoran.
    empresa = db.get(Empresa, dispositivo.empresa_id)
    cuenta = cuenta_empresa.info_cuenta(db, empresa) if empresa else None
    return {"ok": True, "next_check_seconds": 60, "cuenta": cuenta}


# =============================================================
# ENDPOINT: POST /api/v1/device/no-reconocido  (auth X-Device-Token)
# =============================================================
# Red de seguridad del incidente 06-11: cuando el lector SÍ funciona pero llega
# un aviso de billetera/banco que la app no supo interpretar como pago (formato
# nuevo, texto raro), lo reporta aquí para que NO se pierda en silencio. Se
# guarda como notificacion_raw con estado 'no_reconocido', aparece en el panel
# de estado y pinta el equipo amarillo mientras haya avisos recientes.

@router.post("/api/v1/device/no-reconocido")
async def aviso_no_reconocido(
    request: Request,
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
):
    dispositivo = resolver_dispositivo(db, x_device_token)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")
    if not isinstance(data, dict):
        raise HTTPException(400, "El body debe ser un objeto JSON")

    package = (data.get("package") or "")[:200]
    titulo = data.get("titulo") or ""
    texto = data.get("texto") or ""
    if not texto.strip():
        raise HTTPException(400, "Falta el texto del aviso")
    sbn_key = (data.get("sbn_key") or "")[:200] or None

    # Dedup por sbn_key (si la app reintenta, no duplicamos el aviso).
    if sbn_key:
        existente = (
            db.query(NotificacionRaw)
            .filter(NotificacionRaw.empresa_id == dispositivo.empresa_id,
                    NotificacionRaw.sbn_key == sbn_key)
            .first()
        )
        if existente:
            return {"status": "duplicado", "raw_id": existente.id}

    raw = NotificacionRaw(
        empresa_id=dispositivo.empresa_id,
        dispositivo_id=dispositivo.id,
        package_app=package,
        titulo=titulo[:1000] if titulo else None,
        texto=texto[:5000],
        sbn_key=sbn_key,
        timestamp_celular=data.get("timestamp_celular"),
        estado_parseo="no_reconocido",
    )
    db.add(raw)
    db.commit()
    logger.warning(
        f"Aviso NO RECONOCIDO dispositivo={dispositivo.id} empresa={dispositivo.empresa_id} "
        f"package={package} texto={texto[:80]!r}"
    )
    return {"status": "guardado", "raw_id": raw.id}


# =============================================================
# Datos compartidos para el dashboard (JSON y HTML)
# =============================================================

def _minutos_desde(uh: Optional[datetime]) -> Optional[float]:
    if uh is None:
        return None
    if uh.tzinfo is None:
        uh = uh.replace(tzinfo=timezone.utc)
    return (_ahora_utc() - uh).total_seconds() / 60.0


_GRAVEDAD = {"verde": 0, "amarillo": 1, "rojo": 2}


def estado_conexion(minutos: Optional[float]) -> str:
    """Color por antigüedad del último heartbeat (sin heartbeat -> rojo)."""
    if minutos is None or minutos > UMBRAL_ROJO_MIN:
        return "rojo"
    if minutos >= UMBRAL_AMARILLO_MIN:
        return "amarillo"
    return "verde"


def _estado_unico(minutos: Optional[float], reportado: Optional[str],
                  listener_conectado: Optional[bool] = None,
                  avisos_no_reconocidos: int = 0) -> tuple[str, str]:
    """Un solo estado por equipo: el peor entre la conexión, el lector de
    notificaciones y lo que el propio equipo reportó. Devuelve (estado, motivo).

    El lector caído (listener_conectado == False) es ROJO aunque el heartbeat
    sea reciente y verde: ese es justo el incidente 06-11 (el equipo está vivo
    pero Android desconectó el NotificationListener y no captura pagos).

    Un equipo con app vieja NO reporta el lector (listener_conectado == None):
    nunca se pinta verde "por defecto", queda AMARILLO pidiendo actualizar, para
    no dar una falsa sensación de que todo está bien.

    Avisos de billetera sin interpretar (no_reconocido) recientes también dejan
    el equipo en AMARILLO: pudo perderse un pago con un formato nuevo."""
    if minutos is None:
        return "rojo", "Nunca envió un heartbeat"
    if listener_conectado is False:
        return "rojo", "El lector de notificaciones está desconectado"
    conexion = estado_conexion(minutos)
    if conexion != "verde":
        return conexion, "Sin heartbeat reciente"
    if reportado in ("amarillo", "rojo"):
        return reportado, "El equipo reporta un problema de captura"
    if avisos_no_reconocidos:
        n = avisos_no_reconocidos
        return "amarillo", f"{n} aviso{'s' if n != 1 else ''} de billetera sin interpretar"
    if listener_conectado is None:
        return "amarillo", "Sin dato del lector: actualiza la app"
    return "verde", "Conectado"


def _conteo_no_reconocido(db: Session, ids: list[int]) -> dict[int, int]:
    """{dispositivo_id: avisos 'no_reconocido' en las últimas HORAS_NO_RECONOCIDO
    horas}. Equipos sin avisos no aparecen en el dict."""
    if not ids:
        return {}
    desde = datetime.utcnow() - timedelta(hours=HORAS_NO_RECONOCIDO)
    filas = (
        db.query(NotificacionRaw.dispositivo_id, func.count())
        .filter(NotificacionRaw.estado_parseo == "no_reconocido",
                NotificacionRaw.dispositivo_id.in_(ids),
                NotificacionRaw.timestamp_servidor >= desde)
        .group_by(NotificacionRaw.dispositivo_id)
        .all()
    )
    return {did: n for did, n in filas}


def _fmt_utc(dt: Optional[datetime]) -> Optional[str]:
    """datetime -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC) o None."""
    dt = _a_utc(dt)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _obtener_dispositivos(db: Session) -> dict:
    """Equipos no archivados con su estado único, el resumen y las empresas
    sin equipos activos.

    - Activo: heartbeat en los últimos DIAS_INACTIVO días (o registrado hace
      menos de eso y todavía sin heartbeat). verde + amarillo + rojo = total.
    - Inactivo: el resto. Viene con `inactivo=True`; la página lo oculta por
      defecto y se puede archivar (Dispositivo.activo = False, sin borrar).
    Orden: empresa y, dentro de cada una, último heartbeat (más reciente primero).
    """
    ahora = _ahora_utc()
    filas = (
        db.query(Dispositivo, Empresa, DeviceEstadoActual)
        .join(Empresa, Empresa.id == Dispositivo.empresa_id)
        .outerjoin(DeviceEstadoActual, DeviceEstadoActual.dispositivo_id == Dispositivo.id)
        .filter(Dispositivo.activo == True)  # noqa: E712  (los archivados no aparecen)
        .all()
    )

    dispositivos = []
    resumen = {"total": 0, "verde": 0, "amarillo": 0, "rojo": 0, "inactivos": 0}
    activos_por_empresa: dict[int, int] = {}
    inactivos_por_empresa: dict[int, int] = {}
    no_rec = _conteo_no_reconocido(db, [disp.id for disp, _, _ in filas])

    for disp, emp, dea in filas:
        uh = _a_utc(dea.ultimo_heartbeat) if dea else None
        minutos = _minutos_desde(uh)
        referencia = uh or _a_utc(disp.creado_en)
        inactivo = referencia is None or (ahora - referencia).total_seconds() > DIAS_INACTIVO * 86400
        listener = dea.listener_conectado if dea else None
        avisos = no_rec.get(disp.id, 0)
        estado, motivo = _estado_unico(minutos, dea.estado if dea else None, listener, avisos)

        dispositivos.append({
            "dispositivo_id": disp.id,
            "empresa_id": emp.id,
            "empresa": emp.nombre_comercial or emp.razon_social,
            "nombre": disp.nombre,
            "estado": estado,
            "motivo": motivo,
            "estado_reportado": dea.estado if dea else None,
            "listener_conectado": listener,
            "permiso_notificaciones": dea.permiso_notificaciones if dea else None,
            "ultimo_pago_capturado": _fmt_utc(disp.ultimo_pago_capturado),
            "avisos_no_reconocidos": avisos,
            "inactivo": inactivo,
            "manufacturer": dea.manufacturer if dea else None,
            "modelo": dea.modelo if dea else None,
            "android_version": dea.android_version if dea else None,
            "app_version": (dea.app_version if dea else None) or disp.app_version,
            "ultimo_heartbeat": uh.strftime("%Y-%m-%dT%H:%M:%SZ") if uh else None,
            "minutos_desde_ultimo_heartbeat": int(minutos) if minutos is not None else None,
            "veces_zombie_total": (dea.veces_zombie_total if dea else 0) or 0,
            "veces_alarma_disparada": (dea.veces_alarma_disparada if dea else 0) or 0,
            "pings_fallidos_consecutivos": (dea.pings_fallidos_consecutivos if dea else 0) or 0,
            "_orden": (-uh.timestamp()) if uh else float("inf"),
        })

        if inactivo:
            resumen["inactivos"] += 1
            inactivos_por_empresa[emp.id] = inactivos_por_empresa.get(emp.id, 0) + 1
        else:
            resumen["total"] += 1
            resumen[estado] += 1
            activos_por_empresa[emp.id] = activos_por_empresa.get(emp.id, 0) + 1

    dispositivos.sort(key=lambda d: ((d["empresa"] or "").casefold(), d["empresa_id"], d["_orden"]))
    for d in dispositivos:
        del d["_orden"]

    empresas_sin_equipos = [
        {"empresa_id": e.id, "empresa": e.nombre_comercial or e.razon_social, "slug": e.slug,
         "equipos_inactivos": inactivos_por_empresa.get(e.id, 0)}
        for e in db.query(Empresa).all() if not activos_por_empresa.get(e.id)
    ]
    empresas_sin_equipos.sort(key=lambda e: (e["empresa"] or "").casefold())
    return {
        "dispositivos": dispositivos, "resumen": resumen, "empresas_sin_equipos": empresas_sin_equipos,
        "umbrales": {"amarillo_min": UMBRAL_AMARILLO_MIN, "rojo_min": UMBRAL_ROJO_MIN,
                     "inactivo_dias": DIAS_INACTIVO},
    }


def _a_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _token_admin_valido(token: Optional[str]) -> bool:
    return superadmin.token_admin_valido(token)


def _autorizado(request: Request, x_admin_token: Optional[str]) -> bool:
    """Admin global por header X-Admin-Token (scripts) o por la cookie de
    sesión de superadmin (el dashboard la usa tras entrar por POST)."""
    return superadmin.autorizado(request, x_admin_token)


# =============================================================
# ENDPOINT 2: GET /api/v1/admin/dispositivos (JSON)
# =============================================================

@router.get("/api/v1/admin/dispositivos")
def admin_dispositivos_json(
    request: Request,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    if not _autorizado(request, x_admin_token):
        raise HTTPException(401, "No autorizado")
    return _obtener_dispositivos(db)


@router.post("/api/v1/admin/dispositivos/{dispositivo_id}/archivar")
def admin_archivar_dispositivo(
    dispositivo_id: int,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    """Archiva un equipo inactivo: Dispositivo.activo = False (no se borra nada;
    su token deja de funcionar). Se revierte desde /admin/dispositivos de la
    empresa. Solo equipos sin heartbeat hace más de DIAS_INACTIVO días."""
    if not _autorizado(request, x_admin_token):
        raise HTTPException(401, "No autorizado")
    disp = db.get(Dispositivo, dispositivo_id)
    if disp is None or not disp.activo:
        raise HTTPException(404, "Equipo no encontrado o ya archivado")
    fila = next((d for d in _obtener_dispositivos(db)["dispositivos"] if d["dispositivo_id"] == disp.id), None)
    if fila is None or not fila["inactivo"]:
        raise HTTPException(409, f"Solo se archivan equipos sin heartbeat hace más de {DIAS_INACTIVO} días")
    disp.activo = False
    db.commit()
    logger.info(f"Dispositivo {disp.id} (empresa {disp.empresa_id}) archivado desde el panel de estado")
    return {"ok": True, "dispositivo_id": disp.id}


# =============================================================
# ENDPOINT: GET /api/v1/dispositivos/estado  (API de clientes, X-API-Key)
# =============================================================
# El cliente, con SU API key, ve SOLO los equipos de SU empresa y detecta el
# incidente 06-11 por su cuenta: `listener_conectado=false` o un `estado` rojo
# aunque el equipo siga "vivo". Aislamiento: empresa_id sale de la credencial.

@router.get("/api/v1/dispositivos/estado")
def dispositivos_estado_cliente(
    cuenta: CuentaApi = Depends(cuenta_actual),
    db: Session = Depends(get_db),
):
    """Estado de los equipos de la empresa dueña de la API key.

    Respuesta (contrato): lista JSON ordenada del peor estado al mejor:
      [{id, nombre, estado, motivo, ultimo_heartbeat, listener_conectado,
        ultimo_pago_capturado, avisos_no_reconocidos}]
      - estado: "verde" | "amarillo" | "rojo"
      - listener_conectado: true | false | null (equipos con app vieja: null)
      - avisos_no_reconocidos: avisos de billetera sin interpretar en las
        últimas 24 h (0 si ninguno)
      - fechas en UTC ISO-8601 con sufijo Z, o null
    """
    filas = (
        db.query(Dispositivo, DeviceEstadoActual)
        .outerjoin(DeviceEstadoActual, DeviceEstadoActual.dispositivo_id == Dispositivo.id)
        .filter(
            Dispositivo.empresa_id == cuenta.empresa_id,
            Dispositivo.activo == True,  # noqa: E712
        )
        .all()
    )

    no_rec = _conteo_no_reconocido(db, [disp.id for disp, _ in filas])
    salida = []
    for disp, dea in filas:
        uh = _a_utc(dea.ultimo_heartbeat) if dea else None
        minutos = _minutos_desde(uh)
        listener = dea.listener_conectado if dea else None
        avisos = no_rec.get(disp.id, 0)
        estado, motivo = _estado_unico(minutos, dea.estado if dea else None, listener, avisos)
        salida.append({
            "id": disp.id,
            "nombre": disp.nombre,
            "estado": estado,
            "motivo": motivo,
            "ultimo_heartbeat": _fmt_utc(uh),
            "listener_conectado": listener,
            "ultimo_pago_capturado": _fmt_utc(disp.ultimo_pago_capturado),
            "avisos_no_reconocidos": avisos,
        })

    # Peor estado primero (rojo > amarillo > verde); desempate por nombre.
    salida.sort(key=lambda d: (-_GRAVEDAD.get(d["estado"], 0), (d["nombre"] or "").casefold()))
    return salida


# =============================================================
# ENDPOINT 3: GET /admin/dispositivos/estado (HTML)
# =============================================================

@router.get("/admin/dispositivos/estado", response_class=HTMLResponse)
def admin_dispositivos_html(
    request: Request,
    db: Session = Depends(get_db),
):
    # Sesión de superadmin por cookie firmada (el token ya NO viaja en la URL).
    if not superadmin.sesion_valida(request):
        return HTMLResponse(_html_login(), status_code=401)
    datos = _obtener_dispositivos(db)
    return HTMLResponse(_html_dashboard(datos))


# =============================================================
# HTML (autocontenido, paleta "Amazonía Sereno")
# =============================================================

def _html_login() -> str:
    return superadmin.html_login("/admin/dispositivos/estado", "Estado de dispositivos")


def _html_dashboard(datos: dict) -> str:
    import json as _json
    plantilla = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estado de dispositivos · pagoOK</title>
<style>
  :root { --jade:#0f766e; --coral:#ea6d40; --fondo:#faf6ef; --grafito:#1c1f26; --suave:#4b5563; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Inter',system-ui,sans-serif; background:var(--fondo); color:var(--grafito); padding:16px; }
  .cont { max-width:1000px; margin:0 auto; }
  h1 { font-size:20px; color:var(--jade); margin-bottom:8px; }
  .resumen { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
  .chip { border-radius:999px; padding:6px 14px; font-size:13px; font-weight:600; }
  .c-verde { background:#dcfce7; color:#166534; }
  .c-amarillo { background:#fef3c7; color:#854d0e; }
  .c-rojo { background:#fee2e2; color:#991b1b; }
  .c-total { background:#e2e8f0; color:#334155; }
  .c-sin { background:#e5e7eb; color:#4b5563; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 1px 8px rgba(28,31,38,.06); }
  th, td { text-align:left; padding:9px 12px; font-size:13px; border-bottom:1px solid #f0ece3; }
  th { background:#f7f3ea; color:var(--suave); font-weight:600; }
  tr.rojo { background:#fee2e2; }
  tr.amarillo { background:#fef3c7; }
  tr.inactivo { background:#f3f4f6; color:#6b7280; }
  td.empresa { font-weight:600; }
  .badge { border-radius:999px; padding:2px 9px; font-size:11px; font-weight:700; text-transform:capitalize; }
  .b-verde { background:#16a34a; color:#fff; } .b-amarillo { background:#ca8a04; color:#fff; }
  .b-rojo { background:#dc2626; color:#fff; } .b-inactivo { background:#9ca3af; color:#fff; }
  .motivo { display:block; font-size:11px; color:var(--suave); margin-top:2px; }
  .barra { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
  .barra button, td button { border:1px solid #cbd5e1; background:#fff; border-radius:8px; padding:6px 12px; font-size:13px; cursor:pointer; }
  td button { padding:3px 9px; font-size:12px; }
  .salir { margin-left:8px; font-size:13px; color:var(--coral); text-decoration:none; }
  .sin-pago { color:#b91c1c; font-weight:600; }
  .umbrales { font-size:12px; color:var(--suave); }
  h2 { font-size:15px; color:var(--jade); margin:22px 0 8px; }
  .tabla-scroll { overflow-x:auto; }
  .sin-equipos { background:#fff; border-radius:12px; padding:4px 0; box-shadow:0 1px 8px rgba(28,31,38,.06); }
  .sin-equipos li { list-style:none; padding:8px 14px; font-size:13px; border-bottom:1px solid #f0ece3; }
  .sin-equipos li:last-child { border-bottom:none; }
  .sin-equipos small { color:var(--suave); }
  .vacio { font-size:13px; color:var(--suave); padding:10px 0; }
  .actualizado { font-size:12px; color:var(--suave); margin-top:12px; text-align:center; }
</style></head>
<body><div class="cont">
  <h1>Estado de dispositivos</h1>
  <div class="resumen" id="resumen"></div>
  <div class="barra">
    <span class="umbrales" id="umbrales"></span>
    <span>
      <button type="button" id="btn-inactivos">Mostrar inactivos</button>
      <a class="salir" href="/admin/salir">Salir</a>
    </span>
  </div>
  <div class="tabla-scroll"><table>
    <thead><tr>
      <th>Empresa</th><th>Equipo</th><th>Android</th><th>App</th>
      <th>Estado</th><th>Último heartbeat</th><th>Último pago</th><th>Sin interpretar</th><th>Zombies</th><th>Alarmas</th><th></th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table></div>
  <h2>Empresas sin equipos</h2>
  <div id="sin-equipos"></div>
  <div class="actualizado" id="actualizado"></div>
</div>
<script>
let datos = __DATOS_JSON__;
let verInactivos = false;

function esc(s){ return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function relativo(min) {
  if (min == null) return "Nunca";
  if (min < 1) return "hace segundos";
  if (min < 60) return "hace " + min + " min";
  const h = Math.floor(min/60);
  if (h < 48) return "hace " + h + " h";
  return "hace " + Math.floor(h/24) + " días";
}
function ultimoPago(iso) {
  if (!iso) return '<span class="sin-pago">Ninguno</span>';
  const min = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  return relativo(min < 0 ? 0 : min);
}
function sinInterpretar(n) {
  return n ? '<span class="sin-pago" title="Avisos de billetera que la app no supo leer (últimas 24 h)">'+n+'</span>' : '0';
}
function equipo(d) {
  const partes = [d.manufacturer, d.modelo].filter(Boolean);
  return esc(partes.length ? partes.join(" ") : (d.nombre || "Equipo " + d.dispositivo_id));
}
function pintar() {
  const r = datos.resumen, u = datos.umbrales;
  document.getElementById("resumen").innerHTML =
    '<span class="chip c-total">'+r.total+' equipos activos</span>' +
    '<span class="chip c-verde">'+r.verde+' verdes</span>' +
    '<span class="chip c-amarillo">'+r.amarillo+' amarillos</span>' +
    '<span class="chip c-rojo">'+r.rojo+' rojos</span>' +
    '<span class="chip c-sin">'+r.inactivos+' inactivos</span>';
  document.getElementById("umbrales").textContent =
    "Verde: heartbeat hace menos de "+u.amarillo_min+" min · amarillo: "+u.amarillo_min+"–"+u.rojo_min+
    " min · rojo: más de "+u.rojo_min+" min · inactivo: más de "+u.inactivo_dias+" días";
  const btn = document.getElementById("btn-inactivos");
  btn.textContent = (verInactivos ? "Ocultar inactivos" : "Mostrar inactivos") + " (" + r.inactivos + ")";
  const filas = datos.dispositivos.filter(d => verInactivos || !d.inactivo);
  let anterior = null;
  document.getElementById("tbody").innerHTML = filas.length ? filas.map(d => {
    const clase = d.inactivo ? "inactivo" : (d.estado === "verde" ? "" : d.estado);
    const badge = d.inactivo
      ? '<span class="badge b-inactivo">Inactivo</span>'
      : '<span class="badge b-'+d.estado+'">'+esc(d.estado)+'</span><span class="motivo">'+esc(d.motivo)+'</span>';
    const empresa = d.empresa_id === anterior ? "" : esc(d.empresa);
    anterior = d.empresa_id;
    const accion = d.inactivo ? '<button type="button" onclick="archivar('+d.dispositivo_id+')">Archivar</button>' : "";
    return "<tr class='"+clase+"'>" +
      "<td class='empresa'>"+empresa+"</td>" +
      "<td>"+equipo(d)+"</td>" +
      "<td>"+esc(d.android_version || "")+"</td>" +
      "<td>"+esc(d.app_version || "")+"</td>" +
      "<td>"+badge+"</td>" +
      "<td>"+relativo(d.minutos_desde_ultimo_heartbeat)+"</td>" +
      "<td>"+ultimoPago(d.ultimo_pago_capturado)+"</td>" +
      "<td>"+sinInterpretar(d.avisos_no_reconocidos)+"</td>" +
      "<td>"+d.veces_zombie_total+"</td>" +
      "<td>"+d.veces_alarma_disparada+"</td>" +
      "<td>"+accion+"</td>" +
    "</tr>";
  }).join("") : "<tr><td colspan='11' class='vacio'>No hay equipos activos.</td></tr>";
  const sin = datos.empresas_sin_equipos;
  document.getElementById("sin-equipos").innerHTML = sin.length
    ? '<ul class="sin-equipos">' + sin.map(e => "<li>"+esc(e.empresa)+" <small>("+esc(e.slug)+")"+
        (e.equipos_inactivos ? " · "+e.equipos_inactivos+" equipo(s) inactivo(s)" : " · ningún equipo registrado")+
        "</small></li>").join("") + "</ul>"
    : '<p class="vacio">Todas las empresas tienen al menos un equipo activo.</p>';
  document.getElementById("actualizado").textContent = "Actualizado " + new Date().toLocaleTimeString("es-PE");
}
async function refrescar() {
  try {
    const r = await fetch("/api/v1/admin/dispositivos", { credentials: "same-origin" });
    if (r.status === 401) { location.reload(); return; }   // sesión vencida -> login
    if (r.ok) { datos = await r.json(); pintar(); }
  } catch (e) {}
}
async function archivar(id) {
  if (!confirm("¿Archivar este equipo? Su token deja de funcionar; no se borra nada y se puede reactivar desde el panel de dispositivos de la empresa.")) return;
  const r = await fetch("/api/v1/admin/dispositivos/" + id + "/archivar", { method: "POST", credentials: "same-origin" });
  if (!r.ok) { alert("No se pudo archivar: " + ((await r.json().catch(() => ({}))).detail || r.status)); return; }
  refrescar();
}
document.getElementById("btn-inactivos").addEventListener("click", () => { verInactivos = !verInactivos; pintar(); });
pintar();
setInterval(refrescar, 30000);
</script>
</body></html>"""
    return plantilla.replace("__DATOS_JSON__", _json.dumps(datos))
