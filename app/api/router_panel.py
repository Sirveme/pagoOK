"""Panel web multi-empresa: ingreso con OTP por WhatsApp, equipo, dispositivos.

Ingreso:
  POST /panel/login/otp        {celular}           -> misma respuesta exista o no
  POST /panel/login/verificar  {celular, codigo}   -> cookie de sesión (30 días)
  POST /panel/empresa          {empresa_id}        -> cambia de empresa (varias membresías)
  POST /panel/logout

Páginas: /panel, /panel/login, /panel/elegir-empresa, /panel/dispositivos,
         /panel/equipo, /unirse/{token}

API del panel (cookie de sesión; permisos por rol en el backend):
  GET    /api/v1/panel/yo
  GET    /api/v1/panel/pagos/hoy                 ver_pagos
  POST   /api/v1/panel/pagos/buscar              buscar_pago (almacén: sin listado)
  POST   /api/v1/panel/pagos/{id}/marcar-usado   marcar_usado
  GET    /api/v1/panel/reportes/movimientos.csv  reportes
  GET    /api/v1/panel/dispositivos              administrar
  POST   /api/v1/panel/dispositivos/codigo       administrar
  POST   /api/v1/panel/invitaciones              administrar
  GET    /api/v1/panel/equipo                    administrar
  DELETE /api/v1/panel/equipo/{usuario_id}       administrar
  GET/POST/DELETE /api/v1/panel/api-keys         administrar

Con la cuenta vencida o suspendida el panel es de SOLO LECTURA (402 en las
acciones que cambian datos; quitar acceso y cerrar sesión siguen permitidos).
"""
from __future__ import annotations

import csv
import io
import logging
import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.deps_empresa import (
    PERMISOS, SesionCtx, borrar_cookie, crear_sesion, exigir_cuenta_activa, poner_cookie,
    requiere, revocar_sesiones, sesion_opcional, usuario_y_empresa_desde_sesion,
)
from app.api.models_cuenta import CuentaApi, generar_api_key, hash_api_key, prefijo_visible
from app.api.models_device import DeviceEstadoActual
from app.api.models_multiempresa import (
    ROLES, CodigoVinculacion, Invitacion, Membresia, Usuario,
)
from app.api.models_webhook import Dispositivo, PagoDetectado
from app.services import cuenta_empresa
from app.services.conciliacion import (
    PagoNoEncontrado, PagoYaUsado, consultar_pago, marcar_usado_panel, serializar_pago,
)
from app.services.eventos import TZ_LIMA, a_iso_lima, monto_texto
from app.services.otp import OtpDemasiados, OtpNoDisponible, emitir_otp, obtener_enviador, verificar_otp
from app.services.secretos import (
    generar_codigo_vinculacion, generar_token, hash_codigo, hash_token,
    normalizar_celular, normalizar_codigo_vinculacion,
)

logger = logging.getLogger("pagook")
router = APIRouter(tags=["panel"])
templates = Jinja2Templates(directory="templates")

VIGENCIA_CODIGO_VINCULACION = timedelta(minutes=10)
VIGENCIA_INVITACION = timedelta(hours=24)
MENSAJE_OTP_ENVIADO = "Si el número está registrado, te enviaremos un código por WhatsApp."
MENSAJE_OTP_INVALIDO = "Código incorrecto o vencido."

ETIQUETA_ROL = {"dueno": "Dueño", "cajero": "Cajero", "almacen": "Almacén", "contador": "Contador"}


def url_publica() -> str:
    return os.getenv("PAGOOK_URL_PUBLICA", "https://pagook.pro").rstrip("/")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---- Límite simple por IP para el ingreso (en memoria) ----
_lock_ip = threading.Lock()
_intentos_ip: dict[str, list[float]] = {}
LIMITES_IP = {"otp": (10, 600), "verificar": (20, 600)}


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip() if xff else (request.client.host if request.client else "?")


def _limitar_ip(request: Request, accion: str) -> None:
    maximo, ventana = LIMITES_IP[accion]
    clave, ahora = f"{accion}:{_ip(request)}", time.time()
    with _lock_ip:
        lista = [t for t in _intentos_ip.get(clave, []) if t > ahora - ventana]
        lista.append(ahora)
        _intentos_ip[clave] = lista
    if len(lista) > maximo:
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")


def reiniciar_limites_panel() -> None:
    with _lock_ip:
        _intentos_ip.clear()


