"""Rutas admin para gestionar dispositivos (celulares) y ver notificaciones."""
from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from secrets import token_urlsafe
from datetime import datetime

from app.admin.db import get_db
from app.admin.models import Persona, Empresa
from app.admin.deps import current_persona, current_empresa
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado

router = APIRouter(prefix="/admin", tags=["admin-dispositivos"])
templates = Jinja2Templates(directory="templates")


def _ctx(request, persona, empresa, **extra):
    return {"request": request, "persona": persona, "empresa": empresa, **extra}


@router.get("/dispositivos", response_class=HTMLResponse)
async def dispositivos_view(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    dispositivos = db.query(Dispositivo).filter(
        Dispositivo.empresa_id == empresa.id
    ).order_by(desc(Dispositivo.creado_en)).all()

    notifs = db.query(NotificacionRaw).filter(
        NotificacionRaw.empresa_id == empresa.id
    ).order_by(desc(NotificacionRaw.timestamp_servidor)).limit(20).all()

    pagos = db.query(PagoDetectado).filter(
        PagoDetectado.empresa_id == empresa.id
    ).order_by(desc(PagoDetectado.recibido_en)).limit(20).all()

    return templates.TemplateResponse(
        "admin/dispositivos.html",
        _ctx(request, persona, empresa, dispositivos=dispositivos, notifs=notifs, pagos=pagos),
    )


@router.post("/dispositivo", response_class=HTMLResponse)
async def dispositivo_crear(
    request: Request,
    nombre: str = Form(...),
    modelo: str = Form(""),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    token = "tok_" + token_urlsafe(32)
    disp = Dispositivo(
        empresa_id=empresa.id,
        token=token,
        nombre=nombre.strip()[:100],
        modelo=modelo.strip()[:100] or None,
    )
    db.add(disp)
    db.commit()
    db.refresh(disp)
    return templates.TemplateResponse(
        "admin/partials/dispositivo_fila.html",
        {"request": request, "d": disp, "mostrar_token_completo": True},
    )


@router.post("/dispositivo/{disp_id}/regenerar", response_class=HTMLResponse)
async def dispositivo_regenerar(
    disp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    disp = db.query(Dispositivo).filter(
        Dispositivo.id == disp_id, Dispositivo.empresa_id == empresa.id
    ).first()
    if not disp:
        return Response(status_code=404)
    disp.token = "tok_" + token_urlsafe(32)
    db.commit()
    return templates.TemplateResponse(
        "admin/partials/dispositivo_fila.html",
        {"request": request, "d": disp, "mostrar_token_completo": True},
    )


@router.post("/dispositivo/{disp_id}/toggle", response_class=HTMLResponse)
async def dispositivo_toggle(
    disp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    disp = db.query(Dispositivo).filter(
        Dispositivo.id == disp_id, Dispositivo.empresa_id == empresa.id
    ).first()
    if not disp:
        return Response(status_code=404)
    disp.activo = not disp.activo
    db.commit()
    return templates.TemplateResponse(
        "admin/partials/dispositivo_fila.html",
        {"request": request, "d": disp, "mostrar_token_completo": False},
    )


@router.delete("/dispositivo/{disp_id}")
async def dispositivo_eliminar(
    disp_id: int,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    disp = db.query(Dispositivo).filter(
        Dispositivo.id == disp_id, Dispositivo.empresa_id == empresa.id
    ).first()
    if disp:
        db.delete(disp)
        db.commit()
    return Response(status_code=200)
