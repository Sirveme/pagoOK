"""Resolución de empresa: UNA dependencia por tipo de credencial.

Regla de aislamiento: el empresa_id NUNCA viene del cliente. Siempre se
deriva de la credencial:
  - empresa_desde_dispositivo   -> X-Device-Token (inbound, heartbeat, pagos/buscar…)
  - empresa_desde_api_key       -> X-API-Key (API pública v1; = cuenta_actual)
  - usuario_y_empresa_desde_sesion -> cookie de sesión del panel (con su rol)

Si un endpoint recibe un slug o un empresa_id, solo se usa para VALIDAR que
coincide con la credencial (403 si no).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.models_multiempresa import Membresia, Sesion, Usuario
from app.api.models_webhook import Dispositivo
from app.api.publica_v1 import cuenta_actual
from app.services import cuenta_empresa
from app.services.secretos import generar_token, hash_token

COOKIE_SESION = "pagook_sesion"
DURACION_SESION = timedelta(days=30)

# ---- Matriz de permisos por rol (se aplica en el backend) ----
PERMISOS: dict[str, set[str]] = {
    "ver_pagos": {"dueno", "cajero", "contador"},         # pagos del día con monto y pagador
    "buscar_pago": {"dueno", "cajero", "almacen", "contador"},  # ¿existe? (almacén: sin listado)
    "marcar_usado": {"dueno", "cajero"},
    "reportes": {"dueno", "contador"},                    # ingresos/egresos + CSV
    "administrar": {"dueno"},                             # dispositivos, códigos, equipo, API keys
}


def puede(rol: str, permiso: str) -> bool:
    return rol in PERMISOS.get(permiso, set())


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# =============================================================
# DISPOSITIVO (token X-Device-Token)
# =============================================================

def resolver_dispositivo(db: Session, token: str | None) -> Dispositivo:
    """Mismos mensajes que antes del sprint (compatibilidad con la app 1.5.x)."""
    if not token:
        raise HTTPException(401, "Falta header X-Device-Token")
    disp = (
        db.query(Dispositivo)
        .filter(Dispositivo.token == token, Dispositivo.activo == True)  # noqa: E712
        .first()
    )
    if not disp:
        raise HTTPException(401, "Token inválido o desactivado")
    return disp


def empresa_desde_dispositivo(
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
) -> Dispositivo:
    """Dependencia FastAPI: el dispositivo autenticado (su empresa_id manda)."""
    return resolver_dispositivo(db, x_device_token)


# =============================================================
# API KEY (X-API-Key) — ya existía como cuenta_actual
# =============================================================

empresa_desde_api_key = cuenta_actual


# =============================================================
# SESIÓN DEL PANEL (cookie)
# =============================================================

@dataclass
class SesionCtx:
    usuario: Usuario
    empresa: Empresa
    rol: str
    sesion: Sesion
    estado_cuenta: str = field(default="activa")

    def puede(self, permiso: str) -> bool:
        return puede(self.rol, permiso)

    @property
    def bloqueada(self) -> bool:
        return self.estado_cuenta in cuenta_empresa.ESTADOS_BLOQUEADOS


def cookie_segura() -> bool:
    return os.getenv("PAGOOK_ENTORNO", "produccion").strip().lower() != "desarrollo"


def crear_sesion(db: Session, usuario_id: int, empresa_id: int) -> str:
    token = generar_token()
    db.add(Sesion(
        usuario_id=usuario_id, empresa_id=empresa_id, token_hash=hash_token(token),
        creado_en=_ahora(), expira_en=_ahora() + DURACION_SESION,
    ))
    db.commit()
    return token


def poner_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_SESION, token, max_age=int(DURACION_SESION.total_seconds()),
        httponly=True, secure=cookie_segura(), samesite="lax", path="/",
    )


def borrar_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_SESION, path="/")


def revocar_sesiones(db: Session, usuario_id: int, empresa_id: int) -> int:
    n = (
        db.query(Sesion)
        .filter(Sesion.usuario_id == usuario_id, Sesion.empresa_id == empresa_id,
                Sesion.revocada_en.is_(None))
        .update({Sesion.revocada_en: _ahora()}, synchronize_session=False)
    )
    db.commit()
    return n


def sesion_opcional(request: Request, db: Session) -> SesionCtx | None:
    """Valida la cookie EN CADA REQUEST (la revocación es inmediata)."""
    token = request.cookies.get(COOKIE_SESION)
    if not token:
        return None
    ses = (
        db.query(Sesion)
        .filter(Sesion.token_hash == hash_token(token), Sesion.revocada_en.is_(None))
        .first()
    )
    if ses is None or _aware(ses.expira_en) < _ahora():
        return None
    usuario = db.get(Usuario, ses.usuario_id)
    if usuario is None or not usuario.activo:
        return None
    memb = (
        db.query(Membresia)
        .filter(Membresia.usuario_id == ses.usuario_id, Membresia.empresa_id == ses.empresa_id,
                Membresia.activo == True)  # noqa: E712
        .first()
    )
    if memb is None:
        return None
    empresa = db.get(Empresa, ses.empresa_id)
    if empresa is None:
        return None
    estado = cuenta_empresa.refrescar_estado(db, empresa)
    return SesionCtx(usuario=usuario, empresa=empresa, rol=memb.rol, sesion=ses, estado_cuenta=estado)


def usuario_y_empresa_desde_sesion(request: Request, db: Session = Depends(get_db)) -> SesionCtx:
    ctx = sesion_opcional(request, db)
    if ctx is None:
        raise HTTPException(401, "sesion_requerida")
    return ctx


def requiere(permiso: str):
    """Dependencia: sesión válida + rol con ese permiso (403 si no)."""
    def _dep(ctx: SesionCtx = Depends(usuario_y_empresa_desde_sesion)) -> SesionCtx:
        if not ctx.puede(permiso):
            raise HTTPException(403, "sin_permiso")
        return ctx
    return _dep


def exigir_cuenta_activa(ctx: SesionCtx) -> None:
    """Con la cuenta vencida o suspendida el panel es de solo lectura."""
    if ctx.bloqueada:
        raise HTTPException(402, "cuenta_vencida")


# =============================================================
# VISTAS POR SLUG (ingresos, egresos, flujo de caja, receptores...)
# =============================================================

def legacy_publico_perusistemas() -> bool:
    """Transición (zWeb-04): mientras la app 1.5.x abra /perusistemas/... sin
    sesión, esa empresa sigue pública. Se apaga en zStore-05."""
    return os.getenv("LEGACY_PUBLICO_PERUSISTEMAS", "true").strip().lower() not in ("0", "false", "no")


def acceso_por_sesion_o_legacy(slug: str, request: Request, db: Session, permiso: str) -> Empresa:
    """Empresa de una vista por slug, autorizada por la SESIÓN del panel.

    - Con sesión: el slug solo se usa para validar que coincide con la empresa
      de la sesión (403 si no) y el rol debe tener `permiso` (403 si no).
    - Sin sesión: solo perusistemas, y solo con LEGACY_PUBLICO_PERUSISTEMAS=true.
    - Si no: 401 sesion_requerida (las páginas HTML redirigen al login).
    """
    from app.api.router_demo import _obtener_empresa_por_slug  # import local: evita ciclo

    empresa = _obtener_empresa_por_slug(db, slug)
    if empresa is None:
        raise HTTPException(404, "Empresa no encontrada")
    ctx = sesion_opcional(request, db)
    if ctx is not None:
        if ctx.empresa.id != empresa.id or not ctx.puede(permiso):
            raise HTTPException(403, "sin_permiso")
        return empresa
    if empresa.slug == cuenta_empresa.SLUG_PRINCIPAL and legacy_publico_perusistemas():
        return empresa
    raise HTTPException(401, "sesion_requerida")


def acceso_html_o_redirigir(slug: str, request: Request, db: Session, permiso: str):
    """Igual que acceso_por_sesion_o_legacy, pero para páginas HTML: sin sesión
    devuelve un RedirectResponse al login en lugar de 401."""
    from fastapi.responses import RedirectResponse

    try:
        return acceso_por_sesion_o_legacy(slug, request, db, permiso)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/panel/login", status_code=303)
        raise
