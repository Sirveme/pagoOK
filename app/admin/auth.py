"""Autenticación admin: DNI + password para dueños, DNI + PIN para vendedores."""
from passlib.context import CryptContext
from datetime import datetime, timedelta
from secrets import token_urlsafe
from sqlalchemy.orm import Session
from app.admin.models import Persona, SesionAdmin

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESION_DURACION_HORAS = 8


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def autenticar_duenio(db: Session, id_fiscal: str, password: str) -> Persona | None:
    persona = db.query(Persona).filter(
        Persona.id_fiscal == id_fiscal.strip(),
        Persona.es_duenio == True,
        Persona.activa == True,
        Persona.bloqueada == False,
    ).first()
    if not persona:
        return None
    if not verify_password(password, persona.password_hash):
        persona.intentos_fallidos = (persona.intentos_fallidos or 0) + 1
        if persona.intentos_fallidos >= 5:
            persona.bloqueada = True
        db.commit()
        return None
    persona.intentos_fallidos = 0
    persona.ultimo_login = datetime.utcnow()
    db.commit()
    return persona


def crear_sesion(db: Session, persona_id: int, empresa_id: int | None,
                 ip: str | None, user_agent: str | None) -> str:
    token = token_urlsafe(48)
    sesion = SesionAdmin(
        token=token,
        persona_id=persona_id,
        empresa_id=empresa_id,
        expira_en=datetime.utcnow() + timedelta(hours=SESION_DURACION_HORAS),
        ip=ip,
        user_agent=user_agent,
    )
    db.add(sesion)
    db.commit()
    return token


def cerrar_sesion(db: Session, token: str) -> None:
    db.query(SesionAdmin).filter(SesionAdmin.token == token).delete()
    db.commit()


def obtener_sesion(db: Session, token: str | None) -> SesionAdmin | None:
    if not token:
        return None
    sesion = db.query(SesionAdmin).filter(SesionAdmin.token == token).first()
    if not sesion:
        return None
    if sesion.expira_en < datetime.utcnow():
        db.delete(sesion)
        db.commit()
        return None
    return sesion
