"""Router para suscripciones Web Push y administración de receptores."""
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.admin.db import get_db, VAPID_PUBLIC_KEY
from app.admin.models import CodigoInvitacion, Empresa, PushSuscripcion

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _generar_codigo(prefijo: str = "") -> str:
    """Genera código tipo 'AYF-7K3M9P'."""
    alfabeto = string.ascii_uppercase + string.digits
    sufijo = "".join(secrets.choice(alfabeto) for _ in range(6))
    return f"{prefijo}-{sufijo}" if prefijo else sufijo


@router.get("/api/v1/push/vapid-key")
def get_vapid_key():
    """Devuelve la clave pública VAPID para que el cliente JS la use."""
    return {"public_key": VAPID_PUBLIC_KEY}


@router.get("/recibir", response_class=HTMLResponse)
def vista_suscripcion(
    request: Request,
    codigo: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Página de suscripción para receptores."""
    empresa = None
    error = None

    if codigo:
        inv = (
            db.query(CodigoInvitacion)
            .filter(CodigoInvitacion.codigo == codigo.upper())
            .filter(CodigoInvitacion.activo == True)
            .first()
        )
        if not inv:
            error = "Código de invitación inválido o desactivado"
        elif inv.expira_en < datetime.utcnow():
            error = "Este código de invitación expiró"
        elif inv.usos_actuales >= inv.max_usos:
            error = "Este código ya alcanzó el máximo de usos"
        else:
            empresa = inv.empresa

    return templates.TemplateResponse(
        "push/suscribirse.html",
        {
            "request": request,
            "empresa": empresa,
            "codigo": codigo,
            "error": error,
            "vapid_public_key": VAPID_PUBLIC_KEY,
        },
    )


@router.post("/api/v1/push/suscribir")
def api_suscribir(
    payload: dict,
    db: Session = Depends(get_db),
):
    """Endpoint que recibe la suscripción del Service Worker.

    Payload esperado:
    {
      "codigo": "AYF-7K3M9P",
      "nombre_receptor": "Cajero Carlos",
      "subscription": {
        "endpoint": "https://...",
        "keys": {"p256dh": "...", "auth": "..."}
      },
      "user_agent": "..."
    }
    """
    codigo = (payload.get("codigo") or "").upper()
    nombre = (payload.get("nombre_receptor") or "").strip()[:100]
    subscription = payload.get("subscription") or {}
    user_agent = (payload.get("user_agent") or "")[:255]

    if not codigo or not subscription.get("endpoint"):
        raise HTTPException(400, "Faltan datos de suscripción")

    inv = (
        db.query(CodigoInvitacion)
        .filter(CodigoInvitacion.codigo == codigo)
        .filter(CodigoInvitacion.activo == True)
        .first()
    )
    if not inv or inv.expira_en < datetime.utcnow():
        raise HTTPException(403, "Código de invitación inválido")
    if inv.usos_actuales >= inv.max_usos:
        raise HTTPException(403, "Código alcanzó máximo de usos")

    endpoint = subscription["endpoint"]
    keys = subscription.get("keys", {})

    # Si ya existe suscripción con ese endpoint, actualizar (no duplicar)
    existente = (
        db.query(PushSuscripcion)
        .filter(PushSuscripcion.endpoint == endpoint)
        .first()
    )
    if existente:
        existente.empresa_id = inv.empresa_id
        existente.codigo_invitacion_id = inv.id
        existente.nombre_receptor = nombre or existente.nombre_receptor
        existente.p256dh_key = keys.get("p256dh", existente.p256dh_key)
        existente.auth_key = keys.get("auth", existente.auth_key)
        existente.user_agent = user_agent or existente.user_agent
        existente.activo = True
        db.commit()
        return {"ok": True, "id": existente.id, "actualizado": True}

    nueva = PushSuscripcion(
        empresa_id=inv.empresa_id,
        codigo_invitacion_id=inv.id,
        nombre_receptor=nombre or None,
        endpoint=endpoint,
        p256dh_key=keys.get("p256dh", ""),
        auth_key=keys.get("auth", ""),
        user_agent=user_agent,
        rol="general",
    )
    inv.usos_actuales = (inv.usos_actuales or 0) + 1

    db.add(nueva)
    db.commit()
    db.refresh(nueva)

    return {"ok": True, "id": nueva.id, "actualizado": False}


@router.get("/{slug}/receptores", response_class=HTMLResponse)
def panel_receptores(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
):
    """Panel admin del dueño: ver receptores + generar códigos."""
    empresa = (
        db.query(Empresa)
        .filter(or_(Empresa.slug == slug, Empresa.id_fiscal == slug))
        .first()
    )
    if not empresa:
        raise HTTPException(404, "Empresa no encontrada")

    suscripciones = (
        db.query(PushSuscripcion)
        .filter(PushSuscripcion.empresa_id == empresa.id)
        .order_by(PushSuscripcion.creado_en.desc())
        .all()
    )

    codigos = (
        db.query(CodigoInvitacion)
        .filter(CodigoInvitacion.empresa_id == empresa.id)
        .order_by(CodigoInvitacion.creado_en.desc())
        .limit(20)
        .all()
    )

    return templates.TemplateResponse(
        "push/receptores_admin.html",
        {
            "request": request,
            "empresa": empresa,
            "suscripciones": suscripciones,
            "codigos": codigos,
            "now": datetime.utcnow(),
        },
    )


@router.post("/{slug}/receptores/codigo-nuevo")
def crear_codigo(
    slug: str,
    descripcion: str = Form(""),
    dias_validez: int = Form(7),
    max_usos: int = Form(50),
    db: Session = Depends(get_db),
):
    """Genera un código de invitación nuevo."""
    empresa = (
        db.query(Empresa)
        .filter(or_(Empresa.slug == slug, Empresa.id_fiscal == slug))
        .first()
    )
    if not empresa:
        raise HTTPException(404, "Empresa no encontrada")

    prefijo = (empresa.slug or "PGK").upper()[:5]
    # Reintentar si hay colisión (improbable)
    codigo = _generar_codigo(prefijo)
    for _ in range(5):
        existe = (
            db.query(CodigoInvitacion)
            .filter(CodigoInvitacion.codigo == codigo)
            .first()
        )
        if not existe:
            break
        codigo = _generar_codigo(prefijo)

    nuevo = CodigoInvitacion(
        empresa_id=empresa.id,
        codigo=codigo,
        descripcion=(descripcion or "")[:100],
        expira_en=datetime.utcnow() + timedelta(days=max(1, min(dias_validez, 365))),
        max_usos=max(1, min(max_usos, 1000)),
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)

    return RedirectResponse(
        url=f"/{empresa.slug}/receptores?nuevo={nuevo.codigo}",
        status_code=303,
    )


@router.post("/{slug}/receptores/{suscripcion_id}/revocar")
def revocar_receptor(
    slug: str,
    suscripcion_id: int,
    db: Session = Depends(get_db),
):
    """Revoca un receptor."""
    empresa = (
        db.query(Empresa)
        .filter(or_(Empresa.slug == slug, Empresa.id_fiscal == slug))
        .first()
    )
    if not empresa:
        raise HTTPException(404, "Empresa no encontrada")

    sus = (
        db.query(PushSuscripcion)
        .filter(PushSuscripcion.id == suscripcion_id)
        .filter(PushSuscripcion.empresa_id == empresa.id)
        .first()
    )
    if sus:
        sus.activo = False
        db.commit()

    return RedirectResponse(
        url=f"/{empresa.slug}/receptores",
        status_code=303,
    )


@router.post("/api/v1/push/test/{slug}")
def push_test(slug: str, db: Session = Depends(get_db)):
    """Envía un push de prueba a TODOS los receptores activos de la empresa."""
    from app.api.push_service import enviar_push_a_empresa
    from app.admin.db import VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_CLAIM_EMAIL

    empresa = (
        db.query(Empresa)
        .filter(or_(Empresa.slug == slug, Empresa.id_fiscal == slug))
        .first()
    )
    if not empresa:
        raise HTTPException(404, "Empresa no encontrada")

    resultado = enviar_push_a_empresa(
        db=db,
        empresa_id=empresa.id,
        titulo="🔔 Push de prueba",
        cuerpo=f"Funcionando para {empresa.nombre_comercial or empresa.razon_social}",
        datos_extra={"prueba": True},
        vapid_public_key=VAPID_PUBLIC_KEY,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_email=VAPID_CLAIM_EMAIL,
    )
    return JSONResponse(resultado)
