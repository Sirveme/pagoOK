"""Dependencias FastAPI para inyectar usuario/empresa actual."""
from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload
from app.admin.db import get_db
from app.admin.auth import obtener_sesion
from app.admin.models import Persona, Empresa

SESION_COOKIE = "pagook_admin_sesion"


class NoAutenticado(Exception):
    pass


def current_persona(
    request: Request,
    pagook_admin_sesion: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Persona:
    sesion = obtener_sesion(db, pagook_admin_sesion)
    if not sesion:
        raise NoAutenticado()
    persona = db.query(Persona).filter(Persona.id == sesion.persona_id).first()
    if not persona or not persona.activa:
        raise NoAutenticado()
    request.state.persona = persona
    request.state.sesion = sesion
    return persona


def current_empresa(
    request: Request,
    persona: Persona = Depends(current_persona),
    db: Session = Depends(get_db),
) -> Empresa | None:
    """Devuelve la empresa de la sesión actual.
    Si la persona es dueño pero no tiene empresa, devuelve None
    (el frontend lo lleva al onboarding)."""
    sesion = request.state.sesion
    if not sesion.empresa_id:
        # Buscar la primera empresa donde sea dueño
        empresa = db.query(Empresa).filter(
            Empresa.duenio_id == persona.id,
            Empresa.activa == True,
        ).first()
        if empresa:
            sesion.empresa_id = empresa.id
            db.commit()
        return empresa
    return db.query(Empresa).options(
        joinedload(Empresa.pais),
        joinedload(Empresa.regimen),
    ).filter(Empresa.id == sesion.empresa_id).first()
