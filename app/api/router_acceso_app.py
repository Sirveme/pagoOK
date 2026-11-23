"""Entrar con el número de WhatsApp y autorregistro de independientes (zWeb-14).

  GET  /api/v1/app/registro/config?pais=XX   prefijo, validación y rubros
  POST /api/v1/app/acceso/solicitar          {pais, celular}         -> OTP por WhatsApp
  POST /api/v1/app/acceso/verificar          {pais, celular, codigo, datos del equipo}
  POST /api/v1/app/acceso/vincular           {ticket, empresa_id}    (dueño de varias)
  POST /api/v1/app/registro/crear            {ticket, pais, nombre, rubro, nombre_comercial?}

Supuesto del mercado: el celular donde se instala PagoOK es del DUEÑO. Solo un
dueño vincula su celular por esta vía; cajeros y contadores siguen con el
código del panel. Todo se procesa en pagook.pro (Te Atiendo no interviene).

Sin cambios: /api/v1/dispositivos/vincular (código del panel, celulares
dedicados como MITA o las clínicas) y CODIGO_REVISION_PLAY. Ambas vías crean el
dispositivo con la misma función (router_vinculacion.vincular_equipo).

Contrato completo para Android: docs/acceso-app-2026-11.md.
Errores: {"detail": {"codigo": "...", "mensaje": "..."}} con el HTTP indicado.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin.db import get_db
from app.admin.models import Empresa
from app.api.models_multiempresa import Membresia, TicketAcceso, Usuario
from app.api.router_device import _ip_cliente
from app.api.router_vinculacion import datos_empresa_publicos, vincular_equipo
from app.services import cuenta_empresa, registro_paises, revision
from app.services.otp import (
    MAX_OTP_POR_CELULAR, VIGENCIA_OTP, OtpDemasiados, OtpNoDisponible, emitir_otp,
    obtener_enviador, verificar_otp,
)
from app.services.secretos import enmascarar_celular, generar_token, hash_token

logger = logging.getLogger("pagook")
router = APIRouter(tags=["acceso-app"])

PROPOSITO_OTP = "app"
VIGENCIA_TICKET = {"registro": timedelta(minutes=15), "elegir": timedelta(minutes=10)}
ROLES_QUE_VINCULAN = ("dueno",)
ETIQUETA_ROL = {"dueno": "dueño", "cajero": "cajero", "almacen": "almacén", "contador": "contador"}

MENSAJE_REGISTRO_CERRADO = "Por ahora el registro es por invitación. Escríbenos por WhatsApp."
MENSAJE_CODIGO_INVALIDO = "Código incorrecto o vencido. Pide uno nuevo."
MENSAJE_TICKET_INVALIDO = "Tu sesión venció. Vuelve a ingresar tu número para recibir otro código."
MENSAJE_FALLO_ENVIO = ("No pudimos enviarte el código por WhatsApp. Revisa que el número tenga WhatsApp "
                       "e inténtalo de nuevo en unos minutos.")

# Campos del equipo que llegan en verificar y se guardan en el ticket para
# vincular en el paso siguiente (los mismos que usa /dispositivos/vincular).
CAMPOS_EQUIPO = ("instalacion_id", "manufacturer", "modelo", "android_version", "app_version",
                 "version_code", "consentimiento_version", "consentimiento_fecha",
                 "bancos_declarados", "nivel_equipo", "egresos_activo")

# ---- Límites por IP (en memoria, como el ingreso del panel) ----
LIMITES_IP = {"solicitar": (10, 600), "verificar": (20, 600), "ticket": (20, 600)}
_lock_ip = threading.Lock()
_intentos_ip: dict[str, list[float]] = {}


def reiniciar_limites_acceso_app() -> None:
    """Limpia los límites en memoria (tests)."""
    with _lock_ip:
        _intentos_ip.clear()


def _limitar_ip(request: Request, accion: str) -> None:
    maximo, ventana = LIMITES_IP[accion]
    clave, ahora = f"{accion}:{_ip_cliente(request)}", time.time()
    with _lock_ip:
        lista = [t for t in _intentos_ip.get(clave, []) if t > ahora - ventana]
        lista.append(ahora)
        _intentos_ip[clave] = lista
    if len(lista) > maximo:
        _error(429, "demasiados_intentos", "Demasiados intentos. Espera unos minutos.")


# =============================================================
# AYUDAS
# =============================================================

def _error(status: int, codigo: str, mensaje: str):
    raise HTTPException(status, {"codigo": codigo, "mensaje": mensaje})


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def autoregistro_activo() -> bool:
    return os.getenv("AUTOREGISTRO_ACTIVO", "true").strip().lower() not in ("0", "false", "no", "off")


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        _error(400, "json_invalido", "JSON inválido")
    if not isinstance(data, dict):
        _error(400, "json_invalido", "El body debe ser un objeto JSON")
    return data


def _config_pais(valor) -> registro_paises.ConfigPais:
    cfg = registro_paises.pais(str(valor or ""))
    if cfg is None:
        _error(400, "pais_no_disponible", "PagoOK todavía no está disponible en ese país.")
    return cfg


def _celular(cfg: registro_paises.ConfigPais, valor) -> str:
    cel = registro_paises.normalizar(cfg, str(valor or ""))
    if cel is None:
        _error(400, "celular_invalido", registro_paises.mensaje_formato(cfg))
    return cel


def _celular_revision(cfg: registro_paises.ConfigPais, valor) -> str | None:
    """El celular ficticio del revisor de Google (REVISION_PANEL_CELULAR) puede
    no cumplir el formato del país: se reconoce ANTES de validar."""
    candidato = registro_paises.candidato_e164(cfg, str(valor or ""))
    return candidato if revision.es_celular_revision(candidato) else None


def _membresias_activas(db: Session, usuario_id: int) -> list[Membresia]:
    return (
        db.query(Membresia)
        .filter(Membresia.usuario_id == usuario_id, Membresia.activo == True)  # noqa: E712
        .order_by(Membresia.id)
        .all()
    )


def _empresas_que_vincula(db: Session, usuario: Usuario) -> list[Empresa]:
    """Empresas donde ese usuario es dueño (las únicas que puede vincular aquí)."""
    salida = []
    for m in _membresias_activas(db, usuario.id):
        if m.rol in ROLES_QUE_VINCULAN:
            e = db.get(Empresa, m.empresa_id)
            if e is not None:
                salida.append(e)
    return salida


def _nombre_empresa(e: Empresa) -> str:
    return e.nombre_comercial or e.razon_social


def _datos_equipo(data: dict) -> dict:
    datos = {c: data[c] for c in CAMPOS_EQUIPO if data.get(c) not in (None, "")}
    if not datos.get("consentimiento_fecha"):
        datos["consentimiento_fecha"] = _ahora().isoformat()
    return datos


def _respuesta_vinculado(db: Session, empresa: Empresa, disp, estado: str = "vinculado") -> dict:
    return {
        "estado": estado,
        "token": disp.token,
        "dispositivo_id": disp.id,
        "empresa": {
            **datos_empresa_publicos(empresa),
            "id": empresa.id,
            "nombre": _nombre_empresa(empresa),
            "slug": empresa.slug,
            "tipo": empresa.tipo_empresa or "negocio",
        },
        "cuenta": cuenta_empresa.info_cuenta(db, empresa),
    }


def _vincular_y_responder(db: Session, empresa: Empresa, datos: dict, pais: str, motivo: str) -> dict:
    disp = vincular_equipo(db, empresa, {**datos, "pais": pais})
    db.commit()
    logger.info(f"Dispositivo vinculado id={disp.id} empresa={empresa.id} por {motivo}")
    return _respuesta_vinculado(db, empresa, disp)


def _crear_ticket(db: Session, tipo: str, celular: str, pais: str, datos: dict) -> tuple[str, int]:
    token = "tk_" + generar_token()
    vigencia = VIGENCIA_TICKET[tipo]
    db.add(TicketAcceso(tipo=tipo, celular=celular, pais=pais, token_hash=hash_token(token),
                        datos_equipo=datos, creado_en=_ahora(), expira_en=_ahora() + vigencia))
    db.commit()
    return token, int(vigencia.total_seconds())


def _ticket_vigente(db: Session, token, tipo: str, celular_enviado=None) -> TicketAcceso:
    """Ticket del tipo pedido, sin usar y sin vencer. Si la app manda el celular,
    debe ser el mismo al que se entregó el ticket. Mismo 400 en todos los casos."""
    t = None
    if isinstance(token, str) and token.startswith("tk_"):
        t = db.query(TicketAcceso).filter(TicketAcceso.token_hash == hash_token(token)).first()
    if t is None or t.tipo != tipo or t.usado_en is not None or _aware(t.expira_en) < _ahora():
        _error(400, "ticket_invalido", MENSAJE_TICKET_INVALIDO)
    if celular_enviado not in (None, ""):
        cfg = registro_paises.pais(t.pais)
        if cfg is None or registro_paises.normalizar(cfg, str(celular_enviado)) != t.celular:
            _error(400, "ticket_invalido", MENSAJE_TICKET_INVALIDO)
    return t


def _consumir_ticket(db: Session, t: TicketAcceso) -> None:
    """Un solo uso, también si llegan dos pedidos a la vez con el mismo ticket."""
    res = db.execute(
        update(TicketAcceso)
        .where(TicketAcceso.id == t.id, TicketAcceso.usado_en.is_(None))
        .values(usado_en=_ahora())
    )
    if res.rowcount != 1:
        db.rollback()
        _error(400, "ticket_invalido", MENSAJE_TICKET_INVALIDO)


# =============================================================
# CONFIGURACIÓN POR PAÍS
# =============================================================

@router.get("/api/v1/app/registro/config")
def registro_config(pais: str | None = None):
    """Con ?pais=XX: la configuración de ese país. Sin pais: la lista de países."""
    comun = {
        "rubros": registro_paises.rubros_publicos(),
        "autoregistro_activo": autoregistro_activo(),
        "prueba_dias": cuenta_empresa.dias_prueba_independiente(),
        "otp": {"canal": "whatsapp", "digitos": 6, "vence_en_seg": int(VIGENCIA_OTP.total_seconds())},
    }
    if pais is None or not pais.strip():
        return {"paises": [c.publico() for c in registro_paises.PAISES.values()], **comun}
    cfg = registro_paises.pais(pais)
    if cfg is None:
        _error(404, "pais_no_disponible", "PagoOK todavía no está disponible en ese país.")
    return {**cfg.publico(), **comun}


# =============================================================
# 1) SOLICITAR EL CÓDIGO
# =============================================================

@router.post("/api/v1/app/acceso/solicitar")
async def acceso_solicitar(request: Request, db: Session = Depends(get_db)):
    _limitar_ip(request, "solicitar")
    data = await _json(request)
    cfg = _config_pais(data.get("pais"))

    # Revisor de Google Play: código fijo, sin WhatsApp (mismo acceso que el panel).
    if _celular_revision(cfg, data.get("celular")):
        return {"ok": True, "modo": "revision", "mensaje": "Ingresa el código de revisión.",
                "vence_en_seg": None}

    celular = _celular(cfg, data.get("celular"))
    try:
        enviador = obtener_enviador()
    except OtpNoDisponible:
        _error(503, "otp_no_disponible", "El envío de códigos no está disponible en este momento.")

    # Se envía a cualquier número válido: un número nuevo también necesita el
    # código para registrarse. Por eso la respuesta no revela si ya tiene cuenta.
    try:
        emitir_otp(db, celular, PROPOSITO_OTP, enviador)
    except OtpDemasiados:
        _error(429, "demasiados_codigos",
               f"Ya pedimos {MAX_OTP_POR_CELULAR} códigos para este número. Espera 10 minutos.")
    except Exception as exc:
        logger.error(f"Acceso app: no se pudo enviar el OTP ({type(exc).__name__})")
        _error(502, "envio_fallido", MENSAJE_FALLO_ENVIO)
    return {
        "ok": True,
        "modo": "otp",
        "mensaje": f"Te enviamos un código por WhatsApp al {enmascarar_celular(celular)}.",
        "vence_en_seg": int(VIGENCIA_OTP.total_seconds()),
    }


# =============================================================
# 2) VERIFICAR EL CÓDIGO
# =============================================================

@router.post("/api/v1/app/acceso/verificar")
async def acceso_verificar(request: Request, db: Session = Depends(get_db)):
    _limitar_ip(request, "verificar")
    data = await _json(request)
    cfg = _config_pais(data.get("pais"))
    codigo = str(data.get("codigo") or "").strip()

    # Sin consentimiento no se vincula; se revisa ANTES de gastar el código.
    if not data.get("consentimiento_version"):
        _error(400, "consentimiento_requerido", "Falta aceptar la Política de Privacidad (consentimiento_version).")
    datos = _datos_equipo(data)

    # Revisor de Google Play: vincula a la empresa de revisión (como CODIGO_REVISION_PLAY).
    cel_revision = _celular_revision(cfg, data.get("celular"))
    if cel_revision:
        clave_fallos = f"app:{_ip_cliente(request)}"
        if revision.panel_bloqueado(clave_fallos):
            _error(429, "demasiados_intentos", "Demasiados intentos. Espera unos minutos.")
        empresa = revision.empresa_revision(db)
        if not revision.codigo_revision_valido(codigo) or empresa is None:
            revision.registrar_fallo_panel(clave_fallos)
            _error(400, "codigo_invalido", MENSAJE_CODIGO_INVALIDO)
        revision.limpiar_fallos_panel(clave_fallos)
        return _vincular_y_responder(db, empresa, datos, cfg.codigo, "acceso de revisión")

    celular = _celular(cfg, data.get("celular"))
    if not verificar_otp(db, celular, codigo, PROPOSITO_OTP):
        _error(400, "codigo_invalido", MENSAJE_CODIGO_INVALIDO)

    usuario = db.query(Usuario).filter(Usuario.celular == celular).first()
    if usuario is not None and not usuario.activo:
        return {"estado": "cuenta_desactivada",
                "mensaje": "Tu cuenta está desactivada. Escríbenos por WhatsApp."}
    membresias = _membresias_activas(db, usuario.id) if usuario else []

    if not membresias:
        # Número nuevo (o sin ninguna empresa activa): registro.
        if not autoregistro_activo():
            return {"estado": "registro_cerrado", "mensaje": MENSAJE_REGISTRO_CERRADO}
        ticket, vence = _crear_ticket(db, "registro", celular, cfg.codigo, datos)
        logger.info(f"Acceso app: número nuevo {enmascarar_celular(celular)} -> ticket de registro")
        return {"estado": "nuevo", "ticket": ticket, "vence_en_seg": vence, "pais": cfg.codigo,
                "prueba_dias": cuenta_empresa.dias_prueba_independiente()}

    empresas = _empresas_que_vincula(db, usuario)
    if not empresas:
        # Cajero, contador o almacén: no vincula celulares por esta vía.
        m = membresias[0]
        e = db.get(Empresa, m.empresa_id)
        rol = ETIQUETA_ROL.get(m.rol, m.rol)
        return {
            "estado": "sin_permiso",
            "mensaje": (f"Tu número está registrado como {rol} de {_nombre_empresa(e) if e else 'una empresa'}. "
                        "Solo el dueño vincula celulares con su número. Pídele un código de "
                        "vinculación desde el panel (Celulares) e ingrésalo en «Tengo un código»."),
        }
    if len(empresas) == 1:
        return _vincular_y_responder(db, empresas[0], datos, cfg.codigo, "número verificado")

    ticket, vence = _crear_ticket(db, "elegir", celular, cfg.codigo, datos)
    return {
        "estado": "elegir_empresa",
        "ticket": ticket,
        "vence_en_seg": vence,
        "empresas": [{"empresa_id": e.id, "nombre": _nombre_empresa(e), "pais": e.pais_codigo}
                     for e in empresas],
    }


# =============================================================
# 3) DUEÑO DE VARIAS EMPRESAS: ELEGIR UNA
# =============================================================

@router.post("/api/v1/app/acceso/vincular")
async def acceso_vincular(request: Request, db: Session = Depends(get_db)):
    _limitar_ip(request, "ticket")
    data = await _json(request)
    t = _ticket_vigente(db, data.get("ticket"), "elegir", data.get("celular"))
    try:
        empresa_id = int(data.get("empresa_id"))
    except (TypeError, ValueError):
        _error(400, "empresa_invalida", "Elige una de tus empresas.")
    usuario = db.query(Usuario).filter(Usuario.celular == t.celular, Usuario.activo == True).first()  # noqa: E712
    empresa = next((e for e in (_empresas_que_vincula(db, usuario) if usuario else []) if e.id == empresa_id), None)
    if empresa is None:
        _error(403, "sin_permiso", "No eres dueño de esa empresa.")
    _consumir_ticket(db, t)
    return _vincular_y_responder(db, empresa, t.datos_equipo or {}, t.pais, "número verificado (elegida)")


# =============================================================
# 4) NÚMERO NUEVO: CREAR LA CUENTA DEL INDEPENDIENTE
# =============================================================

def _texto(valor, minimo: int, maximo: int, campo: str, obligatorio: bool = True) -> str | None:
    texto = " ".join(str(valor or "").split())
    if not texto and not obligatorio:
        return None
    if not (minimo <= len(texto) <= maximo) or not any(c.isalpha() for c in texto):
        _error(400, f"{campo}_invalido",
               f"El {campo.replace('_', ' ')} debe tener entre {minimo} y {maximo} caracteres.")
    return texto


def _slug_independiente(db: Session, nombre: str) -> str:
    from app.admin.router import slugify  # import local: evita ciclo de imports

    base = (slugify(nombre) or "independiente")[:20]
    for _ in range(20):
        slug = f"{base}-{secrets.token_hex(3)}"
        if not db.query(Empresa).filter(Empresa.slug == slug).first():
            return slug
    raise RuntimeError("No se pudo generar un slug libre")


@router.post("/api/v1/app/registro/crear")
async def registro_crear(request: Request, db: Session = Depends(get_db)):
    _limitar_ip(request, "ticket")
    data = await _json(request)
    t = _ticket_vigente(db, data.get("ticket"), "registro", data.get("celular"))
    if not autoregistro_activo():
        _error(403, "registro_cerrado", MENSAJE_REGISTRO_CERRADO)
    cfg = _config_pais(data.get("pais"))
    if cfg.codigo != t.pais:
        _error(400, "pais_no_coincide", "El país no coincide con el del número verificado.")
    nombre = _texto(data.get("nombre"), 2, 60, "nombre")
    nombre_comercial = _texto(data.get("nombre_comercial"), 2, 80, "nombre_comercial", obligatorio=False)
    rubro = str(data.get("rubro") or "").strip().lower()
    if rubro not in registro_paises.CLAVES_RUBRO:
        _error(400, "rubro_invalido", "Elige un rubro de la lista.")

    # El celular es único: si ya tiene una empresa activa, no se registra otra
    # (no debería pasar: verificar lo trata como existente). El ticket no se gasta.
    usuario = db.query(Usuario).filter(Usuario.celular == t.celular).first()
    if usuario is not None and (not usuario.activo or _membresias_activas(db, usuario.id)):
        _error(409, "ya_registrado", "Este número ya tiene una cuenta. Vuelve a ingresar con tu número.")

    _consumir_ticket(db, t)
    try:
        visible = nombre_comercial or nombre
        empresa = Empresa(
            pais_codigo=cfg.codigo, id_fiscal=None, id_fiscal_tipo=None, tipo_empresa="independiente",
            rubro=rubro, razon_social=nombre, nombre_comercial=visible,
            slug=_slug_independiente(db, visible), activa=True,
            visible_en_testers=False,      # no sale en la grilla pública /testers
            acepta_autoregistro=False,     # otros celulares entran con código del panel
        )
        cuenta_empresa.iniciar_prueba(empresa, dias=cuenta_empresa.dias_prueba_independiente())
        db.add(empresa)
        db.flush()
        if usuario is None:
            usuario = Usuario(nombre=nombre, celular=t.celular, activo=True)
            db.add(usuario)
        else:
            usuario.nombre = nombre  # usuario sin empresas activas (p. ej. le quitaron el acceso)
        db.flush()
        db.add(Membresia(usuario_id=usuario.id, empresa_id=empresa.id, rol="dueno", activo=True))
        disp = vincular_equipo(db, empresa, {**(t.datos_equipo or {}), "pais": cfg.codigo})
        db.commit()
    except IntegrityError:
        db.rollback()
        _error(409, "ya_registrado", "Este número ya tiene una cuenta. Vuelve a ingresar con tu número.")
    logger.info(f"Autorregistro: empresa independiente id={empresa.id} pais={cfg.codigo} rubro={rubro} "
                f"dispositivo={disp.id} celular={enmascarar_celular(t.celular)}")
    return _respuesta_vinculado(db, empresa, disp, estado="creado")
