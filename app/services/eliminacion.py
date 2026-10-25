"""Descarga y eliminación REAL de los datos de una empresa (Google Play).

Una sola función de borrado para los dos caminos:
  - autoservicio: el dueño desde /panel/eliminar
  - solicitud:    el administrador con scripts/gestion_empresas.py eliminar

Reglas:
  - IRREVERSIBLE. Nunca sobre perusistemas ni sobre las empresas de SLUGS_DEMO
    (EliminacionProhibida -> 403).
  - Una sola transacción: quien llama hace commit (o rollback si algo falla).
  - Borrado explícito tabla por tabla, en el orden que exigen las llaves
    foráneas de producción (sesion_admin -> empresa y notificacion_raw ->
    dispositivo no tienen cascada).
  - Queda SOLO eliminacion_auditoria: fecha, HMAC del id fiscal, conteos por
    tabla y canal. Sin nombres ni datos de pagos.
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
import zipfile
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.admin.models import (
    CodigoInvitacion, ConfigImpresion, CuentaBancaria, Empresa, Local, MetodoPago, Persona,
    PushSuscripcion, SesionAdmin, VendedorLocal,
)
from app.api.models_cuenta import CuentaApi
from app.api.models_device import DeviceEstadoActual, DeviceEstadoHistorico
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook
from app.api.models_internos import PersonaCuenta, TitularConfirmadoExterno
from app.api.models_multiempresa import (
    CodigoVinculacion, Invitacion, Membresia, Otp, Sesion, Usuario,
)
from app.api.models_pago_uso import PagoUso
from app.api.models_privacidad import EliminacionAuditoria, SolicitudEliminacion
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado
from app.services import cuenta_empresa
from app.services.eventos import a_iso_lima, canal_de_metodo, institucion_de
from app.services.secretos import hash_codigo

FRASE_CONFIRMACION = "Sí, quiero llevar mis datos y eliminarlos de PagoOK"
CANALES = ("autoservicio", "solicitud")


class EliminacionProhibida(Exception):
    """perusistemas o empresa de demo (-> 403)."""


def _slugs_demo() -> set[str]:
    from app.api.router_demo import slugs_demo  # import local: evita ciclo
    return slugs_demo()


def eliminacion_permitida(empresa: Empresa) -> bool:
    """Nunca perusistemas, ni las de SLUGS_DEMO, ni la demo del revisor de Play."""
    import os
    slug = (empresa.slug or "").lower()
    revision = os.getenv("EMPRESA_REVISION_PLAY", "demo-revision").strip().lower()
    return not (cuenta_empresa.es_principal(empresa) or slug in _slugs_demo() or slug == revision)


def hash_id_fiscal(pais: str, id_fiscal: str) -> str:
    return hash_codigo(f"{(pais or '').upper()}|{(id_fiscal or '').strip()}", "auditoria")


# =============================================================
# DESCARGA (ZIP con JSON + CSV)
# =============================================================

# Registro en memoria de quién descargó (habilita "Eliminar" en el panel).
_descargas: dict[int, float] = {}
_lock_descargas = threading.Lock()
VIGENCIA_DESCARGA_SEG = 2 * 3600


def marcar_descarga(empresa_id: int) -> None:
    with _lock_descargas:
        _descargas[empresa_id] = time.time()


def descargo_recientemente(empresa_id: int) -> bool:
    with _lock_descargas:
        t = _descargas.get(empresa_id)
    return t is not None and time.time() - t < VIGENCIA_DESCARGA_SEG


def _fmt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return a_iso_lima(v)
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    return v


def datos_exportables(db: Session, empresa: Empresa) -> dict[str, list[dict]]:
    """Todo lo de la empresa, SIN secretos (ni tokens, ni hashes de API keys,
    ni secretos de webhook, ni instalacion_id, que sirve para recuperar el token)."""
    e = empresa
    pagos = (
        db.query(PagoDetectado).filter(PagoDetectado.empresa_id == e.id)
        .order_by(PagoDetectado.recibido_en).all()
    )
    usos = {u.pago_id: u for u in db.query(PagoUso).filter(PagoUso.empresa_id == e.id).all()}

    def _pago(p):
        u = usos.get(p.id)
        return {
            "id": p.id, "fecha_hora": _fmt(p.recibido_en), "canal": canal_de_metodo(p.metodo),
            "institucion": institucion_de(p), "contraparte": p.titular or "", "monto": _fmt(Decimal(str(p.monto))),
            "moneda": p.moneda or "PEN", "codigo_operacion": p.codigo_operacion,
            "tipo_egreso": p.tipo_egreso, "tarjeta_ultimos4": p.tarjeta_ultimos4,
            "cuenta_propia": p.confirmacion_usuario == "confirmado_interno",
            "usado_para": u.referencia_externa if u else None, "usado_por": u.sistema_que_uso if u else None,
        }

    miembros = (
        db.query(Membresia, Usuario).join(Usuario, Usuario.id == Membresia.usuario_id)
        .filter(Membresia.empresa_id == e.id).order_by(Membresia.id).all()
    )
    return {
        "empresa": [{
            "razon_social": e.razon_social, "nombre_comercial": e.nombre_comercial, "pais": e.pais_codigo,
            "id_fiscal_tipo": e.id_fiscal_tipo, "id_fiscal": e.id_fiscal, "slug": e.slug,
            "estado_cuenta": e.estado_cuenta, "creada_en": _fmt(e.creada_en),
        }],
        "dispositivos": [{
            "id": d.id, "nombre": d.nombre, "fabricante": d.manufacturer, "modelo": d.modelo,
            "android": d.android_version, "app": d.app_version, "activo": bool(d.activo),
            "creado_en": _fmt(d.creado_en), "ultimo_ping": _fmt(d.ultimo_ping),
            "consentimiento_version": d.consentimiento_version,
            "consentimiento_fecha": _fmt(d.consentimiento_fecha), "pais": d.pais,
            "bancos_declarados": ", ".join(d.bancos_declarados or []), "nivel_equipo": d.nivel_equipo,
            "egresos_activo": d.egresos_activo,
        } for d in db.query(Dispositivo).filter(Dispositivo.empresa_id == e.id).order_by(Dispositivo.id)],
        "ingresos": [_pago(p) for p in pagos if p.tipo != "egreso"],
        "egresos": [_pago(p) for p in pagos if p.tipo == "egreso"],
        "equipo": [{
            "nombre": u.nombre, "celular": u.celular, "rol": m.rol, "activo": bool(m.activo),
            "desde": _fmt(m.creado_en),
        } for m, u in miembros],
        "credenciales_api": [{
            "nombre": c.nombre, "creada_en": _fmt(c.creada_en), "activa": bool(c.activa),
        } for c in db.query(CuentaApi).filter(CuentaApi.empresa_id == e.id).order_by(CuentaApi.id)],
    }


def exportar_zip(db: Session, empresa: Empresa) -> bytes:
    datos = datos_exportables(db, empresa)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("datos.json", json.dumps(datos, ensure_ascii=False, indent=2, default=str))
        for tabla, filas in datos.items():
            s = io.StringIO()
            if filas:
                w = csv.DictWriter(s, fieldnames=list(filas[0].keys()))
                w.writeheader()
                w.writerows(filas)
            z.writestr(f"{tabla}.csv", "\ufeff" + s.getvalue())
        z.writestr("LEEME.txt", (
            f"Datos de {empresa.razon_social} en PagoOK, descargados el "
            f"{a_iso_lima(datetime.now(timezone.utc))}.\n\n"
            "datos.json tiene todo; cada .csv es una tabla (se abre en Excel).\n"
            "Por seguridad NO se incluyen tokens de dispositivos, claves API ni secretos de webhooks.\n"
        ))
    return buf.getvalue()


# =============================================================
# ELIMINACIÓN
# =============================================================

def _borrar(q) -> int:
    return q.delete(synchronize_session=False)


def _borrar_tabla_sin_modelo(db: Session, tabla: str, empresa_id: int) -> int:
    """Tablas antiguas sin modelo ORM (ej. empresa_tributo)."""
    if not inspect(db.connection()).has_table(tabla):  # misma conexión: no corta la transacción
        return 0
    return db.execute(text(f"DELETE FROM {tabla} WHERE empresa_id = :e"), {"e": empresa_id}).rowcount


def eliminar_empresa(db: Session, empresa: Empresa, canal: str,
                     solicitud: SolicitudEliminacion | None = None) -> dict[str, int]:
    """Borra TODO lo de la empresa y deja la auditoría mínima. NO hace commit."""
    if canal not in CANALES:
        raise ValueError(f"canal inválido: {canal}")
    if not eliminacion_permitida(empresa):
        raise EliminacionProhibida(f"La empresa {empresa.slug} no se puede eliminar (demo o principal).")

    eid = empresa.id
    c: dict[str, int] = {}
    disp_ids = [d for (d,) in db.query(Dispositivo.id).filter(Dispositivo.empresa_id == eid)]
    susc_ids = [s for (s,) in db.query(SuscripcionWebhook.id).filter(SuscripcionWebhook.empresa_id == eid)]
    local_ids = [x for (x,) in db.query(Local.id).filter(Local.empresa_id == eid)]
    usuario_ids = {u for (u,) in db.query(Membresia.usuario_id).filter(Membresia.empresa_id == eid)}
    persona_ids = {p for (p,) in db.query(VendedorLocal.persona_id).filter(VendedorLocal.local_id.in_(local_ids))
                   if p} if local_ids else set()
    if empresa.duenio_id:
        persona_ids.add(empresa.duenio_id)
    id_fiscal_hash = hash_id_fiscal(empresa.pais_codigo, empresa.id_fiscal)

    # 1) Correos del banco (antes que los pagos: correo_raw.pago_id los referencia)
    from app.api.models_correo import CorreoEmpresa, CorreoRaw, SolicitudBanco
    c["correo_raw"] = _borrar(db.query(CorreoRaw).filter(CorreoRaw.empresa_id == eid))
    c["correo_empresa"] = _borrar(db.query(CorreoEmpresa).filter(CorreoEmpresa.empresa_id == eid))
    # Los pedidos de bancos se conservan (son estadística), sin el vínculo a la empresa.
    db.query(SolicitudBanco).filter(SolicitudBanco.empresa_id == eid).update(
        {SolicitudBanco.empresa_id: None, SolicitudBanco.dispositivo_id: None}, synchronize_session=False)
    # 2) Pagos y su uso, cola de webhooks
    c["pago_uso"] = _borrar(db.query(PagoUso).filter(PagoUso.empresa_id == eid))
    c["entrega_evento"] = _borrar(db.query(EntregaEvento).filter(EntregaEvento.suscripcion_id.in_(susc_ids))) if susc_ids else 0
    c["suscripcion_webhook"] = _borrar(db.query(SuscripcionWebhook).filter(SuscripcionWebhook.empresa_id == eid))
    c["pago_detectado"] = _borrar(db.query(PagoDetectado).filter(PagoDetectado.empresa_id == eid))
    c["notificacion_raw"] = _borrar(db.query(NotificacionRaw).filter(NotificacionRaw.empresa_id == eid))
    # 2) Dispositivos (sus tokens dejan de existir) y su telemetría
    c["codigo_vinculacion"] = _borrar(db.query(CodigoVinculacion).filter(CodigoVinculacion.empresa_id == eid))
    if disp_ids:
        c["device_estado_actual"] = _borrar(db.query(DeviceEstadoActual).filter(DeviceEstadoActual.dispositivo_id.in_(disp_ids)))
        c["device_estado_historico"] = _borrar(db.query(DeviceEstadoHistorico).filter(DeviceEstadoHistorico.dispositivo_id.in_(disp_ids)))
    c["dispositivo"] = _borrar(db.query(Dispositivo).filter(Dispositivo.empresa_id == eid))
    # 3) Credenciales, equipo y sesiones
    c["cuenta_api"] = _borrar(db.query(CuentaApi).filter(CuentaApi.empresa_id == eid))
    c["invitacion"] = _borrar(db.query(Invitacion).filter(Invitacion.empresa_id == eid))
    c["sesion"] = _borrar(db.query(Sesion).filter(Sesion.empresa_id == eid))
    c["membresia"] = _borrar(db.query(Membresia).filter(Membresia.empresa_id == eid))
    # 4) Receptores push, cuentas propias, titulares
    c["push_suscripciones"] = _borrar(db.query(PushSuscripcion).filter(PushSuscripcion.empresa_id == eid))
    c["codigos_invitacion"] = _borrar(db.query(CodigoInvitacion).filter(CodigoInvitacion.empresa_id == eid))
    c["persona_cuenta"] = _borrar(db.query(PersonaCuenta).filter(PersonaCuenta.empresa_id == eid))
    c["titular_confirmado_externo"] = _borrar(db.query(TitularConfirmadoExterno).filter(TitularConfirmadoExterno.empresa_id == eid))
    # 5) Configuración del panel antiguo (/admin)
    c["vendedor_local"] = _borrar(db.query(VendedorLocal).filter(VendedorLocal.local_id.in_(local_ids))) if local_ids else 0
    c["local"] = _borrar(db.query(Local).filter(Local.empresa_id == eid))
    c["metodo_pago"] = _borrar(db.query(MetodoPago).filter(MetodoPago.empresa_id == eid))
    c["cuenta_bancaria"] = _borrar(db.query(CuentaBancaria).filter(CuentaBancaria.empresa_id == eid))
    c["config_impresion"] = _borrar(db.query(ConfigImpresion).filter(ConfigImpresion.empresa_id == eid))
    c["empresa_tributo"] = _borrar_tabla_sin_modelo(db, "empresa_tributo", eid)
    c["sesion_admin"] = _borrar(db.query(SesionAdmin).filter(SesionAdmin.empresa_id == eid))
    # 6) La empresa
    c["empresa"] = _borrar(db.query(Empresa).filter(Empresa.id == eid))

    # 7) Personas que se quedaron sin ninguna empresa
    huerfanos = [u for u in usuario_ids
                 if db.query(Membresia).filter(Membresia.usuario_id == u).count() == 0]
    celulares = [cel for (cel,) in db.query(Usuario.celular).filter(Usuario.id.in_(huerfanos))] if huerfanos else []
    c["otp"] = _borrar(db.query(Otp).filter(Otp.celular.in_(celulares))) if celulares else 0
    c["usuario"] = _borrar(db.query(Usuario).filter(Usuario.id.in_(huerfanos))) if huerfanos else 0
    personas_huerfanas = [
        p for p in persona_ids
        if db.query(Empresa).filter(Empresa.duenio_id == p).count() == 0
        and db.query(VendedorLocal).filter(VendedorLocal.persona_id == p).count() == 0
    ]
    if personas_huerfanas:
        _borrar(db.query(SesionAdmin).filter(SesionAdmin.persona_id.in_(personas_huerfanas)))
        _borrar(db.query(PersonaCuenta).filter(PersonaCuenta.persona_id.in_(personas_huerfanas)))
        c["persona"] = _borrar(db.query(Persona).filter(Persona.id.in_(personas_huerfanas)))
    else:
        c["persona"] = 0

    # 8) La solicitud se cierra sin datos personales; solo queda la auditoría.
    if solicitud is not None:
        solicitud.estado = "ejecutada"
        solicitud.atendida_en = datetime.now(timezone.utc)
        solicitud.nombre = solicitud.contacto = solicitud.motivo = None
        solicitud.id_fiscal = "[eliminado]"
    conteos = {k: int(v or 0) for k, v in c.items()}
    db.add(EliminacionAuditoria(id_fiscal_hash=id_fiscal_hash, conteos=conteos, canal=canal))
    db.flush()
    with _lock_descargas:
        _descargas.pop(eid, None)
    return conteos