def _membresias_activas(db: Session, usuario_id: int) -> list[Membresia]:
    return (
        db.query(Membresia)
        .filter(Membresia.usuario_id == usuario_id, Membresia.activo == True)  # noqa: E712
        .order_by(Membresia.id)
        .all()
    )


def _empresas_de(db: Session, usuario_id: int) -> list[dict]:
    salida = []
    for m in _membresias_activas(db, usuario_id):
        e = db.get(Empresa, m.empresa_id)
        if e is not None:
            salida.append({"empresa_id": e.id, "nombre": e.nombre_comercial or e.razon_social,
                           "rol": m.rol, "rol_etiqueta": ETIQUETA_ROL.get(m.rol, m.rol)})
    return salida


def _celular_o_400(valor) -> str:
    cel = normalizar_celular(str(valor or ""))
    if cel is None:
        raise HTTPException(400, "Celular inválido. Escríbelo con código de país, ej. +51987654321.")
    return cel


# =============================================================
# INGRESO CON OTP
# =============================================================

@router.post("/panel/login/otp")
def login_otp(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    _limitar_ip(request, "otp")
    celular = _celular_o_400(payload.get("celular"))
    try:
        enviador = obtener_enviador()  # 503 ANTES de mirar el celular: no revela nada
    except OtpNoDisponible:
        return JSONResponse({"detail": "otp_no_disponible"}, status_code=503)

    usuario = db.query(Usuario).filter(Usuario.celular == celular, Usuario.activo == True).first()  # noqa: E712
    if usuario is not None and _membresias_activas(db, usuario.id):
        try:
            emitir_otp(db, celular, "login", enviador)
        except OtpDemasiados:
            logger.warning("Login OTP: demasiados códigos pedidos para un celular registrado")
        except Exception as exc:  # el envío falló: misma respuesta, se registra el error
            logger.error(f"Login OTP: no se pudo enviar ({type(exc).__name__})")
    # Misma respuesta exista o no el celular.
    return {"ok": True, "mensaje": MENSAJE_OTP_ENVIADO}


@router.post("/panel/login/verificar")
def login_verificar(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    _limitar_ip(request, "verificar")
    celular = _celular_o_400(payload.get("celular"))
    if not verificar_otp(db, celular, str(payload.get("codigo") or ""), "login"):
        raise HTTPException(400, MENSAJE_OTP_INVALIDO)
    usuario = db.query(Usuario).filter(Usuario.celular == celular, Usuario.activo == True).first()  # noqa: E712
    empresas = _empresas_de(db, usuario.id) if usuario else []
    if not empresas:
        raise HTTPException(400, MENSAJE_OTP_INVALIDO)
    token = crear_sesion(db, usuario.id, empresas[0]["empresa_id"])
    varias = len(empresas) > 1
    resp = JSONResponse({
        "ok": True,
        "empresas": empresas,
        "requiere_elegir": varias,
        "redirigir": "/panel/elegir-empresa" if varias else "/panel",
    })
    poner_cookie(resp, token)
    return resp


@router.post("/panel/empresa")
def cambiar_empresa(
    payload: dict = Body(...),
    ctx: SesionCtx = Depends(usuario_y_empresa_desde_sesion),
    db: Session = Depends(get_db),
):
    try:
        empresa_id = int(payload.get("empresa_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "empresa_id inválido")
    if empresa_id not in {e["empresa_id"] for e in _empresas_de(db, ctx.usuario.id)}:
        raise HTTPException(403, "sin_permiso")
    ctx.sesion.revocada_en = _ahora()
    db.commit()
    resp = JSONResponse({"ok": True, "redirigir": "/panel"})
    poner_cookie(resp, crear_sesion(db, ctx.usuario.id, empresa_id))
    return resp


@router.post("/panel/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is not None:
        ctx.sesion.revocada_en = _ahora()
        db.commit()
    resp = JSONResponse({"ok": True, "redirigir": "/panel/login"})
    borrar_cookie(resp)
    return resp


# =============================================================
# API DEL PANEL
# =============================================================

def _yo(ctx: SesionCtx, db: Session) -> dict:
    return {
        "usuario": {"id": ctx.usuario.id, "nombre": ctx.usuario.nombre},
        "empresa": {
            "id": ctx.empresa.id, "slug": ctx.empresa.slug,
            "nombre": ctx.empresa.nombre_comercial or ctx.empresa.razon_social,
            "razon_social": ctx.empresa.razon_social, "id_fiscal": ctx.empresa.id_fiscal,
            "id_fiscal_tipo": ctx.empresa.id_fiscal_tipo, "pais": ctx.empresa.pais_codigo,
        },
        "rol": ctx.rol,
        "rol_etiqueta": ETIQUETA_ROL.get(ctx.rol, ctx.rol),
        "permisos": sorted(p for p in PERMISOS if ctx.puede(p)),
        "cuenta": cuenta_empresa.info_cuenta(db, ctx.empresa),
        "solo_lectura": ctx.bloqueada,
        "varias_empresas": len(_empresas_de(db, ctx.usuario.id)) > 1,
    }


@router.get("/api/v1/panel/yo")
def api_yo(ctx: SesionCtx = Depends(usuario_y_empresa_desde_sesion), db: Session = Depends(get_db)):
    return _yo(ctx, db)


def _rango_dia_lima(dia: date) -> tuple[datetime, datetime]:
    """Inicio y fin del día en Lima, como UTC naive (así se guarda recibido_en)."""
    inicio = datetime.combine(dia, dtime.min, tzinfo=TZ_LIMA).astimezone(timezone.utc).replace(tzinfo=None)
    return inicio, inicio + timedelta(days=1)


@router.get("/api/v1/panel/pagos/hoy")
def pagos_hoy(ctx: SesionCtx = Depends(requiere("ver_pagos")), db: Session = Depends(get_db)):
    desde, hasta = _rango_dia_lima(datetime.now(TZ_LIMA).date())
    pagos = (
        db.query(PagoDetectado)
        .filter(PagoDetectado.empresa_id == ctx.empresa.id, PagoDetectado.tipo == "ingreso",
                PagoDetectado.tipo_incierto == False,  # noqa: E712
                PagoDetectado.recibido_en >= desde, PagoDetectado.recibido_en < hasta)
        .order_by(PagoDetectado.recibido_en.desc())
        .all()
    )
    total = sum((Decimal(str(p.monto)) for p in pagos), Decimal("0"))
    return {"pagos": [serializar_pago(p) for p in pagos], "cantidad": len(pagos), "total": monto_texto(total)}


@router.post("/api/v1/panel/pagos/buscar")
def buscar_pago(
    payload: dict = Body(...),
    ctx: SesionCtx = Depends(requiere("buscar_pago")),
    db: Session = Depends(get_db),
):
    try:
        monto = Decimal(str(payload.get("monto", "")).strip().replace(",", "."))
        if not monto.is_finite() or monto <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise HTTPException(400, "Monto inválido")
    fecha = payload.get("fecha_hora")
    try:
        cuando = datetime.fromisoformat(str(fecha).replace("Z", "+00:00")) if fecha else datetime.now(TZ_LIMA)
    except ValueError:
        raise HTTPException(400, "Fecha y hora inválidas")
    try:
        ventana = max(1, min(1440, int(payload.get("ventana_minutos") or 30)))
    except (TypeError, ValueError):
        ventana = 30
    r = consultar_pago(
        db, empresa_id=ctx.empresa.id, cuenta_id=None, monto=monto, fecha_hora_declarada=cuando,
        ventana_minutos=ventana,
        nombre_pagador_declarado=payload.get("nombre_pagador_declarado"),
        codigo_operacion=payload.get("codigo_operacion"),
    )
    if not ctx.puede("ver_pagos"):
        # Almacén: solo si existe o no; sin listado, pagadores ni totales.
        return {"existe": r["nivel"] == "alta", "nivel": r["nivel"], "motivo": r["motivo"],
                "accion_sugerida": r["accion_sugerida"], "mensaje": r["mensaje"]}
    return r


@router.post("/api/v1/panel/pagos/{pago_id}/marcar-usado")
def panel_marcar_usado(
    pago_id: int,
    payload: dict = Body(...),
    ctx: SesionCtx = Depends(requiere("marcar_usado")),
    db: Session = Depends(get_db),
):
    exigir_cuenta_activa(ctx)
    referencia = str(payload.get("referencia_externa") or "").strip()
    if not referencia:
        raise HTTPException(400, "Indica para qué usas el pago (referencia_externa)")
    try:
        return marcar_usado_panel(db, empresa_id=ctx.empresa.id, usuario_nombre=ctx.usuario.nombre,
                                  pago_id=pago_id, referencia_externa=referencia[:150])
    except PagoNoEncontrado:
        raise HTTPException(404, "Pago no encontrado")
    except PagoYaUsado as exc:
        raise HTTPException(409, str(exc))


@router.get("/api/v1/panel/reportes/movimientos.csv")
def reporte_csv(
    desde: str | None = None, hasta: str | None = None,
    ctx: SesionCtx = Depends(requiere("reportes")),
    db: Session = Depends(get_db),
):
    hoy = datetime.now(TZ_LIMA).date()
    try:
        d = date.fromisoformat(desde) if desde else hoy.replace(day=1)
        h = date.fromisoformat(hasta) if hasta else hoy
    except ValueError:
        raise HTTPException(400, "Fechas inválidas (usa AAAA-MM-DD)")
    ini, _ = _rango_dia_lima(d)
    _, fin = _rango_dia_lima(h)
    filas = (
        db.query(PagoDetectado)
        .filter(PagoDetectado.empresa_id == ctx.empresa.id, PagoDetectado.tipo_incierto == False,  # noqa: E712
                PagoDetectado.recibido_en >= ini, PagoDetectado.recibido_en < fin)
        .order_by(PagoDetectado.recibido_en)
        .all()
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha_hora", "tipo", "canal", "institucion", "contraparte", "monto", "moneda",
                "codigo_operacion", "cuenta_propia"])
    for p in filas:
        s = serializar_pago(p)
        w.writerow([s["fecha_hora"], p.tipo, s["canal"], s["institucion"], s["contraparte"], s["monto"],
                    s["moneda"], s["codigo_operacion"] or "",
                    "si" if p.confirmacion_usuario == "confirmado_interno" else "no"])
    nombre = f"pagook_{ctx.empresa.slug}_{d.isoformat()}_{h.isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ---- Dispositivos y vinculación (dueño) ----

@router.get("/api/v1/panel/dispositivos")
def panel_dispositivos(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    filas = (
        db.query(Dispositivo, DeviceEstadoActual)
        .outerjoin(DeviceEstadoActual, DeviceEstadoActual.dispositivo_id == Dispositivo.id)
        .filter(Dispositivo.empresa_id == ctx.empresa.id)
        .order_by(Dispositivo.id.desc())
        .all()
    )
    return {"dispositivos": [{
        "id": d.id, "nombre": d.nombre, "activo": bool(d.activo),
        "estado": e.estado if e else None,
        "ultimo_heartbeat": a_iso_lima(e.ultimo_heartbeat) if e and e.ultimo_heartbeat else None,
        "app_version": (e.app_version if e else None) or d.app_version,
    } for d, e in filas]}


@router.post("/api/v1/panel/dispositivos/codigo")
def panel_codigo_vinculacion(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    exigir_cuenta_activa(ctx)
    codigo = generar_codigo_vinculacion()
    expira = _ahora() + VIGENCIA_CODIGO_VINCULACION
    db.add(CodigoVinculacion(
        empresa_id=ctx.empresa.id, creado_por=ctx.usuario.id, expira_en=expira,
        codigo_hash=hash_codigo(normalizar_codigo_vinculacion(codigo), "vinculacion"),
    ))
    db.commit()
    return {"codigo": codigo, "url_qr": f"pagook://vincular?c={codigo}",
            "expira_en": a_iso_lima(expira), "vigencia_minutos": 10}


# ---- Equipo e invitaciones (dueño) ----

@router.post("/api/v1/panel/invitaciones")
def panel_invitar(
    payload: dict = Body(...),
    ctx: SesionCtx = Depends(requiere("administrar")),
    db: Session = Depends(get_db),
):
    exigir_cuenta_activa(ctx)
    rol = str(payload.get("rol") or "").strip().lower()
    if rol not in ROLES:
        raise HTTPException(400, f"Rol inválido. Usa uno de: {', '.join(ROLES)}")
    token = generar_token()
    expira = _ahora() + VIGENCIA_INVITACION
    db.add(Invitacion(empresa_id=ctx.empresa.id, token_hash=hash_token(token), rol=rol,
                      creado_por=ctx.usuario.id, expira_en=expira))
    db.commit()
    return {"url": f"{url_publica()}/unirse/{token}", "rol": rol, "expira_en": a_iso_lima(expira)}


@router.get("/api/v1/panel/equipo")
def panel_equipo(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    filas = (
        db.query(Membresia, Usuario)
        .join(Usuario, Usuario.id == Membresia.usuario_id)
        .filter(Membresia.empresa_id == ctx.empresa.id, Membresia.activo == True)  # noqa: E712
        .order_by(Membresia.id)
        .all()
    )
    return {"miembros": [{
        "usuario_id": u.id, "nombre": u.nombre, "celular": u.celular, "rol": m.rol,
        "rol_etiqueta": ETIQUETA_ROL.get(m.rol, m.rol), "es_yo": u.id == ctx.usuario.id,
    } for m, u in filas]}


@router.delete("/api/v1/panel/equipo/{usuario_id}")
def panel_quitar_acceso(
    usuario_id: int,
    ctx: SesionCtx = Depends(requiere("administrar")),
    db: Session = Depends(get_db),
):
    # Permitido aun con la cuenta vencida: quitar acceso es una medida de seguridad.
    if usuario_id == ctx.usuario.id:
        raise HTTPException(400, "No puedes quitarte el acceso a ti mismo.")
    m = (
        db.query(Membresia)
        .filter(Membresia.usuario_id == usuario_id, Membresia.empresa_id == ctx.empresa.id,
                Membresia.activo == True)  # noqa: E712
        .first()
    )
    if m is None:
        raise HTTPException(404, "Miembro no encontrado")
    m.activo = False
    m.revocado_en = _ahora()
    db.commit()
    sesiones = revocar_sesiones(db, usuario_id, ctx.empresa.id)
    return {"ok": True, "sesiones_cerradas": sesiones}


# ---- API keys de la empresa (dueño) ----

@router.get("/api/v1/panel/api-keys")
def panel_api_keys(ctx: SesionCtx = Depends(requiere("administrar")), db: Session = Depends(get_db)):
    cuentas = db.query(CuentaApi).filter(CuentaApi.empresa_id == ctx.empresa.id).order_by(CuentaApi.id).all()
    return {"api_keys": [{"id": c.id, "nombre": c.nombre, "prefijo": c.prefijo, "activa": bool(c.activa)}
                         for c in cuentas]}


@router.post("/api/v1/panel/api-keys")
def panel_crear_api_key(
    payload: dict = Body(...),
    ctx: SesionCtx = Depends(requiere("administrar")),
    db: Session = Depends(get_db),
):
    exigir_cuenta_activa(ctx)
    nombre = str(payload.get("nombre") or "").strip()[:150]
    if not nombre:
        raise HTTPException(400, "Ponle un nombre (ej. el sistema que la usará)")
    clave = generar_api_key()
    c = CuentaApi(empresa_id=ctx.empresa.id, nombre=nombre, api_key_hash=hash_api_key(clave),
                  prefijo=prefijo_visible(clave), activa=True)
    db.add(c)
    db.commit()
    return {"id": c.id, "nombre": c.nombre, "api_key": clave,
            "aviso": "Guárdala ahora: no se volverá a mostrar."}


@router.delete("/api/v1/panel/api-keys/{cuenta_id}")
def panel_desactivar_api_key(
    cuenta_id: int,
    ctx: SesionCtx = Depends(requiere("administrar")),
    db: Session = Depends(get_db),
):
    c = db.query(CuentaApi).filter(CuentaApi.id == cuenta_id, CuentaApi.empresa_id == ctx.empresa.id).first()
    if c is None:
        raise HTTPException(404, "API key no encontrada")
    c.activa = False
    db.commit()
    return {"ok": True}


# =============================================================
# UNIRSE CON UNA INVITACIÓN
# =============================================================

def _invitacion_vigente(db: Session, token: str) -> Invitacion | None:
    inv = db.query(Invitacion).filter(Invitacion.token_hash == hash_token(token or "")).first()
    if inv is None or inv.usado_en is not None or _aware(inv.expira_en) < _ahora():
        return None
    return inv


@router.post("/unirse/{token}/otp")
def unirse_otp(token: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    _limitar_ip(request, "otp")
    if _invitacion_vigente(db, token) is None:
        raise HTTPException(400, "La invitación no es válida o ya venció. Pide una nueva.")
    if not str(payload.get("nombre") or "").strip():
        raise HTTPException(400, "Escribe tu nombre")
    celular = _celular_o_400(payload.get("celular"))
    try:
        emitir_otp(db, celular, "invitacion")
    except OtpNoDisponible:
        return JSONResponse({"detail": "otp_no_disponible"}, status_code=503)
    except OtpDemasiados:
        raise HTTPException(429, "Ya pediste varios códigos. Espera unos minutos.")
    except RuntimeError:
        raise HTTPException(502, "No pudimos enviar el código por WhatsApp. Inténtalo de nuevo.")
    return {"ok": True, "mensaje": "Te enviamos un código por WhatsApp."}


@router.post("/unirse/{token}/verificar")
def unirse_verificar(token: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    _limitar_ip(request, "verificar")
    nombre = str(payload.get("nombre") or "").strip()[:150]
    celular = _celular_o_400(payload.get("celular"))
    inv = _invitacion_vigente(db, token)
    if inv is None:
        raise HTTPException(400, "La invitación no es válida o ya venció. Pide una nueva.")
    if not verificar_otp(db, celular, str(payload.get("codigo") or ""), "invitacion"):
        raise HTTPException(400, MENSAJE_OTP_INVALIDO)

    usuario = db.query(Usuario).filter(Usuario.celular == celular).first()
    if usuario is None:
        if not nombre:
            raise HTTPException(400, "Escribe tu nombre")
        usuario = Usuario(nombre=nombre, celular=celular, activo=True)
        db.add(usuario)
        db.flush()
    # Un solo uso, atómico: dos personas con el mismo enlace -> solo una entra.
    res = db.execute(
        update(Invitacion)
        .where(Invitacion.id == inv.id, Invitacion.usado_en.is_(None))
        .values(usado_en=_ahora(), usuario_id=usuario.id)
    )
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "La invitación ya fue usada.")
    memb = (
        db.query(Membresia)
        .filter(Membresia.usuario_id == usuario.id, Membresia.empresa_id == inv.empresa_id)
        .first()
    )
    if memb is None:
        db.add(Membresia(usuario_id=usuario.id, empresa_id=inv.empresa_id, rol=inv.rol, activo=True))
    else:  # vuelve a la empresa (p. ej. tras quitarle el acceso) con el rol nuevo
        memb.rol, memb.activo, memb.revocado_en = inv.rol, True, None
    db.commit()
    resp = JSONResponse({"ok": True, "rol": inv.rol, "redirigir": "/panel"})
    poner_cookie(resp, crear_sesion(db, usuario.id, inv.empresa_id))
    return resp


# =============================================================
# PÁGINAS HTML
# =============================================================

def _pagina(request: Request, plantilla: str, ctx: SesionCtx | None, db: Session, **extra):
    datos = {"request": request, "yo": _yo(ctx, db) if ctx else None, **extra}
    return templates.TemplateResponse(f"panel/{plantilla}", datos)


@router.get("/panel/login", response_class=HTMLResponse)
def pagina_login(request: Request, db: Session = Depends(get_db)):
    if sesion_opcional(request, db) is not None:
        return RedirectResponse("/panel", status_code=303)
    return _pagina(request, "login.html", None, db)


@router.get("/panel", response_class=HTMLResponse)
def pagina_inicio(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is None:
        return RedirectResponse("/panel/login", status_code=303)
    return _pagina(request, "inicio.html", ctx, db)


@router.get("/panel/elegir-empresa", response_class=HTMLResponse)
def pagina_elegir(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is None:
        return RedirectResponse("/panel/login", status_code=303)
    return _pagina(request, "elegir_empresa.html", ctx, db, empresas=_empresas_de(db, ctx.usuario.id))


@router.get("/panel/dispositivos", response_class=HTMLResponse)
def pagina_dispositivos(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is None:
        return RedirectResponse("/panel/login", status_code=303)
    if not ctx.puede("administrar"):
        return RedirectResponse("/panel", status_code=303)
    return _pagina(request, "dispositivos.html", ctx, db)


@router.get("/panel/equipo", response_class=HTMLResponse)
def pagina_equipo(request: Request, db: Session = Depends(get_db)):
    ctx = sesion_opcional(request, db)
    if ctx is None:
        return RedirectResponse("/panel/login", status_code=303)
    if not ctx.puede("administrar"):
        return RedirectResponse("/panel", status_code=303)
    return _pagina(request, "equipo.html", ctx, db, roles=[(r, ETIQUETA_ROL[r]) for r in ROLES])


@router.get("/unirse/{token}", response_class=HTMLResponse)
def pagina_unirse(token: str, request: Request, db: Session = Depends(get_db)):
    inv = _invitacion_vigente(db, token)
    empresa = db.get(Empresa, inv.empresa_id) if inv else None
    return _pagina(
        request, "unirse.html", None, db, token=token, valida=inv is not None,
        empresa_nombre=(empresa.nombre_comercial or empresa.razon_social) if empresa else None,
        rol_etiqueta=ETIQUETA_ROL.get(inv.rol, inv.rol) if inv else None,
    )
