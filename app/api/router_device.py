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
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import CodigoInvitacion, Empresa
from app.api.models_device import DeviceEstadoActual
from app.api.models_webhook import Dispositivo
from app.api.deps_empresa import resolver_dispositivo
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

    # 4) Log según estado.
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


def _estado_unico(minutos: Optional[float], reportado: Optional[str]) -> tuple[str, str]:
    """Un solo estado por equipo: el peor entre la conexión y lo que el propio
    equipo reportó en su último heartbeat. Devuelve (estado, motivo)."""
    conexion = estado_conexion(minutos)
    if minutos is None:
        return "rojo", "Nunca envió un heartbeat"
    if conexion != "verde":
        return conexion, "Sin heartbeat reciente"
    if reportado in ("amarillo", "rojo"):
        return reportado, "El equipo reporta un problema de captura"
    return "verde", "Conectado"


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

    for disp, emp, dea in filas:
        uh = _a_utc(dea.ultimo_heartbeat) if dea else None
        minutos = _minutos_desde(uh)
        referencia = uh or _a_utc(disp.creado_en)
        inactivo = referencia is None or (ahora - referencia).total_seconds() > DIAS_INACTIVO * 86400
        estado, motivo = _estado_unico(minutos, dea.estado if dea else None)

        dispositivos.append({
            "dispositivo_id": disp.id,
            "empresa_id": emp.id,
            "empresa": emp.nombre_comercial or emp.razon_social,
            "nombre": disp.nombre,
            "estado": estado,
            "motivo": motivo,
            "estado_reportado": dea.estado if dea else None,
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
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    return bool(admin_token) and secrets.compare_digest((token or "").encode(), admin_token.encode())


# =============================================================
# ENDPOINT 2: GET /api/v1/admin/dispositivos (JSON)
# =============================================================

@router.get("/api/v1/admin/dispositivos")
def admin_dispositivos_json(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    if not _token_admin_valido(x_admin_token):
        raise HTTPException(401, "No autorizado")
    return _obtener_dispositivos(db)


@router.post("/api/v1/admin/dispositivos/{dispositivo_id}/archivar")
def admin_archivar_dispositivo(
    dispositivo_id: int,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
):
    """Archiva un equipo inactivo: Dispositivo.activo = False (no se borra nada;
    su token deja de funcionar). Se revierte desde /admin/dispositivos de la
    empresa. Solo equipos sin heartbeat hace más de DIAS_INACTIVO días."""
    if not _token_admin_valido(x_admin_token):
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
# ENDPOINT 3: GET /admin/dispositivos/estado (HTML)
# =============================================================

@router.get("/admin/dispositivos/estado", response_class=HTMLResponse)
def admin_dispositivos_html(
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    # MVP: token por query string. TODO(seguridad): migrar a cookie firmada.
    if not _token_admin_valido(token):
        return HTMLResponse(_html_login())
    datos = _obtener_dispositivos(db)
    return HTMLResponse(_html_dashboard(datos, token))


# =============================================================
# HTML (autocontenido, paleta "Amazonía Sereno")
# =============================================================

def _html_login() -> str:
    return """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Estado de dispositivos · pagoOK</title>
<style>
  body { font-family:'Inter',system-ui,sans-serif; background:#faf6ef; color:#1c1f26; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
  .caja { background:#fff; border-radius:16px; padding:28px; box-shadow:0 2px 14px rgba(28,31,38,.1); max-width:360px; width:90%; }
  h1 { font-size:18px; color:#0f766e; margin:0 0 6px; }
  p { font-size:13px; color:#4b5563; margin:0 0 16px; }
  input { width:100%; border:1px solid #ddd; border-radius:8px; padding:10px 12px; font-size:14px; }
  button { width:100%; margin-top:12px; background:#0f766e; color:#fff; border:none; border-radius:8px; padding:11px; font-weight:700; cursor:pointer; }
</style></head>
<body><div class="caja">
  <h1>Estado de dispositivos</h1>
  <p>Ingresa el token de administrador para continuar.</p>
  <form method="get" action="/admin/dispositivos/estado">
    <input type="password" name="token" placeholder="Token de administrador" autofocus>
    <button type="submit">Entrar</button>
  </form>
</div></body></html>"""


def _html_dashboard(datos: dict, token: str) -> str:
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
    <button type="button" id="btn-inactivos">Mostrar inactivos</button>
  </div>
  <div class="tabla-scroll"><table>
    <thead><tr>
      <th>Empresa</th><th>Equipo</th><th>Android</th><th>App</th>
      <th>Estado</th><th>Último heartbeat</th><th>Zombies</th><th>Alarmas</th><th></th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table></div>
  <h2>Empresas sin equipos</h2>
  <div id="sin-equipos"></div>
  <div class="actualizado" id="actualizado"></div>
</div>
<script>
const TOKEN = __TOKEN_JSON__;
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
      "<td>"+d.veces_zombie_total+"</td>" +
      "<td>"+d.veces_alarma_disparada+"</td>" +
      "<td>"+accion+"</td>" +
    "</tr>";
  }).join("") : "<tr><td colspan='9' class='vacio'>No hay equipos activos.</td></tr>";
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
    const r = await fetch("/api/v1/admin/dispositivos", { headers: { "X-Admin-Token": TOKEN } });
    if (r.ok) { datos = await r.json(); pintar(); }
  } catch (e) {}
}
async function archivar(id) {
  if (!confirm("¿Archivar este equipo? Su token deja de funcionar; no se borra nada y se puede reactivar desde el panel de dispositivos de la empresa.")) return;
  const r = await fetch("/api/v1/admin/dispositivos/" + id + "/archivar", { method: "POST", headers: { "X-Admin-Token": TOKEN } });
  if (!r.ok) { alert("No se pudo archivar: " + ((await r.json().catch(() => ({}))).detail || r.status)); return; }
  refrescar();
}
document.getElementById("btn-inactivos").addEventListener("click", () => { verInactivos = !verInactivos; pintar(); });
pintar();
setInterval(refrescar, 30000);
</script>
</body></html>"""
    return (plantilla
            .replace("__TOKEN_JSON__", _json.dumps(token))
            .replace("__DATOS_JSON__", _json.dumps(datos)))
